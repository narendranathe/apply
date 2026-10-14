import unittest

from cloud.notion import AmbiguousWriteError
from cloud.orchestrator import ShadowStore
from cloud.records import make_record
from scripts.cloud_maintenance import run


class MaintenanceTests(unittest.TestCase):
    def test_readiness_is_verified_but_cannot_activate(self):
        store = ShadowStore()
        exported = {'records': [make_record('contacts', {}, 'contact:example')]}
        result = run('readiness', store, exported, private_verified=True)
        self.assertFalse(result['activation_authorized'])
        self.assertIsNone(store.get('deployment:cutover:v2'))
        row = next(iter(store.records.values()))
        self.assertFalse(row['payload']['history_reconciled'])
        self.assertFalse(row['payload']['activation_authorized'])

    def test_private_gate_and_uncertain_readback_stop(self):
        with self.assertRaises(ValueError):
            run('readiness', ShadowStore(), {'records': []})
        class Uncertain(ShadowStore):
            def get(self, identity):
                return None
        with self.assertRaises(AmbiguousWriteError):
            run('readiness', Uncertain(), {'records': []}, private_verified=True)
