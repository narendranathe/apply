import json
import unittest

from cloud.applications import StateConflict
from cloud.orchestrator import _failure_summary


class ApplicationDiagnosticTests(unittest.TestCase):
    def test_known_conflict_uses_only_the_closed_code(self):
        error = StateConflict('application_changed_before_transition')
        error._cloud_operation = 'run_applications'
        self.assertEqual(_failure_summary(error), {
            'status': 'blocked', 'error_type': 'StateConflict',
            'reason': 'application_changed_before_transition',
            'cloud_operation': 'run_applications',
        })

    def test_private_or_extended_error_text_is_never_logged(self):
        for value in ('private contact and message body',
                      'application_changed_before_transition private body',
                      {'token': 'private-value'}):
            result = _failure_summary(StateConflict(value))
            self.assertEqual(result['reason'], 'inspect_private_reconciliation_records')
            self.assertNotIn('private', json.dumps({k: v for k, v in result.items() if k != 'reason'}))


if __name__ == '__main__':
    unittest.main()
