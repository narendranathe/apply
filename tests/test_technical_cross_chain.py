"""Cross-check independently complete source and intake alias histories."""
import unittest

from cloud.agent_dispatcher import DispatcherStorageError
from cloud.records import stable_id
from cloud.technical_admission import admit_technical_record
from cloud.technical_records import make_technical_record
from tests.test_agent_dispatcher import WRITER
from tests.test_technical_worker_intake import admit, candidate, setup_store


class TechnicalCrossChainTests(unittest.TestCase):
    def test_conflicting_intake_and_admitted_source_fence_unrelated_intake(self):
        for collision in ("source_url", "source_identity"):
            with self.subTest(collision=collision):
                store = setup_store()
                first = candidate()
                admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="first")

                # Construct each chain under its own otherwise empty synthetic
                # history, then simulate their inconsistent combined snapshot.
                other_store = setup_store()
                changes = {"entity_id": stable_id("technical_item", "other")}
                if collision == "source_url":
                    changes["source_identity"] = stable_id("source", "other")
                else:
                    changes["source_url"] = "https://example.org/different"
                other = make_technical_record("technical_items", {**first["payload"], **changes})
                admit_technical_record(other_store, other, writer_context={**WRITER,
                    "orchestrator_verified": True, "previous_writer_reconciled": True})
                kinds = {"technical_items", "technical_admission_intents", "technical_alias_reservations",
                         "technical_admission_completions"}
                for row in other_store.records.values():
                    if row["kind"] in kinds:
                        store.records[row["id"]] = row
                before = store.writes
                with self.assertRaises(DispatcherStorageError):
                    admit(store, "research", evidence=["source:job"], key="other")
                self.assertEqual(store.writes, before)
