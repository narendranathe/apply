#!/usr/bin/env python3
"""Durable, idempotent local mirror for the Notion hiring Activity Ledger.

No network or LinkedIn actions. The connected agent transports pending payloads
to Notion and returns verified acknowledgements. See SYNC_RUNBOOK.md.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_LOG = ROOT / '.migration-private' / 'Outreach_Log.md'
METADATA_PAIRS = {
    'v3-send-intent-hannah-20260924-0111': 'https://app.notion.com/p/3e5e10221fac8113b2d8dc7b4b21250f',
    'v3-send-intent-shirl-20260924-0111': 'https://app.notion.com/p/3e5e10221fac81178bdffac0a5dcef81',
}
METADATA_KEYS = ('company', 'work_key', 'worker', 'claim_token')

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def notion_page_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('app.notion.com', 'www.notion.so', 'notion.so'):
        raise ValueError('Acknowledgement requires a Notion page URL')
    page = re.search(r'([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$', parsed.path.rstrip('/'))
    if not page:
        raise ValueError('Acknowledgement requires an identifiable Notion page ID')
    return 'https://app.notion.com/p/' + page.group(1).replace('-', '').lower()

def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.outreach-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

class Journal:
    def __init__(self, state, log, mirror):
        self.state, self.log, self.mirror = Path(state), Path(log), Path(mirror)
        self.state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / 'journal.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, remote_url TEXT, created TEXT NOT NULL, legacy INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.commit()

    @contextlib.contextmanager
    def lock(self):
        with (self.state / 'mirror.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def validate(self, event):
        required = ('event_id', 'title', 'actor', 'kind', 'occurred_at', 'content', 'delivery_state')
        if any(not isinstance(event.get(k), str) or not event[k] for k in required):
            raise ValueError('Missing or invalid required event field')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', event['event_id']):
            raise ValueError('Invalid event_id')
        if event.get('content_hash', digest(event['content'])) != digest(event['content']):
            raise ValueError('Content hash mismatch')
        if event['delivery_state'] == 'Sent verified' and not event.get('evidence'):
            raise ValueError('Verified send requires evidence')
        event = dict(event)
        event['content_hash'] = digest(event['content'])
        return event

    def record(self, event, remote_url=None, legacy=False):
        return self.record_batch([{'payload': event, 'notion_url': remote_url, 'legacy': legacy}])[0]

    def record_batch(self, items):
        prepared = [(self.validate(item['payload']), notion_page_url(item['notion_url']) if item.get('notion_url') else None,
                     bool(item.get('legacy', False))) for item in items]
        with self.lock():
            with self.db:
                for event, remote_url, legacy in prepared:
                    old = self.db.execute('SELECT * FROM events WHERE event_id=?', (event['event_id'],)).fetchone()
                    if old and old['payload'] != dump(event):
                        raise ValueError('Event ID collision: payload changed; append a correction event')
                    if legacy and event['content'] not in self.log.read_text(encoding='utf-8'):
                        raise ValueError('Legacy record not present verbatim in source log')
                    if not old:
                        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (event['event_id'], dump(event), remote_url, now(), int(legacy)))
                    elif remote_url:
                        if old['remote_url'] and notion_page_url(old['remote_url']) != remote_url:
                            raise ValueError('Multiple Notion rows for one event ID; reconcile duplicates')
                        self.db.execute('UPDATE events SET remote_url=? WHERE event_id=?', (remote_url, event['event_id']))
            self._mirror()
        return [event for event, _, _ in prepared]

    def _mirror(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        existing = self.log.read_text(encoding='utf-8') if self.log.exists() else '# Outreach log\n'
        additions = []
        for row in self.db.execute('SELECT * FROM events ORDER BY rowid'):
            event = json.loads(row['payload'])
            if row['legacy']:
                if event['content'] not in existing:
                    raise ValueError('Previously imported source history changed; reconciliation required')
                continue
            marker = '<!-- outreach-event:' + event['event_id'] + ' -->'
            block = marker + '\n## ' + event['occurred_at'] + ': ' + event['title'] + '\n\nEvent ID: ' + event['event_id'] + '\nActor: ' + event['actor'] + '\nKind: ' + event['kind'] + '\nDelivery state: ' + event['delivery_state'] + '\nContent SHA-256: ' + event['content_hash'] + '\n\n' + event['content'] + '\n<!-- /outreach-event:' + event['event_id'] + ' -->\n'
            if marker in existing and block not in existing:
                raise ValueError('Local event content changed or append was incomplete; reconcile from journal')
            if marker not in existing:
                additions.append('\n\n' + block)
        if not self.log.exists() or additions:
            with self.log.open('a', encoding='utf-8') as stream:
                if stream.tell() == 0:
                    stream.write('# Outreach log\n')
                stream.write(''.join(additions))
                stream.flush()
                os.fsync(stream.fileno())
        if self.mirror.resolve() != self.log.resolve():
            atomic_write(self.mirror, self.log.read_text(encoding='utf-8'))

    def pending(self):
        return [json.loads(r['payload']) for r in self.db.execute('SELECT payload FROM events WHERE remote_url IS NULL ORDER BY rowid')]

    def acknowledge(self, items):
        batch = []
        for item in items:
            row = self.db.execute('SELECT payload FROM events WHERE event_id=?', (item['event_id'],)).fetchone()
            if not row:
                raise ValueError('Unknown local event: ' + item['event_id'])
            event = json.loads(row['payload'])
            if event['content_hash'] != item['content_hash']:
                raise ValueError('Remote acknowledgement hash mismatch')
            batch.append({'payload': event, 'notion_url': item['notion_url']})
        self.record_batch(batch)

    def reconcile_metadata(self, data):
        required = ('correction_event_id', 'occurred_at', 'authorization', 'reason', 'notion_url', 'local_payload', 'notion_payload')
        if any(k not in data for k in required):
            raise ValueError('Metadata correction is missing required proof fields')
        event_id = data['local_payload'].get('event_id')
        target_url = notion_page_url(data['notion_url'])
        if METADATA_PAIRS.get(event_id) != target_url:
            raise ValueError('Metadata reconciliation is not authorized for this exact event and URL')
        row = self.db.execute('SELECT payload,remote_url FROM events WHERE event_id=?', (event_id,)).fetchone()
        if not row or not row['remote_url'] or notion_page_url(row['remote_url']) != target_url:
            raise ValueError('Metadata correction requires an acknowledged matching local event')
        local = json.loads(row['payload'])
        if dump(data['local_payload']) != dump(local):
            raise ValueError('Proof local payload does not exactly match stored event')
        remote = data['notion_payload']
        if not isinstance(remote, dict) or any(k not in METADATA_KEYS or not isinstance(v, str) or not v for k, v in remote.items() if k not in local):
            raise ValueError('Only additional nonempty approved metadata strings may be reconciled')
        if set(remote) - set(local) != {k for k in remote if k in METADATA_KEYS and k not in local}:
            raise ValueError('Unknown additional metadata field')
        if any(k not in remote or dump(remote[k]) != dump(v) for k, v in local.items()):
            raise ValueError('Original event fields differ')
        proof = {'schema':'outreach-metadata-reconciliation/v1','event_id':event_id,'notion_url':target_url,
                 'local_payload':local,'notion_payload':remote,'authorization':data['authorization'],
                 'reason':data['reason'],'occurred_at':data['occurred_at']}
        if not isinstance(data['authorization'], str) or 'Naren' not in data['authorization'] or 'October 1' not in data['authorization'] or not isinstance(data['reason'], str) or not data['reason'].strip():
            raise ValueError('Proof requires Naren October 1 authorization and a reason')
        occurred = dt.datetime.fromisoformat(data['occurred_at'].replace('Z', '+00:00'))
        if occurred.tzinfo is None or occurred.date().isoformat() != '2026-10-01':
            raise ValueError('Proof timestamp must be timezone-aware and dated October 1, 2026')
        event = {'event_id':data['correction_event_id'],'title':'Metadata reconciliation proof for '+event_id,
                 'actor':'Codex','kind':'Correction','occurred_at':data['occurred_at'],'content':dump(proof),
                 'delivery_state':'Not applicable'}
        return self.record(event)

    def _approved_variant(self, local, incoming, source_url):
        event_id = local['event_id']
        canonical = METADATA_PAIRS.get(event_id)
        if not canonical or notion_page_url(source_url) != canonical:
            return False
        added = set(incoming) - set(local)
        if not added or not added <= set(METADATA_KEYS) or any(not isinstance(incoming[k], str) or not incoming[k] for k in added):
            return False
        if any(k not in incoming or dump(incoming[k]) != dump(v) for k, v in local.items()) or incoming.get('content_hash') != local['content_hash']:
            return False
        for row in self.db.execute("SELECT payload,remote_url FROM events WHERE remote_url IS NOT NULL"):
            proof_event = json.loads(row['payload'])
            if proof_event.get('kind') != 'Correction' or row['remote_url'] is None:
                continue
            try:
                proof = json.loads(proof_event['content'])
            except (KeyError, ValueError, TypeError):
                continue
            if (proof_event.get('actor') != 'Codex' or proof_event.get('kind') != 'Correction'
                    or proof_event.get('delivery_state') != 'Not applicable'
                    or not isinstance(proof, dict)
                    or set(proof) != {'schema','event_id','notion_url','local_payload','notion_payload','authorization','reason','occurred_at'}
                    or not isinstance(proof.get('authorization'), str) or 'Naren' not in proof['authorization']
                    or 'October 1' not in proof['authorization']
                    or not isinstance(proof.get('reason'), str) or not proof['reason'].strip()):
                continue
            try:
                proof_time = dt.datetime.fromisoformat(proof_event['occurred_at'].replace('Z', '+00:00'))
            except (KeyError, ValueError, TypeError):
                continue
            if (proof_time.tzinfo is None or proof_time.date().isoformat() != '2026-10-01'
                    or proof.get('occurred_at') != proof_event.get('occurred_at')):
                continue
            if (proof.get('schema') == 'outreach-metadata-reconciliation/v1' and proof.get('event_id') == event_id
                    and proof.get('notion_url') == canonical and dump(proof.get('local_payload')) == dump(local)
                    and dump(proof.get('notion_payload')) == dump(incoming)):
                return True
        return False

    def ingest(self, envelope):
        try:
            if not isinstance(envelope, dict) or not isinstance(envelope.get('events'), list):
                raise ValueError('Invalid inbound envelope')
            accepted = []
            with self.lock():
                with self.db:
                    to_record = []
                    seen_urls = {}
                    for item in envelope['events']:
                        incoming = self.validate(item['payload'])
                        source = notion_page_url(item['notion_url'])
                        if source in seen_urls and seen_urls[source] != incoming['event_id']:
                            raise ValueError('Duplicate Notion page URL for multiple event IDs')
                        seen_urls[source] = incoming['event_id']
                        duplicate = self.db.execute('SELECT event_id FROM events WHERE remote_url IS NOT NULL AND remote_url=? AND event_id<>?', (source, incoming['event_id'])).fetchone()
                        if duplicate:
                            raise ValueError('Duplicate Notion page URL for multiple event IDs')
                        row = self.db.execute('SELECT payload,remote_url FROM events WHERE event_id=?', (incoming['event_id'],)).fetchone()
                        if not row:
                            to_record.append((incoming, source))
                        else:
                            local = json.loads(row['payload'])
                            if row['remote_url'] and notion_page_url(row['remote_url']) != source:
                                raise ValueError('Multiple Notion rows for one event ID; reconcile duplicates')
                            if dump(incoming) != dump(local) and not self._approved_variant(local, incoming, source):
                                raise ValueError('Event ID collision: payload changed; append a correction event')
                            if dump(incoming) == dump(local):
                                to_record.append((incoming, source))
                        accepted.append(incoming)
                    for incoming, source in to_record:
                        old = self.db.execute('SELECT payload,remote_url FROM events WHERE event_id=?', (incoming['event_id'],)).fetchone()
                        if old:
                            if old['payload'] != dump(incoming):
                                raise ValueError('Event ID collision: payload changed; append a correction event')
                            if old['remote_url'] and notion_page_url(old['remote_url']) != source:
                                raise ValueError('Multiple Notion rows for one event ID; reconcile duplicates')
                            if not old['remote_url']:
                                self.db.execute('UPDATE events SET remote_url=? WHERE event_id=?', (source, incoming['event_id']))
                        else:
                            self.db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (incoming['event_id'], dump(incoming), source, now(), 0))
                    if envelope.get('complete') is True:
                        captured = dt.datetime.fromisoformat(envelope['captured_at'].replace('Z', '+00:00'))
                        if captured.tzinfo is None:
                            raise ValueError('Complete pull timestamp must be timezone-aware')
                        age = (dt.datetime.now(dt.timezone.utc)-captured).total_seconds()
                        if age < 0 or age > 300:
                            raise ValueError('Complete pull timestamp must be recent, non-future, and timezone-aware')
                self._mirror()
                if envelope.get('complete') is True:
                    with self.db:
                        self.db.execute("DELETE FROM meta WHERE key='ingest_error'")
                        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('last_complete_pull', envelope['captured_at']))
            return accepted
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('ingest_error', str(exc)))
            raise

    def status(self):
        return {'events': self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                'pending_notion': self.db.execute('SELECT COUNT(*) FROM events WHERE remote_url IS NULL').fetchone()[0],
                'local_log': str(self.log), 'offline_copy': str(self.mirror),
                'last_complete_pull': self.db.execute("SELECT value FROM meta WHERE key='last_complete_pull'").fetchone()[0] if self.db.execute("SELECT value FROM meta WHERE key='last_complete_pull'").fetchone() else None,
                'ingest_error': self.db.execute("SELECT value FROM meta WHERE key='ingest_error'").fetchone()[0] if self.db.execute("SELECT value FROM meta WHERE key='ingest_error'").fetchone() else None,
                'transport': 'Connected agent; no background service is installed'}

    def export(self):
        return [{'payload': json.loads(r['payload']), 'notion_url': r['remote_url']} for r in self.db.execute('SELECT * FROM events ORDER BY rowid')]

def legacy_events(path):
    """Keep every legacy heading/body verbatim, including batch context and skips."""
    text = Path(path).read_text(encoding='utf-8')
    text = text.split('<!-- outreach-event:', 1)[0]
    matches = list(re.finditer(r'^#{2,3} (.+)$', text, re.M))
    parent, date, result = '', '', []
    for i, match in enumerate(matches):
        raw = text[match.start():matches[i+1].start() if i+1 < len(matches) else len(text)].rstrip()
        heading = match.group(1)
        if match.group(0).startswith('## '):
            parent = heading
            d = re.match(r'(\w+ \d+, \d{4}):', heading)
            if d:
                date = dt.datetime.strptime(d.group(1), '%B %d, %Y').date().isoformat()
        if not date:
            continue
        profile = re.search(r'^Profile:\s*(https://(?:www\.)?linkedin\.com/in/[^\s/]+/?)', raw, re.M)
        key = profile.group(1).split('?')[0].replace('https://linkedin.com', 'https://www.linkedin.com').rstrip('/') + '/' if profile else ''
        event = {'event_id': 'legacy-' + digest(raw)[:24], 'title': heading,
                 'actor': 'Historical import', 'kind': 'Historical record', 'occurred_at': date,
                 'content': raw, 'contact_key': key, 'role_key': '', 'intent_id': '',
                 'delivery_state': 'Historical observation',
                 'evidence': 'Verbatim source log; group: ' + parent + '. Imported observation, not a fresh LinkedIn check.'}
        event['content_hash'] = digest(raw)
        result.append(event)
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=ROOT / 'sync-state')
    p.add_argument('--log', type=Path, default=DEFAULT_LOG)
    p.add_argument('--mirror', type=Path, default=ROOT / 'Outreach_Log.md')
    p.add_argument('command', choices=['import-legacy','record','record-batch','pending','ack','ingest','reconcile-metadata','status','export','repair','preflight'])
    p.add_argument('--input', type=Path)
    args = p.parse_args()
    j = Journal(args.state, args.log, args.mirror)
    try:
        if args.command == 'import-legacy':
            events = legacy_events(args.log)
            for event in events:
                j.record(event, legacy=True)
            result = j.status()
        elif args.command in ('record', 'record-batch', 'ack', 'ingest', 'reconcile-metadata'):
            if not args.input:
                raise ValueError('--input is required')
            data = json.loads(args.input.read_text(encoding='utf-8'))
            if args.command == 'record':
                j.record(data)
            elif args.command == 'record-batch':
                j.record_batch([{'payload': event} for event in data])
            elif args.command == 'ack':
                j.acknowledge(data)
            elif args.command == 'reconcile-metadata':
                j.reconcile_metadata(data)
            else:
                # The agent must fetch every page of the Notion ledger before
                # declaring complete. Each row includes the immutable payload.
                j.ingest(data)
            result = j.status()
        elif args.command == 'pending':
            result = j.pending()
        elif args.command == 'export':
            result = j.export()
        elif args.command == 'repair':
            with j.lock():
                j._mirror()
            result = j.status()
        else:
            result = j.status()
            if args.command == 'preflight':
                with j.lock():
                    j._mirror()
                t = result['last_complete_pull']
                age = (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(t.replace('Z', '+00:00'))).total_seconds() if t else None
                fresh = age is not None and 0 <= age <= 300
                result['sync_ready'] = bool(not result['pending_notion'] and fresh and not result['ingest_error'])
                result['note'] = 'Sync readiness only; ownership, suppression, identity, follow-up limits, and live delivery checks are separately required.'
                print(json.dumps(result, indent=2))
                return 0 if result['sync_ready'] else 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        j.db.close()

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
