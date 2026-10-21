"""Exact identity selection after validating every independent lookup match."""
from copy import deepcopy
import unittest

from cloud.notion import ConflictError, NotionStore, _rich_text
from cloud.records import make_record
from tests import test_notion as notion_fixture
from tests.test_notion import FakeNotion


class NotionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeNotion()
        self.store = NotionStore('synthetic-token', transport=self.api, request_interval=0)
        self.requested = make_record('entity_projections', {'synthetic': True}, 'projection:requested')
        self.unrelated = make_record('audit_events', {'nested': {'id': self.requested['id']}},
                                     'audit:unrelated')

    def test_valid_nested_identity_reference_is_not_the_requested_record(self):
        self.store.put(self.unrelated)
        self.assertIsNone(self.store.get(self.requested['id']))
        self.assertEqual(len(self.api.writes()), 1)

    def test_valid_nested_reference_does_not_prevent_exact_creation_or_lookup(self):
        self.store.put(self.unrelated)
        created = self.store.put(self.requested)
        self.assertEqual(created['id'], self.requested['id'])
        self.assertEqual(self.store.get(self.requested['id'])['hash'], self.requested['hash'])
        self.assertEqual(len(self.api.writes()), 2)

    def test_corrupted_unrelated_candidate_blocks_get_and_creation(self):
        self.store.put(self.unrelated)
        self.api.pages[0]['properties']['Cloud Hash'] = {'rich_text': _rich_text('edited')}
        for operation in (lambda: self.store.get(self.requested['id']),
                          lambda: self.store.put(self.requested)):
            with self.subTest(operation=operation), self.assertRaises(ConflictError) as caught:
                operation()
            self.assertEqual(caught.exception.operation, 'verify_cloud_properties')
        self.assertEqual(len(self.api.writes()), 1)

    def test_cleared_or_edited_actual_display_identity_still_blocks_creation(self):
        for replacement in ('', 'projection:edited'):
            with self.subTest(replacement=replacement):
                api = FakeNotion()
                store = NotionStore('synthetic-token', transport=api, request_interval=0)
                store.put(self.requested)
                api.pages[0]['properties']['Cloud ID'] = {'rich_text': _rich_text(replacement)}
                with self.assertRaises(ConflictError) as caught:
                    store.put(self.requested)
                self.assertEqual(caught.exception.operation, 'verify_cloud_properties')
                self.assertEqual(len(api.writes()), 1)

    def test_duplicate_exact_identity_blocks_even_with_valid_nested_match(self):
        self.store.put(self.requested)
        self.store.put(self.unrelated)
        self.api.pages.append(deepcopy(self.api.pages[0]))
        with self.assertRaisesRegex(ConflictError, 'Duplicate Cloud ID'):
            self.store.get(self.requested['id'])
        self.assertEqual(len(self.api.writes()), 2)

    def test_duplicate_unrelated_nested_identity_blocks_get_and_creation(self):
        self.store.put(self.unrelated)
        self.api.pages.append(deepcopy(self.api.pages[0]))
        for operation in (lambda: self.store.get(self.requested['id']),
                          lambda: self.store.put(self.requested)):
            with self.subTest(operation=operation), self.assertRaisesRegex(ConflictError, 'Duplicate Cloud ID'):
                operation()
        self.assertEqual(len(self.api.writes()), 1)

    def test_corrupted_nested_candidate_cannot_hide_behind_an_exact_match(self):
        self.store.put(self.requested)
        self.store.put(self.unrelated)
        self.api.pages[1]['properties']['Cloud Status'] = {'rich_text': _rich_text('edited')}
        with self.assertRaises(ConflictError) as caught:
            self.store.get(self.requested['id'])
        self.assertEqual(caught.exception.operation, 'verify_cloud_properties')
        self.assertEqual(len(self.api.writes()), 2)

    def test_legacy_lookup_selects_exact_decoded_identity_and_preserves_duplicates(self):
        fixture = notion_fixture.NotionTests()
        fixture.setUp()
        payload = fixture.add_legacy()
        self.assertEqual(fixture.store.get(payload['event_id'])['id'], payload['event_id'])
        fixture.api.pages.append(deepcopy(fixture.api.pages[0]))
        with self.assertRaisesRegex(ConflictError, 'Duplicate legacy Event ID'):
            fixture.store.get(payload['event_id'])
        self.assertFalse(fixture.api.writes())

    def test_legacy_lookup_never_returns_a_different_decoded_identity(self):
        fixture = notion_fixture.NotionTests()
        fixture.setUp()
        fixture.add_legacy()
        original = fixture.api.__call__

        def transport(method, path, body, headers):
            if (method == 'POST' and path.endswith('/query')
                    and body.get('filter', {}).get('property') == 'Event ID'):
                return notion_fixture.page_list(deepcopy(fixture.api.pages))
            return original(method, path, body, headers)

        fixture.store.transport = transport
        self.assertIsNone(fixture.store.get('event:absent'))
        self.assertFalse(fixture.api.writes())

    def test_duplicate_unrelated_legacy_identity_blocks_get_and_creation(self):
        fixture = notion_fixture.NotionTests()
        fixture.setUp()
        fixture.add_legacy()
        fixture.api.pages.append(deepcopy(fixture.api.pages[0]))
        original = fixture.api.__call__

        def transport(method, path, body, headers):
            if (method == 'POST' and path.endswith('/query')
                    and body.get('filter', {}).get('property') == 'Event ID'):
                return notion_fixture.page_list(deepcopy(fixture.api.pages))
            return original(method, path, body, headers)

        fixture.store.transport = transport
        requested = make_record('outreach_events', {'synthetic': True}, 'event:absent')
        for operation in (lambda: fixture.store.get(requested['id']),
                          lambda: fixture.store.put(requested)):
            with self.subTest(operation=operation), self.assertRaisesRegex(ConflictError, 'Duplicate legacy Event ID'):
                operation()
        self.assertFalse(fixture.api.writes())


if __name__ == '__main__':
    unittest.main()
