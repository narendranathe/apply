import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import importlib.util
import time
import datetime as dt

spec = importlib.util.spec_from_file_location('work_queue', Path(__file__).resolve().parents[1] / 'outputs/work_queue.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SCRIPT = Path(__file__).resolve().parents[1] / 'outputs/work_queue.py'

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_queue(self, command, data):
        return subprocess.run([sys.executable, str(SCRIPT), '--state', str(self.state), command],
                              input=json.dumps(data), text=True, capture_output=True)

    def test_two_processes_cannot_claim_same_contact_via_different_tasks(self):
        def attempt(n):
            return self.run_queue('claim', dict(task_id=f'task-{n}', resource_key='contact:alice', worker=f'worker-{n}'))
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1], [r.stderr for r in results])
        winner = json.loads(next(r.stdout for r in results if r.returncode == 0))
        self.assertEqual(winner['state'], 'reserved')

    def test_publication_draft_gate_expiry_and_recovery(self):
        q = module.Queue(self.state)
        self.addCleanup(q.db.close)
        profile = 'https://www.linkedin.com/in/fictional/'
        claim = q.claim('task-1', 'contact:'+profile, 'test/run-1')
        token = claim['token']
        with self.assertRaises(ValueError): q.check(token)
        observed = q.properties(token)
        with self.assertRaises(ValueError): q.publish(token, dict(observed, Worker='wrong'))
        q.publish(token, observed)
        with self.assertRaises(ValueError): q.draft_check(token)
        evidence = dict(profile_url=profile, identity_source='Fictional profile', history_source='Fictional thread',
                        history_summary='Fictional full history checked', local_history_check='Fictional ledger',
                        notion_history_check='Fictional history pull', identity_matches=True, full_history_checked=True,
                        suppression_checked=True, draft_eligible=True, checked_at=dt.datetime.now(dt.timezone.utc).isoformat())
        with self.assertRaises(ValueError): q.verify(token, dict(evidence, full_history_checked=False))
        with self.assertRaises(ValueError): q.verify(token, dict(evidence, profile_url=profile+'wrong'))
        self.assertTrue(q.verify(token, evidence)['draft_ready'])
        self.assertFalse(q.draft_check(token)['send_authorized'])
        q.db.execute('UPDATE work_claims SET lease_until=? WHERE token=?', (time.time()-1, token))
        q.db.commit()
        with self.assertRaises(ValueError): q.check(token)
        with self.assertRaises(Exception): q.claim('task-2', 'contact:'+profile, 'test/run-2')
        with self.assertRaises(ValueError): q.release(token, 'not reconciled')
        q.release(token, 'Fictional stopped worker and both-store reconciliation', reconciled=True)
        newer = q.claim('task-2', 'contact:'+profile, 'test/run-2')
        self.assertNotEqual(newer['token'], token)
        with self.assertRaises(ValueError): q.finish(token, 'stale result')

    def test_completion_holds_resource_until_dual_write_ack_and_different_contacts_parallel(self):
        q = module.Queue(self.state)
        self.addCleanup(q.db.close)
        a = q.claim('a', 'contact:a', 'worker/a')
        b = q.claim('b', 'contact:b', 'worker/b')
        q.publish(a['token'], q.properties(a['token']))
        q.finish(a['token'], 'Evidence recorded')
        with self.assertRaises(Exception): q.claim('a2', 'contact:a', 'worker/c')
        q.release(a['token'], 'Fictional matched Notion event and local mirror')
        self.assertEqual(q.claim('a2', 'contact:a', 'worker/c')['state'], 'reserved')
        self.assertEqual(q.get(b['token'])['state'], 'reserved')

    def test_published_fields_survive_notion_text_and_minute_date_format(self):
        q = module.Queue(self.state)
        self.addCleanup(q.db.close)
        c = q.claim('format', 'contact:https://www.linkedin.com/in/fictional/', 'worker/run')
        props = q.properties(c['token'])
        self.assertRegex(props['Work Key'], r'^wk-[a-f0-9]{64}$')
        self.assertEqual(c['lease_until'] % 60, 0)

if __name__ == '__main__':
    unittest.main()
