import unittest
from unittest.mock import Mock

from cloud.runtime_budget import BudgetExceeded, RuntimeBudget
from cloud.submission import SubmissionBlocked


class RuntimeBudgetTests(unittest.TestCase):
    def assert_boundaries(self, seconds, reserve, *, default=False):
        now = [1234]
        configuration = () if default else (seconds, reserve)
        budget = RuntimeBudget(*configuration, clock=lambda: now[0])
        now[0] = 1234 + seconds - reserve - 1
        budget.require_effect_capacity()
        budget.check_request()
        now[0] += 1
        with self.assertRaisesRegex(SubmissionBlocked,
                                   'runtime_budget_reserved_for_receipts_and_backup'):
            budget.require_effect_capacity()
        budget.check_request()
        now[0] = 1234 + seconds - 1
        budget.check_request()
        now[0] += 1
        with self.assertRaisesRegex(BudgetExceeded, 'cloud_request_budget_exhausted'):
            budget.check_request()

    def test_default_maintenance_budget_keeps_120_minutes_and_30_minute_reserve(self):
        self.assert_boundaries(7200, 1800, default=True)

    def test_orchestrator_budget_allows_180_minutes_with_unchanged_receipt_reserve(self):
        from cloud.orchestrator import RECOVERY_RESERVE_SECONDS, RUNTIME_SECONDS

        self.assertEqual(RUNTIME_SECONDS, 10800)
        self.assertEqual(RECOVERY_RESERVE_SECONDS, 1800)
        self.assert_boundaries(RUNTIME_SECONDS, RECOVERY_RESERVE_SECONDS)

    def test_explicit_upper_bound_is_supported_without_changing_the_default(self):
        self.assert_boundaries(10800, 1800)

    def test_expired_budget_prevents_request(self):
        now = [0]
        budget = RuntimeBudget(100, 20, clock=lambda: now[0])
        store = Mock()
        original = Mock(return_value={'ok': True})
        store.transport = original
        budget.attach(store)
        now[0] = 100
        with self.assertRaises(BudgetExceeded):
            store.transport('POST', '/pages', {}, {})
        original.assert_not_called()

    def test_reserve_blocks_effect_but_allows_receipt_readback(self):
        now = [0]
        budget = RuntimeBudget(100, 20, clock=lambda: now[0])
        budget.require_effect_capacity()
        now[0] = 80
        with self.assertRaises(SubmissionBlocked):
            budget.require_effect_capacity()
        budget.check_request()
        now[0] = 100
        with self.assertRaises(BudgetExceeded):
            budget.check_request()

    def test_invalid_budget_cannot_remove_bound(self):
        for seconds, reserve in ((0, 0), (10801, 1), (100, 100), (100, -1)):
            with self.subTest(seconds=seconds, reserve=reserve), self.assertRaises(ValueError):
                RuntimeBudget(seconds, reserve)


if __name__ == '__main__':
    unittest.main()
