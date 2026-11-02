"""Synthetic, offline comparison only; no custody authority or effects."""
from copy import deepcopy
import unittest

from cloud.custody_snapshot_delta_offline import compare_snapshots, SnapshotComparisonUnavailable
from cloud.outreach import record
from tests.test_startup_reconciliation import entry, states


class CustodySnapshotDeltaOfflineTests(unittest.TestCase):
    def test_classifies_unchanged_and_added_pages_without_leaking_contents(self):
        _, _, before, _ = states()
        after = deepcopy(before)
        after['pages'].append(entry(record('contact:private-person', 'contacts',
                                           {'note': 'synthetic private text'}, 'suppressed')))
        result = compare_snapshots(before, after)
        self.assertEqual(result['pages'], {'added': 1, 'removed': 0, 'changed': 0})
        self.assertEqual(result['records'], {'added': 1, 'removed': 0, 'changed': 0})
        self.assertFalse(result['source_metadata_changed'])
        self.assertFalse(result['source_fingerprints_equal'])
        self.assertFalse(result['authenticity_verified'])
        self.assertFalse(result['reconciliation_authorized'])
        self.assertNotIn('private-person', str(result))
        self.assertNotIn('synthetic private text', str(result))

    def test_incomplete_or_foreign_snapshots_fail_closed_with_sanitized_error(self):
        _, _, before, _ = states()
        for after in ({**deepcopy(before), 'complete': False},
                      {**deepcopy(before), 'data_source_id': 'other'}):
            with self.subTest(after_complete=after['complete']), \
                    self.assertRaisesRegex(SnapshotComparisonUnavailable, 'snapshot_comparison_unavailable'):
                compare_snapshots(before, after)

    def test_current_snapshot_older_than_observed_is_unavailable(self):
        _, _, before, _ = states()
        after = deepcopy(before)
        after['captured_at'] = '2026-10-15T21:00:00Z'
        with self.assertRaisesRegex(SnapshotComparisonUnavailable, 'snapshot_comparison_unavailable'):
            compare_snapshots(before, after)

    def test_changed_and_removed_records_and_unmanaged_page_are_counted(self):
        _, _, before, _ = states()
        after = deepcopy(before)
        after['pages'][0] = entry(record('contact:synthetic', 'contacts', {'suppressed': False}, 'open'))
        after['pages'].pop(1)
        after['pages'][1]['blocks'][0]['paragraph']['rich_text'][0]['text']['content'] = 'different private text'
        result = compare_snapshots(before, after)
        self.assertEqual(result['pages'], {'added': 0, 'removed': 1, 'changed': 2})
        self.assertEqual(result['records'], {'added': 0, 'removed': 1, 'changed': 1})
        self.assertNotIn('different private text', str(result))

    def test_historical_edit_stamps_are_ignored_but_metadata_drift_is_visible(self):
        _, _, before, _ = states()
        after = deepcopy(before)
        after['pages'][0]['page']['last_edited_time'] = '2026-10-18T00:00:00Z'
        after['data_source']['request_id'] = 'transient'
        unchanged = compare_snapshots(before, after)
        self.assertTrue(unchanged['source_fingerprints_equal'])
        self.assertEqual(unchanged['pages'], {'added': 0, 'removed': 0, 'changed': 0})
        after['data_source']['properties']['new'] = {'type': 'text'}
        drift = compare_snapshots(before, after)
        self.assertTrue(drift['source_metadata_changed'])
        self.assertFalse(drift['source_fingerprints_equal'])


if __name__ == '__main__':
    unittest.main()
