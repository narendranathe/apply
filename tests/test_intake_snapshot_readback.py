"""Independent lookup disagreements must never permit intake writes or replay."""
import unittest
from unittest.mock import patch

from cloud.agent_dispatcher import (
    DispatcherStorageError, _queue_and_result, _semantic_reservation,
)
from cloud.records import make_record
from tests.test_agent_dispatcher import WRITER, dispatch, proposal, writer_store
from tests.test_technical_worker_intake import admit, candidate, setup_store


class IntakeSnapshotReadbackTests(unittest.TestCase):
    def test_complete_replay_with_all_exact_reads_missing_does_not_write(self):
        store = setup_store()
        envelope = admit(store, "admit_technical_source", evidence=["source:job"],
                         candidate=candidate(), key="fixture")
        queue, ack = _queue_and_result(envelope)
        reservation = _semantic_reservation(envelope)
        # Hide each exact lookup only once; a mistaken append could otherwise
        # pass readback and appear to be a successful replay.
        hidden = {row["id"] for row in (envelope, queue, ack, reservation)}
        original_get = store.get

        def initially_hidden(identity):
            if identity in hidden:
                hidden.remove(identity)
                return None
            return original_get(identity)

        before = store.writes
        with patch.object(store, "get", side_effect=initially_hidden):
            with self.assertRaisesRegex(DispatcherStorageError, "intake_snapshot_readback_mismatch"):
                dispatch(store, envelope, context={**WRITER, "previous_writer_reconciled": True})
        self.assertEqual(store.writes, before)

    def test_exact_read_must_match_snapshot_for_every_intake_member(self):
        for technical in (False, True):
            for member in ("intake", "queue", "ack", "reservation"):
                for disagreement in ("missing", "changed"):
                    with self.subTest(technical=technical, member=member, disagreement=disagreement):
                        if technical:
                            store = setup_store()
                            envelope = admit(store, "admit_technical_source", evidence=["source:job"],
                                             candidate=candidate(), key="fixture")
                        else:
                            store, _ = writer_store()
                            envelope = proposal(store)
                            dispatch(store, envelope)
                        queue, ack = _queue_and_result(envelope)
                        reservation = _semantic_reservation(envelope)
                        row = {"intake": envelope, "queue": queue, "ack": ack,
                               "reservation": reservation}[member]
                        original_get = store.get

                        def inconsistent(identity):
                            if identity != row["id"]:
                                return original_get(identity)
                            if disagreement == "missing":
                                return None
                            return make_record(row["kind"], {**row["payload"], "changed": True},
                                               row["id"], row["status"])

                        before = store.writes
                        with patch.object(store, "get", side_effect=inconsistent):
                            with self.assertRaisesRegex(DispatcherStorageError, "intake_snapshot_readback_mismatch"):
                                dispatch(store, envelope, context={**WRITER, "previous_writer_reconciled": True})
                        self.assertEqual(store.writes, before)

    def test_snapshot_omitting_existing_generic_member_stops_replay(self):
        store, _ = writer_store()
        envelope = proposal(store)
        dispatch(store, envelope)
        queue, _ = _queue_and_result(envelope)
        original_query = store.query
        before = store.writes
        with patch.object(store, "query", side_effect=lambda kind=None: [
            row for row in original_query(kind) if row["id"] != queue["id"]
        ]):
            with self.assertRaisesRegex(DispatcherStorageError, "intake_snapshot_readback_mismatch"):
                dispatch(store, envelope)
        self.assertEqual(store.writes, before)


if __name__ == "__main__":
    unittest.main()
