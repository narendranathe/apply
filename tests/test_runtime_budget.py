import unittest
from unittest.mock import Mock

from cloud.runtime_budget import BudgetExceeded, RuntimeBudget
from cloud.submission import SubmissionBlocked


class RuntimeBudgetTests(unittest.TestCase):
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
        for seconds, reserve in ((0, 0), (7201, 1), (100, 100), (100, -1)):
            with self.subTest(seconds=seconds, reserve=reserve), self.assertRaises(ValueError):
                RuntimeBudget(seconds, reserve)


if __name__ == '__main__':
    unittest.main()
