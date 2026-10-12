#!/usr/bin/env python3
"""Local claim allocator. No network, sending or autonomous remote lock claims.

All local workers use this exact state directory. Notion-only workers require
coordinator-issued grants. See QUEUE_PROTOCOL.md for the publication boundary.
"""
import argparse
import contextlib
import datetime as dt
import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent

def stamp(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()

class Queue:
    def __init__(self, state):
        Path(state).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(state) / 'journal.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=30000')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS work_claims (
              token TEXT PRIMARY KEY, task_id TEXT NOT NULL, resource_key TEXT NOT NULL,
              worker TEXT NOT NULL, state TEXT NOT NULL, lease_until REAL NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, verification TEXT, result TEXT,
              release_evidence TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS active_resource ON work_claims(resource_key) WHERE active=1;
            CREATE UNIQUE INDEX IF NOT EXISTS active_task ON work_claims(task_id) WHERE active=1;
        ''')

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, token, live=True):
        row = self.db.execute('SELECT * FROM work_claims WHERE token=?', (token,)).fetchone()
        if not row or not row['active']:
            raise ValueError('Unknown or released claim; stale worker must stop')
        if live and row['lease_until'] <= time.time():
            raise ValueError('Lease expired; stop work and reconcile before release/reassignment')
        return dict(row)

    def claim(self, task_id, resource_key, worker, lease_seconds=3600):
        if not all(isinstance(v, str) and v.strip() for v in (task_id, resource_key, worker)):
            raise ValueError('Exact task, canonical resource key and unique worker/run ID required')
        if not 60 <= lease_seconds <= 7200:
            raise ValueError('Lease must be 60–7200 seconds')
        if not resource_key.startswith(('contact:', 'company:', 'role:', 'task:')):
            raise ValueError('Use contact:, company:, role: or task: canonical resource key')
        token = str(uuid.uuid4())
        with self.transaction():
            self.db.execute('INSERT INTO work_claims(token,task_id,resource_key,worker,state,lease_until) VALUES(?,?,?,?,?,?)',
                            (token, task_id, resource_key, worker, 'reserved', int((time.time()+lease_seconds)//60)*60))
        return self.get(token)

    def properties(self, token):
        row = self.get(token)
        return {'Worker': row['worker'], 'Work Key': 'wk-'+hashlib.sha256(row['resource_key'].encode()).hexdigest(),
                'Claim Token': row['token'], 'Status': 'In progress',
                'date:Lease Until:start': stamp(row['lease_until']),
                'date:Lease Until:is_datetime': 1}

    def publish(self, token, observed):
        with self.transaction():
            expected = self.properties(token)
            for key, value in expected.items():
                actual = observed.get(key)
                if key == 'date:Lease Until:start' and isinstance(actual, str):
                    actual = dt.datetime.fromisoformat(actual.replace('Z', '+00:00')).isoformat()
                if actual != value:
                    raise ValueError('Notion readback differs: ' + key)
            if self.get(token)['state'] not in ('reserved', 'published'):
                raise ValueError('Cannot republish a finishing claim')
            self.db.execute("UPDATE work_claims SET state='published' WHERE token=?", (token,))
        return self.get(token)

    def check(self, token):
        row = self.get(token)
        if row['state'] != 'published':
            raise ValueError('Work cannot start until the Notion grant is published and read back')
        return row

    def verify(self, token, evidence):
        with self.transaction():
            row = self.check(token)
            expected = 'contact:' + str(evidence.get('profile_url', ''))
            if expected != row['resource_key'] or not expected.startswith('contact:https://www.linkedin.com/in/'):
                raise ValueError('Verified profile must match the exact claimed contact key')
            for field in ('identity_source', 'history_source', 'history_summary', 'local_history_check', 'notion_history_check'):
                if not isinstance(evidence.get(field), str) or not evidence[field].strip():
                    raise ValueError('Missing identity/history evidence: ' + field)
            for field in ('identity_matches', 'full_history_checked', 'suppression_checked', 'draft_eligible'):
                if evidence.get(field) is not True:
                    raise ValueError('Draft prerequisite not verified: ' + field)
            checked = dt.datetime.fromisoformat(evidence['checked_at'].replace('Z', '+00:00'))
            if checked.tzinfo is None or not 0 <= time.time()-checked.timestamp() <= 1800:
                raise ValueError('Identity/history check must be timezone-aware and within 30 minutes')
            self.db.execute('UPDATE work_claims SET verification=? WHERE token=?', (json.dumps(evidence), token))
        return self.draft_check(token)

    def draft_check(self, token):
        row = self.check(token)
        if not row['verification']:
            raise ValueError('No draft: verify identity and complete history first')
        evidence = json.loads(row['verification'])
        age = time.time()-dt.datetime.fromisoformat(evidence['checked_at'].replace('Z', '+00:00')).timestamp()
        if not 0 <= age <= 1800:
            raise ValueError('No draft: identity/history evidence is stale')
        return {'draft_ready': True, 'send_authorized': False, 'token': token, 'evidence': evidence}

    def finish(self, token, result):
        if not isinstance(result, str) or not result.strip():
            raise ValueError('Result/evidence required')
        with self.transaction():
            row = self.get(token)
            if row['state'] not in ('published', 'flushing') or (row['result'] and row['result'] != result):
                raise ValueError('Unpublished claim or changed completion; reconcile')
            self.db.execute("UPDATE work_claims SET state='flushing',result=? WHERE token=?", (result, token))
        return self.get(token)

    def release(self, token, evidence, reconciled=False):
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError('Notion/local completion evidence or stopped-worker reconciliation required')
        with self.transaction():
            row = self.get(token, live=False)
            if not reconciled and (row['state'] != 'flushing' or row['lease_until'] <= time.time()):
                raise ValueError('Incomplete/expired claim requires explicit stopped-worker reconciliation')
            self.db.execute("UPDATE work_claims SET active=0,state='released',release_evidence=? WHERE token=?", (evidence, token))
        return {'released': token, 'evidence': evidence}

    def renew(self, token, lease_seconds=3600):
        if not 60 <= lease_seconds <= 7200:
            raise ValueError('Lease must be 60–7200 seconds')
        with self.transaction():
            self.check(token)
            self.db.execute("UPDATE work_claims SET lease_until=?,state='reserved' WHERE token=?", (int((time.time()+lease_seconds)//60)*60, token))
        return self.get(token)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT/'sync-state')
    parser.add_argument('command', choices=['claim','properties','publish','check','verify','draft-check','finish','release','renew','list'])
    args = parser.parse_args()
    q = Queue(args.state)
    try:
        if args.command == 'list':
            result = [dict(r) for r in q.db.execute('SELECT * FROM work_claims WHERE active=1')]
        else:
            data = json.load(sys.stdin)
            result = getattr(q, args.command.replace('-', '_'))(**data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        q.db.close()

if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
