import copy
import unittest

from cloud.notion import ConflictError, NotionStore, PaginationError
from cloud.records import make_record
from tests.test_notion import FakeNotion


class BoundedIndexTests(unittest.TestCase):
    def test_latest_manifest_is_server_sorted_and_uniqueness_checked(self):
        api = FakeNotion()
        store = NotionStore('test-token', transport=api, request_interval=0)
        row = make_record('index_manifests', {'schema': 'agent-index/v1'}, 'manifest:one')
        store.put(row)
        api.faults = [('POST', '/query', {'results': copy.deepcopy(api.pages), 'has_more': True})]
        before = len(api.calls)
        result = store.latest_index_manifest()
        body = api.calls[before][2]
        self.assertEqual(body['page_size'], 1)
        self.assertEqual(body['sorts'], [{'timestamp': 'created_time', 'direction': 'descending'}])
        self.assertEqual(result['id'], row['id'])

    def test_duplicate_manifest_identity_cannot_be_selected(self):
        api = FakeNotion()
        store = NotionStore('test-token', transport=api, request_interval=0)
        store.put(make_record('index_manifests', {}, 'manifest:one'))
        api.pages.append(copy.deepcopy(api.pages[0]))
        api.faults = [('POST', '/query', {'results': copy.deepcopy(api.pages[:1]), 'has_more': True})]
        with self.assertRaises(ConflictError):
            store.latest_index_manifest()

    def test_incomplete_lookup_fails_closed(self):
        api = FakeNotion()
        api.faults = [('POST', '/query', {'results': [], 'has_more': False,
                                       'request_status': {'type': 'incomplete'}})]
        with self.assertRaises(PaginationError):
            NotionStore('test-token', transport=api).latest_index_manifest()
