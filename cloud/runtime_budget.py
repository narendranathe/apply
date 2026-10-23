"""Monotonic request deadline with reserved time for receipts and recovery."""
import time


class BudgetExceeded(RuntimeError):
    pass


class RuntimeBudget:
    def __init__(self, seconds=7200, reserve_seconds=1800, *, clock=time.monotonic):
        if not 0 < seconds <= 10800 or not 0 <= reserve_seconds < seconds:
            raise ValueError('Invalid cloud runtime budget')
        self.clock = clock
        self.deadline = clock() + seconds
        self.effect_deadline = self.deadline - reserve_seconds

    def check_request(self):
        if self.clock() >= self.deadline:
            raise BudgetExceeded('cloud_request_budget_exhausted')

    def require_effect_capacity(self):
        if self.clock() >= self.effect_deadline:
            from cloud.submission import SubmissionBlocked
            raise SubmissionBlocked('runtime_budget_reserved_for_receipts_and_backup')

    def attach(self, store):
        transport = store.transport
        def bounded(*args, **kwargs):
            self.check_request()
            return transport(*args, **kwargs)
        store.transport = bounded
        return store
