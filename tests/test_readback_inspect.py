import json
import unittest

from cloud.records import make_record
from cloud.notion import NotionStore
from scripts.inspect_cloud_readback import inspect
from test_notion import FakeNotion


class ReadbackInspectionTests(unittest.TestCase):
    def test_probe_reads_without_creating_or_exposing_private_payload(self):
        api = FakeNotion()
        store = NotionStore('fixture-token', transport=api, request_interval=0)
        row = make_record('contacts', {'name': 'private-contact', 'message': 'private-message'}, 'contact:fixture')
        store.put(row)
        before = len(api.writes())
        result = inspect(store, [row])
        self.assertEqual(len(api.writes()), before)
        self.assertEqual(result['cloud_rows'], 1)
        self.assertTrue(result['checks'][0]['managed_hash_matches'])
        self.assertTrue(result['checks'][0]['source_core_matches'])
        self.assertNotIn('private-contact', json.dumps(result))
        self.assertNotIn('private-message', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
