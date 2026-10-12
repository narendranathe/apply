import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloud.migration import SCHEMA, application_delta, combine_exports, record, reconcile_records
from scripts.encrypted_backup import create_backup, private_key
from scripts.verify_migration_archive import verify


def source_fixture(status='draft_ready', applied_at=None):
    folder = '2026-10-12-acme-role'
    url = 'https://higher.gs.com/roles/185198'
    row = {'folder': folder, 'company': 'Acme', 'role': 'Engineer', 'status': status,
           'url': url, 'claimed_by': 'source-worker', 'claimed_at': '2026-10-12', 'applied_at': applied_at}
    prefix = 'applications/' + folder + '/'
    return {'applications/registry.json': json.dumps({url: row}).encode(),
            prefix + 'status.json': json.dumps({'status': status, 'gate': 'PASS'}).encode(),
            prefix + 'resume.tex': b'Exact source with newline\n',
            prefix + 'enumitem.sty': b'Exact dependency\n',
            prefix + 'answers.json': b'{"unresolved":true}',
            'resumes/variants/' + folder + '.tex': 'Unicode café\n'.encode(),
            'docs/ground-truth.md': b'Source statement, not runtime approval',
            'audit/base-ground-truth.md': b'Previous source',
            'audit/ground-truth.diff': b'+ Source statement', 'AGENTS.md': b'Source old policy'}


class ApplicationDeltaTests(unittest.TestCase):
    def test_exact_dependencies_stable_identity_and_no_qa_inference(self):
        files = source_fixture()
        first = application_delta(files, source_commit='c' * 40, base_commit='b' * 40)
        self.assertEqual(first, application_delta(files, source_commit='c' * 40, base_commit='b' * 40))
        app = next(row for row in first['records'] if row['kind'] == 'applications')
        packet = next(row for row in first['records'] if row['kind'] == 'application_packets')
        self.assertEqual(app['status'], 'prepared')
        self.assertEqual(app['payload']['requisition_id'], '185198')
        self.assertFalse(app['payload']['receipt_verified'])
        self.assertFalse(app['payload']['official_verified'])
        self.assertEqual(app['payload']['timestamp_precision'], 'date')
        self.assertEqual(packet['status'], 'incomplete')
        self.assertFalse(packet['payload']['ready'])
        self.assertFalse(packet['payload']['qa_verified'])
        self.assertFalse(packet['payload']['source_pdf_present'])
        self.assertEqual(packet['payload']['source_status_document']['gate'], 'PASS')
        for entry in packet['payload']['source_files']:
            raw = entry['content_utf8'].encode()
            self.assertEqual(raw, files[entry['path']])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])

    def test_any_old_attempt_without_receipt_is_unknown(self):
        delta = application_delta(source_fixture(applied_at='2026-10-12'), source_commit='c', base_commit='b')
        app = next(row for row in delta['records'] if row['kind'] == 'applications')
        self.assertEqual(app['status'], 'submission_unknown')

    def test_later_submission_report_appends_unknown_without_erasing_draft(self):
        first = application_delta(source_fixture(), source_commit='first', base_commit='base')
        files = source_fixture(status='applied', applied_at='2026-10-12')
        files['applications/2026-10-12-acme-role/status.json'] = b'{"status":"draft_ready"}'
        second = application_delta(files, source_commit='second', base_commit='first', previous_records=first['records'])
        merged = combine_exports([first, second])
        from cloud.applications import fold_applications
        latest = next(iter(fold_applications([x for x in merged['records'] if x['kind']=='applications']).values()))
        self.assertEqual(latest['status'], 'submission_unknown')
        self.assertEqual(latest['payload']['revision'], 1)
        self.assertTrue(latest['payload']['source_status_conflict'])
        self.assertEqual(latest['payload']['source_status_document']['status'], 'draft_ready')
        self.assertFalse(latest['payload']['receipt_verified'])
        self.assertEqual(len([x for x in merged['records'] if x['kind']=='applications']), 2)

    def test_mirror_identity_remains_unresolved_with_both_observed_ids(self):
        files = source_fixture()
        original = json.loads(files['applications/registry.json'])
        row = next(iter(original.values()))
        row['company'] = 'JPMorganChase'
        url = 'https://jpmorganchase.veteran-hiring.com/job-detail/engineer-210767115'
        row['url'] = url
        files['applications/registry.json'] = json.dumps({url: row}).encode()
        files['audit/identity-observations.json'] = json.dumps({url: {'job_id': '210767115',
            'requisition_id': '300091444962411', 'source': 'unverified mirror observation'}}).encode()
        delta = application_delta(files, source_commit='c', base_commit='b')
        app = next(r for r in delta['records'] if r['kind'] == 'applications')['payload']
        self.assertTrue(app['employer_history_unresolved'])
        self.assertEqual(app['observed_job_id'], '210767115')
        self.assertEqual(app['observed_requisition_id'], '300091444962411')
        self.assertFalse(app['identity_observation_verified'])
        self.assertIn('requisition:jpmorganchase:300091444962411', app['identity_aliases'])

    def test_source_conflicts_and_combined_conflicts_stop(self):
        files = source_fixture()
        files['applications/2026-10-12-acme-role/status.json'] = b'{"status":"approved"}'
        with self.assertRaises(ValueError):
            application_delta(files, source_commit='c', base_commit='b')
        a = {'schema': SCHEMA, 'records': [record('same', 'audit_events', {'a': 1}, 'observed')], 'blocked': []}
        b = {'schema': SCHEMA, 'records': [record('same', 'audit_events', {'a': 2}, 'observed')], 'blocked': []}
        with self.assertRaises(ValueError):
            combine_exports([a, b])
        self.assertEqual(len(combine_exports([a, a])['records']), 1)

    def test_delta_import_rerun_is_unchanged(self):
        delta = application_delta(source_fixture(), source_commit='c', base_commit='b')
        class Store:
            def __init__(self): self.rows = {}
            def get(self, key): return self.rows.get(key)
            def put(self, row): self.rows[row['id']] = row
            def query(self, kind=None): return list(self.rows.values())
        store = Store()
        first = reconcile_records(delta['records'], store, dry_run=False, private_destination=True)
        second = reconcile_records(delta['records'], store, dry_run=False, private_destination=True)
        self.assertEqual(first['imported'], 3)
        self.assertEqual(second['unchanged'], 3)
        self.assertEqual(second['conflicted'], 0)

    def test_hosted_verify_all_binds_both_archives_and_combines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / 'key.bin'
            private_key(key, create=True)
            baseline = {'schema': SCHEMA, 'records': [record('one', 'audit_events', {'value': 1}, 'observed')], 'blocked': []}
            delta = {'schema': SCHEMA, 'records': [record('two', 'audit_events', {'value': 2}, 'observed')], 'blocked': []}
            for index, exported in enumerate((baseline, combine_exports([baseline, delta]))):
                source = root / f'input-{index}.json'
                source.write_text(json.dumps(exported))
                archive = root / f'{index}.aesgcm'
                receipt = create_backup({'normalized/merged-historical-records.json': source}, archive,
                                       key_path=key, private_destination_verified=True)
                receipt.update({'archive': str(archive), 'canonical_records': len(exported['records'])})
                if index:
                    receipt['files'] = [{'member_id': hashlib.sha256(row['name'].encode()).hexdigest(),
                                         'bytes': row['bytes'], 'sha256': row['sha256']} for row in receipt['files']]
                archive.with_suffix('.receipt.json').write_text(json.dumps(receipt))
            with patch.dict(os.environ, {'MIGRATION_BACKUP_KEY': base64.b64encode(key.read_bytes()).decode()}), contextlib.redirect_stdout(io.StringIO()):
                result = verify(directory=root)
            self.assertEqual(len(result['archives']), 2)
            self.assertEqual(result['normalized_records'], 2)
            self.assertTrue(result['hashes_verified'])


if __name__ == '__main__':
    unittest.main()
