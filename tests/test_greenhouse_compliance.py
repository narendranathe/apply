"""Synthetic official-form shapes; no applicant, provider or storage writes."""
import copy
import tempfile
from pathlib import Path
import unittest

from cloud.preparation import digest, screening_answers
from cloud.submission import GreenhouseAdapter, Response, SubmissionBlocked


def question(name="eeo_choice", *, required=False):
    return {"label": "Voluntary selection", "required": required,
            "fields": [{"name": name, "type": "multi_value_single_select",
                        "values": [{"label": "A", "value": "a"}]}]}


def official_body():
    # All five real failing responses had these section wrappers, rather than
    # a flat compliance question. This fixture contains no real role or person.
    return {"id": 100, "company_name": "Acme", "title": "Data Engineer",
            "requisition_id": "REQ-1", "content": "<p>SQL and Python</p>",
            "location": {"name": "Dallas, TX"},
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/100",
            "questions": [{"label": "Name", "required": True,
                           "fields": [{"name": "first_name", "type": "input_text"}]}],
            "compliance": [
                {"type": "eeoc", "description": "Exact voluntary disclosure context.",
                 "questions": [question()]},
                {"type": "eeoc", "description": "Exact second disclosure context.",
                 "questions": [question("eeo_second"), question("eeo_third")]}]}


class OfficialGet:
    def __init__(self, body):
        self.body, self.calls = copy.deepcopy(body), []

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append(method)
        if method != "GET" or headers or body is not None:
            raise AssertionError("Fixture permits official public reads only")
        return Response(200, copy.deepcopy(self.body))


class GreenhouseComplianceTests(unittest.TestCase):
    lead = {"company": "Acme", "url": "https://job-boards.greenhouse.io/acme/jobs/100"}

    def adapter(self, body):
        return GreenhouseAdapter({"ats": "greenhouse", "board_token": "acme"}, OfficialGet(body))

    def test_grouped_sections_are_questions_with_exact_context_and_provenance(self):
        body = official_body()
        adapter = self.adapter(body)
        job = adapter.verify(self.lead)
        self.assertTrue(job["schema_verified"])
        self.assertEqual(len(job["questions"]), 4)
        self.assertEqual(job["form_schema"]["sections"]["compliance"], body["compliance"])
        self.assertEqual(job["form_schema_hash"], digest(job["form_schema"]))
        for q, section_index, question_index in zip(job["questions"][1:], (0, 1, 1), (0, 0, 1)):
            section = body["compliance"][section_index]
            original = section["questions"][question_index]
            context = q["greenhouse_compliance"]
            self.assertEqual(context["section_type"], section["type"])
            self.assertEqual(context["section_description"], section["description"])
            self.assertEqual(context["section_index"], section_index)
            self.assertEqual(context["question_index"], question_index)
            self.assertEqual(context["original_question_sha256"], digest(original))
            self.assertEqual(q["required"], original["required"])
            self.assertEqual(q["fields"][0]["values"], original["fields"][0]["values"])
            self.assertEqual(q["fields"][0]["type"], "unsupported_demographic")
        self.assertEqual(adapter.transport.calls, ["GET"])
        self.assertEqual(adapter.transport.body, body)

    def test_changed_parent_legal_context_changes_schema_and_question_hashes(self):
        original = official_body()
        changed = copy.deepcopy(original)
        changed["compliance"][0]["description"] += " An additional exact clause."
        first, second = self.adapter(original).verify(self.lead), self.adapter(changed).verify(self.lead)
        self.assertNotEqual(first["form_schema_hash"], second["form_schema_hash"])
        self.assertNotEqual(digest(first["questions"][1]), digest(second["questions"][1]))

    def test_required_compliance_never_autofills_even_a_verified_override(self):
        body = official_body()
        body["questions"] = []
        body["compliance"] = [{"type": "eeoc", "description": "Mandatory human disclosure context.",
                               "questions": [question(required=True)]}]
        job = self.adapter(body).verify(self.lead)
        q = job["questions"][0]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "facts.md").write_text("Synthetic fixture only\n")
            (root / "answers.md").write_text("No standing demographic answer\n")
            answers = screening_answers(job["questions"], {
                "ground_truth_path": root / "facts.md", "standing_answers_path": root / "answers.md",
                "verified_answers": {"eeo_choice": {"verified": True, "source": "synthetic-review",
                                                     "question_hash": digest(q), "answer": "A"}}})
        self.assertEqual(answers["fields"], {})
        self.assertEqual(len(answers["missing"]), 1)

    def test_optional_compliance_does_not_invent_a_demographic_answer(self):
        body = official_body()
        body["questions"] = []
        job = self.adapter(body).verify(self.lead)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "facts.md").write_text("Synthetic fixture only\n")
            (root / "answers.md").write_text("No standing demographic answer\n")
            answers = screening_answers(job["questions"], {
                "ground_truth_path": root / "facts.md", "standing_answers_path": root / "answers.md"})
        self.assertEqual(answers["fields"], {})
        self.assertEqual(answers["missing"], [])

    def test_legacy_flat_compliance_is_preserved_and_human_only(self):
        body = official_body()
        body["compliance"] = [question()]
        job = self.adapter(body).verify(self.lead)
        self.assertEqual(job["form_schema"]["sections"]["compliance"], body["compliance"])
        self.assertEqual(job["questions"][1]["fields"][0]["type"], "unsupported_demographic")

    def test_missing_or_nonboolean_nested_required_is_never_defaulted(self):
        for value in ("absent", None, 0, 1, "false"):
            with self.subTest(required=value):
                body = official_body()
                q = body["compliance"][0]["questions"][0]
                if value == "absent":
                    q.pop("required")
                else:
                    q["required"] = value
                with self.assertRaisesRegex(SubmissionBlocked, "official_application_requirement_ambiguous"):
                    self.adapter(body).verify(self.lead)

    def test_malformed_or_unrecognized_compliance_sections_fail_closed(self):
        cases = [
            [{"type": "unreviewed", "description": "Context", "questions": [question()]}],
            [{"description": "Context", "questions": [question()]}],
            [{"type": "eeoc", "questions": [question()]}],
            [{"type": "eeoc", "description": None, "questions": [question()]}],
            [{"type": "eeoc", "description": "Context", "questions": None}],
            [{"type": "eeoc", "description": "Context", "questions": ["invalid"]}],
            [{"type": "eeoc", "description": "Context", "questions": [question()], "required": True}],
            [official_body()["compliance"][0], question()],
        ]
        for sections in cases:
            with self.subTest(section_shape=sections):
                body = official_body()
                body["compliance"] = sections
                with self.assertRaisesRegex(SubmissionBlocked, "official_application_schema_malformed"):
                    self.adapter(body).verify(self.lead)

    def test_every_nested_question_is_validated_before_success(self):
        body = official_body()
        body["compliance"][1]["questions"][1]["fields"] = [{"name": "field", "type": 7}]
        with self.assertRaisesRegex(SubmissionBlocked, "official_application_schema_malformed"):
            self.adapter(body).verify(self.lead)

    def test_unknown_nested_section_is_not_recursively_dropped(self):
        body = official_body()
        body["compliance"][1]["questions"] = [{"questions": [question()]}]
        with self.assertRaises(SubmissionBlocked):
            self.adapter(body).verify(self.lead)

    def test_data_consent_remains_a_required_human_attestation(self):
        body = official_body()
        body["data_compliance"] = [{"type": "gdpr", "requires_processing_consent": True,
                                    "requires_retention_consent": False}]
        job = self.adapter(body).verify(self.lead)
        self.assertEqual(job["questions"][-1]["fields"][0]["type"], "human_attestation")
        self.assertTrue(job["questions"][-1]["required"])
        self.assertEqual(job["form_schema"]["sections"]["data_compliance"], body["data_compliance"])

    def test_research_read_does_not_claim_or_require_a_verified_form(self):
        body = official_body()
        body["compliance"] = [{"type": "unreviewed"}]
        job = self.adapter(body).verify(self.lead, require_schema=False)
        self.assertFalse(job["schema_verified"])
        self.assertNotIn("form_schema", job)

    def test_parsed_form_still_does_not_grant_submission_permission(self):
        adapter = self.adapter(official_body())
        job = adapter.verify(self.lead)
        with self.assertRaisesRegex(SubmissionBlocked, "employer_submission_permission_missing"):
            adapter.submit(job, {})
        self.assertEqual(adapter.transport.calls, ["GET"])


if __name__ == "__main__":
    unittest.main()
