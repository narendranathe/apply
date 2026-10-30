"""Offline tests for immutable, source-dated technical intelligence records."""
import copy
import unittest

from cloud.records import content_hash, stable_id, validate_record
from cloud.technical_records import make_technical_record, validate_technical_record


def sample():
    return {
        "entity_id": stable_id("technical_item", "example"),
        "revision": 1,
        "source_url": "https://example.org/blog/one?utm_source=test&id=7",
        "source_identity": stable_id("source", "example"),
        "author": "Example Author",
        "author_verified": True,
        "first_seen_at": "2026-10-29T12:00:00+00:00",
        "source_published_at": "2026-10-28T10:00:00+00:00",
        "observed_at": "2026-10-30T12:00:00+00:00",
        "content_hash": "a" * 64,
        "freshness": "fresh",
        "confidentiality": "public",
        "evidence_refs": ["source-revision:fixture"],
        "status": "observed",
        "actor": "agent:fixture",
    }


class TechnicalRecordTests(unittest.TestCase):
    def test_versioned_technical_envelope_round_trips_through_existing_validator(self):
        record = make_technical_record("technical_items", sample())
        self.assertEqual(record["kind"], "technical_items")
        self.assertEqual(record["status"], "observed")
        self.assertEqual(record["payload"]["schema"], "apply-technical-record/v1")
        self.assertEqual(record["payload"]["source_url"], "https://example.org/blog/one?id=7")
        self.assertEqual(validate_record(record), record)
        self.assertEqual(validate_technical_record(record), record)

    def test_every_required_family_has_stable_identity_and_immutable_revisions(self):
        kinds = ["technical_sources", "technical_items", "engineering_observations",
                 "technical_signals", "technical_hypotheses", "engineering_opportunities",
                 "contact_topic_relevances", "engagement_intents", "engagement_outcomes",
                 "content_drafts", "content_revisions", "publisher_intents", "publisher_receipts",
                 "source_cursors", "technical_next_actions"]
        for kind in kinds:
            with self.subTest(kind=kind):
                first = make_technical_record(kind, sample())
                replay = make_technical_record(kind, sample())
                new_payload = sample()
                new_payload["revision"] = 2
                revised = make_technical_record(kind, new_payload)
                self.assertEqual(first, replay)
                self.assertNotEqual(first["id"], revised["id"])
                self.assertEqual(validate_technical_record(first), first)
        with self.assertRaises(ValueError):
            make_technical_record("unknown_family", sample())

    def test_missing_or_invalid_provenance_fails_closed(self):
        cases = []
        for field in sample():
            payload = sample()
            del payload[field]
            cases.append(("missing_" + field, payload))
        for field, value in [("revision", 0), ("revision", True), ("entity_id", ""),
                             ("source_identity", ""), ("source_url", "file:///etc/passwd"),
                             ("author", ""), ("author_verified", False),
                             ("first_seen_at", "2026-10-29"),
                             ("source_published_at", "2026-11-01T12:00:00+00:00"),
                             ("observed_at", "2026-10-27T12:00:00+00:00"),
                             ("content_hash", "not-a-hash"), ("freshness", "always"),
                             ("confidentiality", "unknown"), ("evidence_refs", [""]),
                             ("status", ""), ("actor", ""), ("schema", "foreign/v1")]:
            payload = sample()
            payload[field] = value
            cases.append((field, payload))
        for name, payload in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    make_technical_record("technical_items", payload)
        unknown_author = sample()
        unknown_author.update(author=None, author_verified=False, source_published_at=None)
        self.assertEqual(make_technical_record("technical_items", unknown_author)["payload"]["author"], None)

    def test_corrupt_envelopes_and_mismatched_ids_are_rejected(self):
        valid = make_technical_record("technical_items", sample())
        for change in ("payload_without_hash", "identity", "status", "schema", "missing_hash"):
            record = copy.deepcopy(valid)
            if change == "payload_without_hash":
                record["payload"]["actor"] = "another-agent"
            elif change == "identity":
                record["id"] = "article:another"
            elif change == "status":
                record["status"] = "verified"
            elif change == "schema":
                record["payload"]["schema"] = "foreign/v1"
                record["hash"] = content_hash(record["payload"])
            else:
                del record["hash"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_technical_record(record)

    def test_same_revision_conflicts_instead_of_silently_replacing_content(self):
        payload = sample()
        original = make_technical_record("technical_items", payload)
        payload["content_hash"] = "b" * 64
        revised_without_new_revision = make_technical_record("technical_items", payload)
        self.assertEqual(original["id"], revised_without_new_revision["id"])
        self.assertNotEqual(original["hash"], revised_without_new_revision["hash"])
        self.assertEqual(original["payload"]["content_hash"], "a" * 64)
        # A shared store must reject the two different envelopes at the same ID;
        # this pure module does not itself perform storage or prove readback.

    def test_internal_draft_uses_evidence_identity_without_invented_url(self):
        payload = sample()
        payload.update(entity_id=stable_id("draft", "fixture"), source_url=None,
                       source_identity=stable_id("source", "internal"), author=None,
                       author_verified=False, evidence_refs=[stable_id("source", "internal")])
        record = make_technical_record("content_drafts", payload)
        self.assertIsNone(record["payload"]["source_url"])
        self.assertEqual(validate_technical_record(record), record)
        payload["evidence_refs"] = []
        with self.assertRaises(ValueError):
            make_technical_record("content_drafts", payload)

    def test_source_url_retains_identity_parameters(self):
        payload = sample()
        payload["source_url"] = "https://example.org/story?source=abc&ref=xyz&id=7&utm_medium=mail&fbclid=click"
        record = make_technical_record("technical_items", payload)
        self.assertEqual(record["payload"]["source_url"], "https://example.org/story?id=7&ref=xyz&source=abc")

    def test_exact_source_alias_collisions_block_distinct_entities(self):
        from cloud.technical_records import validate_technical_aliases
        first = sample()
        second = sample()
        second.update(entity_id=stable_id("technical_item", "other"), source_identity=stable_id("source", "other"))
        first_record = make_technical_record("technical_items", first)
        second_record = make_technical_record("technical_items", second)
        with self.assertRaises(ValueError):
            validate_technical_aliases([first_record, second_record])
        second["source_url"] = "https://another.example/story"
        second["source_identity"] = first["source_identity"]
        with self.assertRaises(ValueError):
            validate_technical_aliases([first_record, make_technical_record("technical_items", second)])
        second["entity_id"] = first["entity_id"]
        second["revision"] = 2
        validate_technical_aliases([first_record, make_technical_record("technical_items", second)])

    def test_unverified_publisher_receipt_cannot_claim_publication(self):
        payload = sample()
        payload["status"] = "published_verified"
        with self.assertRaises(ValueError):
            make_technical_record("publisher_receipts", payload)

    def test_metadata_identifiers_are_bounded_and_structured(self):
        for field, value in [("entity_id", "article:readable-identifier"),
                             ("source_identity", "source:readable-identifier"),
                             ("entity_id", "private conversation text"),
                             ("entity_id", "x" * 201),
                             ("source_identity", "https://example.org/a?secret=private"),
                             ("source_identity", "name\nprivate-message"),
                             ("evidence_refs", ["private message body"]),
                             ("evidence_refs", ["e" * 201])]:
            payload = sample()
            payload[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                make_technical_record("technical_items", payload)
        # Syntax constraints and size limits cannot detect all secrets.
        payload = sample()
        payload.update(entity_id=stable_id("article", "example_7"), source_identity=stable_id("source", "fixture"),
                       evidence_refs=["source-revision:fixture-1"])
        self.assertEqual(validate_technical_record(make_technical_record("technical_items", payload))["payload"]["entity_id"], payload["entity_id"])


if __name__ == "__main__":
    unittest.main()
