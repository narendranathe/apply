import base64
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloud.applications import (StateConflict, application_key, canonical_url, fold_applications,
                                matching_application, run_applications, transition, daily_attempt_count)
from cloud.notion import AmbiguousWriteError
from cloud.orchestrator import ShadowStore
from cloud.preparation import digest
from cloud.records import make_record
from cloud.submission import GreenhouseAdapter, LeverAdapter, Response, SubmissionBlocked
from scripts.registry import main as registry_main


class ATSTransport:
    def __init__(self, *, uncertain=False, change_form=False, greenhouse=False):
        self.calls = []
        self.uncertain, self.change_form, self.greenhouse = uncertain, change_form, greenhouse
        self.form_reads = 0

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append((method, url, body))
        if method == 'POST':
            if self.uncertain:
                raise TimeoutError('sandbox uncertainty')
            return Response(200, {'ok': True, 'applicationId': 'sandbox-receipt'})
        if 'boards-api.greenhouse.io' in url:
            return Response(200, {'id': 100, 'company_name': 'Acme', 'title': 'Data Engineer',
                'requisition_id': 'req-1', 'content': '<h3>Requirements</h3><li>SQL and Python</li><li>CI/CD and stakeholder requirements</li>',
                'location': {'name': 'Dallas, TX'}, 'absolute_url': 'https://job-boards.greenhouse.io/acme/jobs/100',
                'questions': [{'label': 'Name', 'required': True, 'fields': [{'name': 'first_name', 'type': 'input_text'}]}]})
        if '/v1/' in url:
            self.form_reads += 1
            personal = [{'name': 'name', 'required': True}, {'name': 'email', 'required': True},
                        {'name': 'resume', 'required': True}]
            if self.change_form and self.form_reads > 1:
                personal.append({'name': 'phone', 'required': True})
            return Response(200, {'data': {'id': 'req-1', 'personalInformation': personal,
                                         'customQuestions': [], 'eeoQuestions': {}, 'urls': []}})
        return Response(200, {'id': 'req-1', 'text': 'Data Engineer', 'categories': {'location': 'Dallas, TX'},
                'description': '<h3>Requirements</h3><li>SQL and Python</li><li>CI/CD and stakeholder requirements</li>',
                'lists': [], 'salaryRange': {'min': 175000, 'max': 210000, 'currency': 'USD', 'interval': 'per-year-salary'},
                'hostedUrl': 'https://jobs.lever.co/acme/req-1'})


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.config = {'applications': {'max_candidates': 5, 'max_submissions_per_day': 3, 'employers': {'acme': {
            'ats': 'lever', 'board_token': 'acme', 'credential_env': 'TEST_APPLICATION_TOKEN',
            'schema_credential_env': 'TEST_SCHEMA_TOKEN',
            'screening': {'sponsorship_evidence': {'verified': True, 'recent_h1b_filings': True,
                'source_url': 'https://acme.test/sandbox-filings', 'verified_at': now.isoformat(),
                'fiscal_years': [now.year - 1, now.year]}},
            'employer_verification': {'company': 'Acme', 'evidence_url': 'https://acme.test/careers', 'verified_at': now.isoformat()},
            'permission': {'permitted': True, 'scope': 'application_submission', 'evidence_url': 'https://acme.test/permission',
                           'verified_at': (now-timedelta(days=1)).isoformat(), 'expires_at': (now+timedelta(days=1)).isoformat()}}}}}
        self.feed = {'exported_at': now.isoformat(), 'jobs': [{'title': 'Data Engineer', 'company': 'Acme',
            'url': 'https://jobs.lever.co/acme/req-1?utm_source=feed', 'ats': 'lever', 'location': 'Dallas, TX',
            'relevance_score': .6, 'posted_at': now.isoformat(), 'is_active': True}]}
        self.env = patch.dict('os.environ', {'TEST_APPLICATION_TOKEN': 'sandbox', 'TEST_SCHEMA_TOKEN': 'sandbox'})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        (self.path/'resume.tex').write_text('sandbox source\n')
        (self.path/'resume.pdf').write_bytes(b'%PDF-1.4\nsandbox fixture only\n')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def prepare(self, job, grade, config):
        fields = {'name': 'Sandbox Applicant', 'email': 'sandbox@example.test'}
        return {'ready': True, 'reason': 'sandbox_fixture', 'artifact_dir': str(self.path),
                'resume_path': str(self.path/'resume.pdf'), 'resume_sha256': digest((self.path/'resume.pdf').read_bytes()),
                'source_sha256': digest((self.path/'resume.tex').read_bytes()), 'ground_truth_sha256': digest('sandbox-facts'),
                'answers_hash': digest(fields), 'fields': fields, 'qa': {'verified': True},
                'answer_evidence': [], 'claims': [], 'changes': [], 'missing': [], 'arc': 'C'}

    def run_fixture(self, store=None, transport=None, dry_run=False, feed=None, prepare=None):
        return run_applications(store or ShadowStore(), self.config, dry_run,
                                feed=feed or self.feed, transport=transport or ATSTransport(), prepare=prepare or self.prepare)

    def test_company_requisition_and_url_alias_identity(self):
        self.assertEqual(application_key('Acme, Inc.', 'REQ-1'), 'acmeinc:req1')
        self.assertEqual(canonical_url('https://job-boards.greenhouse.io/acme/jobs/100?gh_src=campaign'),
                         canonical_url('https://boards.greenhouse.io/acme/jobs/100'))
        self.assertNotEqual(canonical_url('https://acme.test/careers?gh_jid=1'), canonical_url('https://acme.test/careers?gh_jid=2'))

    def test_daily_pacing_uses_one_chicago_day_across_utc_midnight(self):
        records = {
            'earlier': {'payload': {'attempt': {'started_at': '2026-10-11T23:30:00Z'}}, 'status': 'applied_verified'},
            'later': {'payload': {'attempt': {'started_at': '2026-10-12T00:30:00Z'}}, 'status': 'submission_unknown'},
            'prior_day': {'payload': {'attempt': {'started_at': '2026-10-11T04:30:00Z'}}, 'status': 'applied_verified'},
        }
        self.assertEqual(daily_attempt_count(records, datetime(2026, 10, 12, 1, tzinfo=timezone.utc)), 2)
        self.assertEqual(daily_attempt_count(records, datetime(2026, 10, 12, 6, tzinfo=timezone.utc)), 0)

    def test_daily_pacing_handles_chicago_winter_boundary_and_unknown_dates(self):
        records = {'attempt': {'payload': {'attempt': {'started_at': '2026-11-03T05:30:00Z'}}, 'status': 'submission_unknown'}}
        self.assertEqual(daily_attempt_count(records, datetime(2026, 11, 3, 5, 45, tzinfo=timezone.utc)), 1)
        self.assertEqual(daily_attempt_count(records, datetime(2026, 11, 3, 6, 15, tzinfo=timezone.utc)), 0)
        records['attempt']['payload']['attempt']['started_at'] = 'unknown'
        with self.assertRaises(StateConflict):
            daily_attempt_count(records, datetime(2026, 11, 3, 6, tzinfo=timezone.utc))

    def test_renamed_role_cannot_evade_unknown_employer_application_history(self):
        for status in ('applied', 'submission_unknown', 'applied_verified'):
            old = {'payload': {'company': 'Acme', 'role': 'Data Engineer', 'url': 'https://acme.test/old'}, 'status': status}
            self.assertIs(matching_application({'legacy': old}, 'Acme', 'new-req', ['https://acme.test/new'], 'AI Platform Engineer'), old)

    def test_verified_distinct_requisition_is_allowed(self):
        old = {'payload': {'company': 'Acme', 'role': 'Data Engineer', 'requisition_id': 'req-1'}, 'status': 'applied_verified'}
        self.assertIsNone(matching_application({'known': old}, 'Acme', 'req-2', ['https://acme.test/new'], 'AI Platform Engineer'))
        self.assertIs(matching_application({'known': old}, 'Acme', 'req-1', ['https://acme.test/alias'], 'Renamed Role'), old)

    def test_happy_path_requires_receipt_and_no_manual_approval_state(self):
        store, transport = ShadowStore(), ATSTransport()
        summary = self.run_fixture(store, transport)
        self.assertEqual(summary['applied_verified'], 1)
        self.assertEqual(summary['submission_attempts'], 1)
        self.assertEqual([r['status'] for r in store.query('applications')],
                         ['discovered', 'qualified', 'prepared', 'submission_eligible', 'submitting', 'applied_verified'])
        packet = store.query('application_packets')[0]['payload']
        self.assertEqual(base64.b64decode(packet['resume_pdf_base64']), (self.path/'resume.pdf').read_bytes())
        output = self.path/'restore'
        identity = next(iter(fold_applications(store.query('applications'))))
        self.assertEqual(registry_main(['packet', '--application-id', identity, '--output', str(output)], store=store), 0)
        self.assertEqual((output/'resume.pdf').read_bytes(), (self.path/'resume.pdf').read_bytes())

    def test_dry_run_has_no_state_or_external_mutation(self):
        store, transport = ShadowStore(), ATSTransport()
        summary = self.run_fixture(store, transport, True)
        self.assertEqual(store.records, {})
        self.assertEqual(summary['statuses'], {'submission_eligible': 1})
        self.assertFalse(any(call[0] == 'POST' for call in transport.calls))

    def test_unknown_response_never_retried(self):
        store, transport = ShadowStore(), ATSTransport(uncertain=True)
        first = self.run_fixture(store, transport)
        second = self.run_fixture(store, transport)
        self.assertEqual(first['submission_unknown'], 1)
        self.assertEqual(first['applied_verified'], 0)
        self.assertEqual(second['duplicates'], 1)
        self.assertEqual(sum(call[0] == 'POST' for call in transport.calls), 1)

    def test_confirmed_duplicate_never_reapplied(self):
        store, transport = ShadowStore(), ATSTransport()
        self.run_fixture(store, transport)
        self.assertEqual(self.run_fixture(store, transport)['duplicates'], 1)
        self.assertEqual(sum(call[0] == 'POST' for call in transport.calls), 1)

    def test_historical_applied_suppresses_even_inactive_feed_job(self):
        feed = copy.deepcopy(self.feed)
        old = {**feed['jobs'][0], 'application_status': 'applied', 'is_active': False, 'relevance_score': 0}
        feed['jobs'].insert(0, old)
        store, transport = ShadowStore(), ATSTransport()
        summary = self.run_fixture(store, transport, feed=feed)
        self.assertEqual(summary['duplicates'], 1)
        self.assertEqual(next(iter(fold_applications(store.query('applications')).values()))['status'], 'submission_unknown')
        self.assertFalse(any(call[0] == 'POST' for call in transport.calls))

    def test_legacy_employer_application_event_suppresses_unknown_requisition(self):
        store, transport = ShadowStore(), ATSTransport()
        store.put(make_record('outreach_events', {'kind': 'Application', 'company': 'Acme', 'delivery_state': 'recorded'}, 'old-application'))
        summary = self.run_fixture(store, transport)
        self.assertEqual(summary['duplicates'], 1)
        self.assertEqual(summary['submission_attempts'], 0)

    def test_unresolved_application_event_stops_without_guessing_from_prose(self):
        store = ShadowStore()
        store.put(make_record('outreach_events', {'kind': 'Application', 'content': 'unstructured history'}, 'unknown-application'))
        summary = self.run_fixture(store)
        self.assertEqual(summary['exceptions'], {'unresolved_historical_application_identity': 1})
        self.assertEqual(summary['submission_attempts'], 0)

    def test_form_change_at_action_time_blocks(self):
        summary = self.run_fixture(transport=ATSTransport(change_form=True))
        self.assertEqual(summary['submission_attempts'], 0)
        self.assertEqual(summary['exceptions'], {'official_post_changed_at_action_time': 1})

    def test_packet_tampering_blocks(self):
        def tampered(job, grade, config):
            packet = self.prepare(job, grade, config)
            packet['answers_hash'] = 'incorrect'
            return packet
        summary = self.run_fixture(prepare=tampered)
        self.assertEqual(summary['submission_attempts'], 0)
        self.assertIn('packet_changed_at_action_time', summary['exceptions'])

    def test_daily_pacing_counts_unknown_attempts(self):
        self.config['applications']['max_submissions_per_day'] = 0
        summary = self.run_fixture()
        self.assertEqual(summary['submission_attempts'], 0)
        self.assertEqual(summary['exceptions'], {'daily_application_pacing_reached': 1})

    def test_unknown_sponsorship_evidence_and_compensation_never_inferred(self):
        self.config['applications']['employers']['acme']['screening'] = {}
        summary = self.run_fixture()
        self.assertEqual(summary['submission_attempts'], 0)
        self.assertEqual(summary['exceptions'], {'recent_sponsorship_evidence_missing': 1})

    def test_missing_answer_is_durable_exception_and_other_candidate_continues(self):
        feed = copy.deepcopy(self.feed)
        feed['jobs'].append({**feed['jobs'][0], 'company': 'Other', 'url': 'https://other.test/jobs/1'})
        def missing(job, grade, config):
            packet = self.prepare(job, grade, config)
            packet.update(ready=False, missing=[{'question': 'Salary?', 'reason': 'unknown'}])
            return packet
        store = ShadowStore()
        summary = self.run_fixture(store, feed=feed, prepare=missing)
        self.assertEqual(summary['candidates_seen'], 2)
        self.assertEqual(summary['exceptions']['verified_answer_unavailable'], 1)
        self.assertIn('needs_user_fact', summary['statuses'])
        self.assertEqual(len(store.query('application_packets')), 1)

    def test_ambiguous_store_write_stops_all_candidates(self):
        class FailingStore(ShadowStore):
            def put(self, record):
                if record['status'] == 'prepared':
                    raise AmbiguousWriteError('sandbox unknown write')
                return super().put(record)
        transport = ATSTransport()
        with self.assertRaises(AmbiguousWriteError):
            self.run_fixture(FailingStore(), transport)
        self.assertEqual(sum(call[0] == 'POST' for call in transport.calls), 0)

    def test_receipt_write_failure_leaves_submitting_and_no_second_effect(self):
        class FailingStore(ShadowStore):
            def put(self, record):
                if record['status'] == 'applied_verified':
                    raise AmbiguousWriteError('sandbox unknown receipt write')
                return super().put(record)
        store, transport = FailingStore(), ATSTransport()
        with self.assertRaises(AmbiguousWriteError):
            self.run_fixture(store, transport)
        self.assertEqual(next(iter(fold_applications(store.query('applications')).values()))['status'], 'submitting')
        self.run_fixture(store, transport)
        self.assertEqual(sum(call[0] == 'POST' for call in transport.calls), 1)

    def test_manual_conflict_and_invalid_transition_stop(self):
        store = ShadowStore()
        current = transition(store, 'app', 'discovered', {'company': 'Acme'})
        with self.assertRaises(StateConflict):
            transition(store, 'app', 'applied_verified', expected=current)
        with self.assertRaises(StateConflict):
            transition(store, 'app', 'qualified', expected=None)

    def test_greenhouse_generic_success_is_not_receipt(self):
        employer = self.config['applications']['employers']['acme']
        adapter = GreenhouseAdapter({**employer, 'ats': 'greenhouse'}, ATSTransport())
        result = adapter.submit({'endpoint': 'https://boards-api.greenhouse.io/v1/boards/acme/jobs/100'}, self.prepare({}, {}, {}))
        self.assertEqual(result['status'], 'submission_unknown')

    def test_greenhouse_nullable_optional_schema_is_valid_and_ambiguous_required_schema_blocks(self):
        class NullableTransport(ATSTransport):
            bad = False
            def request(self, *args, **kwargs):
                response = super().request(*args, **kwargs)
                response.data.update(demographic_questions=None, location_questions=None,
                                     compliance=None, data_compliance=None, pay_input_ranges=None)
                if self.bad:
                    response.data['questions'] = None
                return response
        transport = NullableTransport()
        adapter = GreenhouseAdapter({'ats': 'greenhouse', 'board_token': 'acme'}, transport)
        lead = {'company': 'Acme', 'url': 'https://job-boards.greenhouse.io/acme/jobs/100'}
        self.assertTrue(adapter.verify(lead)['open'])
        transport.bad = True
        with self.assertRaises(SubmissionBlocked):
            adapter.verify(lead)
        research = adapter.verify(lead, require_schema=False)
        self.assertTrue(research['open'])
        self.assertFalse(research['schema_verified'])

    def test_lever_public_research_does_not_require_submission_credentials(self):
        transport = ATSTransport()
        adapter = LeverAdapter({'board_token': 'acme'}, transport)
        lead = {'company': 'Acme', 'url': 'https://jobs.lever.co/acme/req-1'}
        research = adapter.verify(lead, require_schema=False)
        self.assertTrue(research['open'])
        self.assertFalse(research['employer_identity_verified'])
        self.assertFalse(research['schema_verified'])
        self.assertEqual(len(transport.calls), 1)
        with self.assertRaises(SubmissionBlocked):
            adapter.verify(lead)

    def test_permission_required_even_with_credentials(self):
        self.config['applications']['employers']['acme']['permission']['permitted'] = False
        transport = ATSTransport()
        summary = self.run_fixture(transport=transport)
        self.assertEqual(summary['submission_attempts'], 0)
        self.assertFalse(any(call[0] == 'POST' for call in transport.calls))

    def test_missing_feed_timestamp_fails_closed(self):
        summary = self.run_fixture(feed={'jobs': self.feed['jobs']})
        self.assertEqual(summary['exceptions'], {'feed_stale_or_timestamp_unknown': 1})

    def test_retired_cli_mutation_cannot_compete_with_cloud(self):
        self.assertEqual(registry_main(['claim', '--key', 'new'], store=ShadowStore()), 3)


if __name__ == '__main__':
    unittest.main()
