from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloud.preparation import (FACTS, MASTER, digest, grade_job, prepare_packet,
                               screening_answers, select_arc, tailor_source, verify_resume, requirements_from_jd)
from scripts.jobscout_feed import candidates, feed_metadata


def question(label, name, *, required=True, kind='input_text', values=None):
    return {'label': label, 'required': required, 'fields': [{'name': name, 'type': kind, 'values': values or []}]}


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.job = {'jd': 'Requirements\nSQL and Python\nCI/CD and stakeholder requirements', 'title': 'Data Engineer',
                    'location': 'Dallas, TX', 'questions': [question('First Name', 'first_name')]}

    def test_truthful_fit_is_not_feed_score(self):
        result = grade_job(self.job)
        self.assertTrue(result['qualified'])
        self.assertEqual(result['coverage'], 1)
        bad = grade_job({**self.job, 'jd': 'Requirements\nProduction Snowflake and AWS\n7+ years data pipelines'})
        self.assertFalse(bad['qualified'])
        self.assertIn('experience_tenure', bad['requirements'][1]['gaps'])

    def test_independent_ai_evidence_does_not_become_production_experience(self):
        result = grade_job({**self.job, 'jd': 'Requirements\nProduction PyTorch model deployment'})
        self.assertFalse(result['qualified'])
        self.assertIn('production_pytorch', result['requirements'][0]['gaps'])

    def test_unknown_requirement_section_is_explicit_gap(self):
        self.assertEqual(grade_job({**self.job, 'jd': 'A great team with Python.'})['reason'], 'requirement_map_unavailable')

    def test_real_posting_heading_shapes_preserve_explicit_requirement_boundaries(self):
        for heading in ("Requirements:", "Qualifications:", "You may be a good fit if you:", "What we're looking for:", "Who you are:"):
            requirements = requirements_from_jd(heading + '\nSQL and Python\nCI/CD\nPreferred qualifications:\nProduction AWS\nBenefits:\nUnlimited leave')
            self.assertEqual(requirements, ['SQL and Python', 'CI/CD'])

    def test_ineligible_country_and_sponsorship_prohibition(self):
        self.assertFalse(grade_job({**self.job, 'location': 'London, England'})['qualified'])
        self.assertFalse(grade_job({**self.job, 'jd': self.job['jd']+'\nCannot sponsor visas'})['qualified'])

    def test_arc_uses_jd_language(self):
        self.assertEqual(select_arc('On-call reliability, incidents and recovery.'), 'A')
        self.assertEqual(select_arc('LLM serving and RAG model deployment.'), 'E')

    def test_standing_authorization_and_sponsorship_answers_have_evidence(self):
        result = screening_answers([question('Are you legally authorized to work in the United States?', 'auth'),
                                    question('Will you now or in the future require sponsorship?', 'sponsor')])
        self.assertEqual(result['fields'], {'auth': 'Yes', 'sponsor': 'Yes'})
        self.assertEqual(result['missing'], [])
        self.assertTrue(all(answer['source_hash'] and answer['question_hash'] for answer in result['evidence']))

    def test_salary_eeo_hidden_fields_and_attestations_are_not_guessed(self):
        result = screening_answers([question('Expected salary?', 'salary'), question('Veteran status', 'veteran'),
                                    question('Latitude', 'latitude', kind='input_hidden'), question('I certify accuracy', 'certify')])
        self.assertEqual(result['fields'], {})
        self.assertEqual(len(result['missing']), 4)

    def test_optional_unknown_sensitive_field_is_omitted(self):
        result = screening_answers([question('Veteran status', 'veteran', required=False)])
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['fields'], {})

    def test_select_answer_uses_exact_option_value(self):
        q = question('Will you require sponsorship?', 'sponsor', kind='multi_value_single_select',
                     values=[{'label': 'No', 'value': 0}, {'label': 'Yes', 'value': 1}])
        self.assertEqual(screening_answers([q])['fields'], {'sponsor': 1})
        q['fields'][0]['values'] = [{'label': 'No', 'value': 0}]
        self.assertEqual(len(screening_answers([q])['missing']), 1)

    def test_verified_override_is_locked_to_exact_live_question(self):
        q = question('Expected salary?', 'salary')
        config = {'verified_answers': {'salary': {'verified': True, 'answer': '190000', 'source': 'user:exact-answer', 'question_hash': digest(q)}}}
        self.assertEqual(screening_answers([q], config)['fields']['salary'], '190000')
        self.assertEqual(screening_answers([{**q, 'label': 'Minimum salary commitment?'}], config)['fields'], {})

    def test_master_copy_preserves_titles_and_independent_project_context(self):
        original = MASTER.read_text()
        grade = grade_job(self.job)
        source, claims, changes = tailor_source(original, grade)
        self.assertEqual(MASTER.read_text(), original)
        self.assertIn('Portfolio Risk Analytics (in progress)', source)
        self.assertIn('independ', __import__('cloud.preparation', fromlist=['SUMMARIES']).SUMMARIES['E'])
        self.assertIn('\\roleheader{Business Analyst}{Zomato}', source)
        self.assertEqual(len(claims), 23)
        self.assertTrue(changes)
        self.assertNotIn('400 databases', source)
        self.assertNotIn('47.8 TPS', source)

    def test_changed_source_cannot_inherit_claim_manifest(self):
        with self.assertRaises(ValueError):
            tailor_source(MASTER.read_text().replace('5,000 engineering hours', '50,000 engineering hours'), grade_job(self.job))

    def test_missing_compiler_is_not_a_verified_resume(self):
        with patch('cloud.preparation.shutil.which', return_value=None):
            packet = prepare_packet(self.job, grade_job(self.job))
        self.assertFalse(packet['ready'])
        self.assertIn('tex_compiler_unavailable', packet['qa']['reasons'])
        self.assertTrue((Path(packet['artifact_dir'])/'changelog.md').is_file())
        self.assertTrue((Path(packet['artifact_dir'])/'resume.tex').is_file())

    def test_strict_source_qa_rejects_bad_summary_or_context(self):
        with tempfile.TemporaryDirectory() as directory:
            tex, pdf = Path(directory)/'resume.tex', Path(directory)/'resume.pdf'
            tex.write_text('\\summary{'+('x'*300)+'}\n\\rolecontext{short}\n\\item '+('x'*110)+'\n')
            result = verify_resume(tex, pdf)
            self.assertFalse(result['verified'])
            self.assertFalse(result['checks']['source_budgets'])

    def test_first_seen_alone_does_not_prove_posting_freshness(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        data = {'jobs': [{'is_active': True, 'relevance_score': .9, 'first_seen_at': now.isoformat()}]}
        self.assertEqual(candidates(data, now=now), [])
        self.assertTrue(feed_metadata({})['stale'])


if __name__ == '__main__':
    unittest.main()
