import copy
import unittest

from cloud.notion import ConflictError, NotionStore, PaginationError
from cloud.records import make_record
from tests.test_notion import FakeNotion


class BoundedIndexTests(unittest.TestCase):
    def test_source_index_avoids_generated_versions_without_hiding_corruption(self):
        api = FakeNotion()
        store = NotionStore('test-token', transport=api, request_interval=0)
        source = make_record('applications', {'application_id': 'a'}, 'app:a')
        store.put(source)
        for kind in ('entity_projections', 'index_manifests', 'index_changes', 'agent_tasks'):
            store.put(make_record(kind, {'schema': 'agent-index/v1'}, 'generated:' + kind))
        self.assertEqual([r['id'] for r in store.index_source_records()], ['app:a'])
        from tests.test_notion import compound_depth
        queries = [c for c in api.calls if c[0] == 'POST' and c[1].endswith('/query')]
        self.assertTrue(all(compound_depth(c[2]['filter']) <= 2 for c in queries))
        api.pages[0]['properties']['Cloud Kind']['rich_text'][0]['text']['content'] = 'entity_projections'
        with self.assertRaises(ConflictError):
            store.index_source_records()

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
