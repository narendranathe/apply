"""Synthetic proof that technical admission cannot invent the prior-writer fence."""
import os
import unittest
from unittest.mock import patch

from cloud.orchestrator import _agent_worker_authority
from tests.test_agent_worker_orchestration import HOST


class TechnicalWorkerAuthorityTests(unittest.TestCase):
    def test_prior_writer_fence_requires_actual_host_guard(self):
        store = object()
        with patch.dict(os.environ, HOST, clear=True), patch("cloud.runtime_guard.verify_previous_writers") as guard:
            _, context = _agent_worker_authority(store)
            guard.assert_called_once_with(store)
            self.assertIs(context.get("previous_writer_reconciled"), True)
        with patch.dict(os.environ, HOST, clear=True), patch("cloud.runtime_guard.verify_previous_writers", side_effect=RuntimeError("unresolved")):
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                _agent_worker_authority(store)
        with patch.dict(os.environ, HOST, clear=True):
            _, unfenced = _agent_worker_authority()
        self.assertNotIn("previous_writer_reconciled", unfenced)


if __name__ == "__main__":
    unittest.main()
