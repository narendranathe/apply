"""Synthetic permanent holds for uncertain legacy coordination grants."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import unittest
from unittest.mock import patch

from cloud.agent_index import build_index
from cloud.outreach import run_outreach
from cloud.records import make_record
from tests.test_agent_index import contact, event, projection, tasks

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
PROFILE_FIELDS = ("profile_url", "linkedin_url", "linkedin", "contact_key", "url", "profile_aliases")
PROFILE_FORMS = ("https://www.linkedin.com/in/fixture-one/?trk=public-profile",
                 "https://www.linkedin.com/in/fixture-one#experience",
                 "http://www.linkedin.com/in/fixture-one/",
                 "https://uk.linkedin.com/in/fixture-one/",
                 "https://en.www.linkedin.com/in/fixture-one/?trk=public-profile#experience")


def quarantine(*, identity=None, classification="effect_unknown", **updates):
    payload = {"schema": "apply-coordination-quarantine/v1", "active": True,
               "classification": classification,
               "identity": identity or {"contact_ids": ["contact:one"]},
               "provenance": {key: character * 64 for key, character in
                              (("claim_sha256", "a"), ("task_sha256", "b"), ("resource_sha256", "c"))},
               "observed_at": NOW.isoformat(), "evidence_refs": ["audit:stopped-worker"], **updates}
    status = "unstarted_unverified" if classification == "unstarted_unverified" else "needs_reconciliation"
    return make_record("exceptions", payload, "coordination-quarantine:" + "a" * 64, status)


def outreach_contact(identity="contact:one", **updates):
    context = "Synthetic verified conversation context"
    return make_record("contacts", {"contact_id": identity, "state": "connected", "name": "Fixture",
        "identity_verified": True, "history_complete": True, "verified_at": NOW.isoformat(),
        "unanswered_total": 0, "genuine_reply": True, "reply_context": context,
        "verified_reply_draft": "Synthetic draft", "reply_draft_context_hash": hashlib.sha256(context.encode()).hexdigest(),
        **updates}, "source:" + identity)


class Store:
    def __init__(self, rows):
        self.rows, self.written = rows, []

    def query(self, kind=None):
        return deepcopy([row for row in self.rows if kind is None or row["kind"] == kind])

    def put(self, row):
        self.written.append(deepcopy(row))
        return row


class QuarantineConsumerTests(unittest.TestCase):
    def test_outreach_holds_contact_but_other_contacts_continue(self):
        store = Store([outreach_contact(), outreach_contact("contact:other"), quarantine()])
        with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
            result = run_outreach(store, {}, "accepted", NOW)
        self.assertEqual(result["drafts"], 1)
        self.assertEqual(result["blocked"], {"coordination_effect_unknown": 1})
        held = next(row for row in store.written if row["payload"]["contact_id"] == "source:contact:one")
        self.assertNotIn("draft", held["payload"])
        self.assertEqual(held["payload"]["coordination_quarantine"]["evidence_refs"], [quarantine()["id"]])

    def test_outreach_existing_suppression_remains_visible_with_quarantine(self):
        for updates, expected in (({"opt_out": True}, "suppressed"), ({"state": "pending"}, "pending"),
                                  ({"delivery_unknown": True}, "delivery_unknown")):
            with self.subTest(updates=updates):
                store = Store([outreach_contact(**updates), quarantine()])
                with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
                    result = run_outreach(store, {}, "accepted", NOW)
                self.assertEqual(result["drafts"], 0)
                self.assertEqual(result["blocked"], {expected: 1})
                self.assertEqual(store.written[0]["payload"]["coordination_quarantine"]["reason"], "coordination_effect_unknown")

    def test_index_new_verified_revision_and_reply_do_not_clear_hold(self):
        rows = [contact(revision=1), contact(revision=2), event("recruiter_reply_confirmed", "fresh-reply"), quarantine()]
        result = build_index(rows, NOW, source_complete=True)
        for identity in ("contact:one", "thread:one"):
            value = projection(result, identity)
            self.assertTrue(value["suppression"]["retry_blocked"])
            self.assertIn("coordination_effect_unknown", value["blockers"])
            self.assertEqual(value["state"], "reply_received")
            self.assertEqual(value["next_action"]["status"], "blocked")
        self.assertEqual(tasks(result, "contact:one")[0]["action"], "reconcile_uncertain_action")

    def test_source_identity_survives_new_entity_revision(self):
        source_id = "00000000-0000-4000-8000-000000000001"
        hold = quarantine(identity={"source_notion_ids": [source_id]})
        result = build_index([contact(source_notion_id=source_id), hold], NOW, source_complete=True)
        self.assertTrue(projection(result, "contact:one")["suppression"]["retry_blocked"])

    def test_profile_alias_holds_outreach_even_when_contact_id_changes(self):
        hold = quarantine(identity={"profile_urls": ["https://www.linkedin.com/in/fixture-one/"]})
        store = Store([outreach_contact("contact:new", profile_url="https://linkedin.com/in/fixture-one"), hold])
        with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
            result = run_outreach(store, {}, "accepted", NOW)
        self.assertEqual(result["drafts"], 0)
        self.assertEqual(result["blocked"], {"coordination_effect_unknown": 1})

    def test_all_recognized_profile_fields_and_url_forms_hold_actual_outreach(self):
        hold = quarantine(identity={"profile_urls": ["https://www.linkedin.com/in/fixture-one/"]})
        for field in PROFILE_FIELDS:
            for url in PROFILE_FORMS:
                with self.subTest(field=field, url=url):
                    value = ["https://www.linkedin.com/in/unrelated/", url] if field == "profile_aliases" else url
                    store = Store([outreach_contact("contact:new", **{field: value}), hold])
                    with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
                        result = run_outreach(store, {}, "accepted", NOW)
                    self.assertEqual(result["drafts"], 0)
                    self.assertEqual(result["blocked"], {"coordination_effect_unknown": 1})

    def test_profile_aliases_hold_new_verified_index_contact_and_thread(self):
        hold = quarantine(identity={"profile_urls": ["https://www.linkedin.com/in/fixture-one/"]})
        for field in PROFILE_FIELDS:
            for url in PROFILE_FORMS:
                with self.subTest(field=field, url=url):
                    value = ["https://www.linkedin.com/in/unrelated/", url] if field == "profile_aliases" else url
                    rows = [contact("contact:new", revision=9, **{field: value}), hold,
                            event("recruiter_reply_confirmed", "fresh-reply", contact_id="contact:new")]
                    result = build_index(rows, NOW, source_complete=True)
                    for identity in ("contact:new", "thread:one"):
                        projected = projection(result, identity)
                        self.assertTrue(projected["suppression"]["retry_blocked"])
                        self.assertIn("coordination_effect_unknown", projected["blockers"])
                        self.assertEqual(projected["next_action"]["status"], "blocked")

    def test_matching_unknown_schema_and_malformed_hold_fail_closed(self):
        for updates in ({"schema": "apply-coordination-quarantine/v9"}, {"active": False},
                        {"provenance": {}}, {"expires_at": "2020-01-01T00:00:00Z"}):
            with self.subTest(updates=updates):
                store = Store([outreach_contact(), quarantine(**updates)])
                with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
                    result = run_outreach(store, {}, "accepted", NOW)
                self.assertEqual(result["drafts"], 0)
                self.assertEqual(result["blocked"], {"coordination_quarantine_invalid": 1})

    def test_invalid_matching_record_keeps_index_blocked(self):
        for hold in (quarantine(active=False), quarantine(schema="apply-coordination-quarantine/v8")):
            result = build_index([contact(), hold], NOW, source_complete=True)
            value = projection(result, "contact:one")
            self.assertIn("coordination_quarantine_invalid", value["blockers"])
            self.assertTrue(value["suppression"]["retry_blocked"])
            self.assertEqual(tasks(result, "contact:one")[0]["status"], "blocked")

    def test_unscoped_corrupt_quarantine_stops_before_outreach_or_index(self):
        from cloud.coordination_quarantine import QuarantineError
        hold = quarantine(identity={"unexpected": ["private-synthetic-data"]})
        store = Store([outreach_contact(), hold])
        with self.assertRaisesRegex(QuarantineError, "^coordination_quarantine_invalid$"):
            run_outreach(store, {}, "accepted", NOW)
        self.assertEqual(store.written, [])
        with self.assertRaisesRegex(QuarantineError, "^coordination_quarantine_invalid$"):
            build_index([contact(), hold], NOW, source_complete=True)

    def test_unrelated_malformed_contact_hold_does_not_block_other_contact(self):
        hold = quarantine(identity={"contact_ids": ["contact:other"]}, active=False)
        store = Store([outreach_contact(), hold])
        with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
            self.assertEqual(run_outreach(store, {}, "accepted", NOW)["drafts"], 1)

    def test_company_coordination_does_not_suppress_contact_or_company(self):
        hold = quarantine(identity={"company_ids": ["company:acme"]}, classification="unstarted_unverified")
        result = build_index([contact(), hold], NOW, source_complete=True)
        self.assertFalse(projection(result, "contact:one")["suppression"]["retry_blocked"])
        store = Store([outreach_contact(), hold])
        with patch("cloud.outreach.verified_proof", return_value="Synthetic verified proof"):
            self.assertEqual(run_outreach(store, {}, "accepted", NOW)["drafts"], 1)

    def test_pending_and_opt_out_are_preserved(self):
        for state, opt_out in (("pending", False), ("connected", True)):
            with self.subTest(state=state, opt_out=opt_out):
                result = build_index([contact(state=state, opt_out=opt_out), quarantine()], NOW, source_complete=True)
                value = projection(result, "contact:one")
                self.assertEqual(value["state"], "suppressed" if opt_out else state)
                self.assertTrue(value["suppression"]["retry_blocked"])


class QuarantineRecordTests(unittest.TestCase):
    def test_constructor_and_validation_are_immutable_and_strict(self):
        from cloud.coordination_quarantine import make_quarantine, validate_quarantine
        expected = quarantine()
        payload = deepcopy(expected["payload"])
        actual = make_quarantine(payload)
        self.assertEqual(actual, expected)
        self.assertEqual(validate_quarantine(actual), expected)
        payload["identity"]["contact_ids"].append("contact:changed")
        self.assertEqual(actual, expected)

    def test_no_expiry_resolution_or_outcome_assertions_are_accepted(self):
        from cloud.coordination_quarantine import make_quarantine, QuarantineError
        for updates in ({"active": False}, {"expires_at": NOW.isoformat()}, {"resolved": True},
                        {"delivery_state": "Not sent"}, {"effect_verified": True}, {"message": "private"},
                        {"classification": "completed"}, {"schema": "apply-coordination-quarantine/v2"},
                        {"observed_at": "2026-10-13"}, {"evidence_refs": []},
                        {"provenance": {"claim_sha256": "a" * 64}}):
            with self.subTest(updates=updates), self.assertRaisesRegex(QuarantineError, "^coordination_quarantine_invalid$"):
                make_quarantine(quarantine(**updates)["payload"])

    def test_identity_scope_and_envelope_cannot_be_weakened(self):
        from cloud.coordination_quarantine import make_quarantine, validate_quarantine, QuarantineError
        for identity in ({}, {"contact_ids": []}, {"company_ids": ["company:acme"]},
                         {"profile_urls": ["https://other.test/in/fixture"]},
                         {"source_notion_ids": ["not-an-exact-id"]}):
            payload = deepcopy(quarantine()["payload"])
            payload["identity"] = identity
            with self.subTest(identity=identity), self.assertRaises(QuarantineError):
                make_quarantine(payload)
        for key, value in (("kind", "audit_events"), ("status", "done"), ("id", "different"), ("hash", "0" * 64)):
            row = quarantine()
            row[key] = value
            with self.subTest(key=key), self.assertRaises(QuarantineError):
                validate_quarantine(row)


if __name__ == "__main__":
    unittest.main()
