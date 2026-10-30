"""A partial technical append fences all central agent-intake writes."""
import unittest

from cloud.agent_dispatcher import DispatcherStorageError
from cloud.technical_admission import _records
from tests.test_agent_dispatcher import dispatch
from tests.test_agent_worker import admit, job_store
from tests.test_technical_storage_integration import technical


class TechnicalDispatchFenceTests(unittest.TestCase):
    def test_partial_technical_admission_stops_unrelated_intake_before_any_write(self):
        store = job_store()
        intent = _records(technical("technical_items"))[0]
        store.put(intent)
        before = store.writes
        with self.assertRaises(DispatcherStorageError):
            admit(store)
        self.assertEqual(store.writes, before)

    def test_unavailable_authoritative_snapshot_stops_intake_before_any_write(self):
        store = job_store()
        def unavailable():
            raise TimeoutError("private failure")
        store.index_source_records = unavailable
        before = store.writes
        with self.assertRaises(DispatcherStorageError):
            admit(store)
        self.assertEqual(store.writes, before)

    def test_partial_technical_admission_stops_even_existing_replay_acknowledgement(self):
        store = job_store()
        envelope = admit(store)
        store.put(_records(technical("technical_items"))[0])
        before = store.writes
        with self.assertRaises(DispatcherStorageError):
            dispatch(store, envelope)
        self.assertEqual(store.writes, before)


if __name__ == "__main__":
    unittest.main()
