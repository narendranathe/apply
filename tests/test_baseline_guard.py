import unittest

from cloud.baseline_guard import verify_application_coverage
from cloud.notion import ConflictError
from cloud.orchestrator import ShadowStore
from cloud.records import make_record


class CoverageTests(unittest.TestCase):
    def test_fresh_missing_or_edited_archived_receipt_blocks(self):
        old = make_record('applications', {'application_id': 'fixture', 'receipt': 'synthetic-receipt'}, 'receipt:fixture', 'applied_verified')
        baseline = {'records': [old]}
        store = ShadowStore()
        with self.assertRaises(ConflictError):
            verify_application_coverage(store, baseline)
        store.put(old)
        self.assertEqual(verify_application_coverage(store, baseline), 1)
        store.records[old['id']] = make_record('applications', {'application_id': 'fixture'}, old['id'], 'prepared')
        with self.assertRaises(ConflictError):
            verify_application_coverage(store, baseline)
