"""Synthetic native/AES durable custody admission; no live or private inputs."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cloud import index_custody_evidence as evidence
from cloud import index_custody_receipt as subject
from cloud import index_custody_reconciliation as recovery
from cloud.outreach import record
from cloud.records import content_hash
from cloud.startup_reconciliation import SnapshotState, ReconciliationBlocked
from scripts.encrypted_backup import create_backup
from tests.test_index_custody_evidence import HASHES, SOURCES, jobs, run
from tests.test_index_custody_reconciliation import fixture
from tests.test_startup_reconciliation import entry


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class GitHub:
    def __init__(self):
        self.calls, self.runs, self.jobs, self.logs = [], {}, {}, {}
        self.callback = None
        self.add(900)
        self.history = [deepcopy(self.runs[900])]
        self.listing = {'total_count': 1, 'artifacts': []}
        self.artifact, self.zip = None, None
        for identity in recovery.FAILURES:
            self.add(int(identity))

    def add(self, identity, *, mode='reconcile'):
        self.runs[identity] = run(identity, active=False)
        self.runs[identity]['display_title'] = 'index-custody:' + mode
        self.jobs[identity] = jobs(identity, active=False)
        self.jobs[identity]['jobs'][0]['steps'][0].update(
            started_at='2026-10-28T01:00:00Z', completed_at='2026-10-28T01:00:59Z')

    def fetch(self, path):
        self.calls.append(path)
        if self.callback:
            self.callback(path, self)
        if path.startswith('/contents/'):
            name = path.removeprefix('/contents/').partition('?')[0]
            return {'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(SOURCES[name]).decode()}
        if path.startswith('/actions/workflows/'):
            page = int(path.rsplit('page=', 1)[1])
            return {'total_count': len(self.history), 'workflow_runs': deepcopy(self.history[(page-1)*100:page*100])}
        if path.startswith('/actions/artifacts/'):
            return deepcopy(self.artifact)
        if path.startswith('/actions/runs/'):
            identity = int(path.removeprefix('/actions/runs/').partition('/')[0])
            if '/jobs?' in path:
                return deepcopy(self.jobs[identity])
            if '/artifacts?' in path:
                return deepcopy(self.listing if identity in {900, 901} else {'total_count': 0, 'artifacts': []})
            return deepcopy(self.runs[identity])
        raise AssertionError('unexpected synthetic route')

    def download(self, identity):
        self.calls.append('download:' + str(identity))
        return self.zip

    def download_path(self, path):
        self.calls.append(path)
        return self.logs[path]


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='apply-test-custody-receipt-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = b'k' * 32
        self.exported, self.before, observed, self.proof, self.snapshots = fixture()
        # Keep the actual 757-record baseline gate and immutable readback active.
        original = deepcopy(observed.snapshot)
        for number in range(756):
            row = record('contact:synthetic:' + str(number), 'contacts', {'suppressed': True}, 'suppressed')
            self.exported['records'].append(row)
            original['pages'].append(entry(row))
        self.observed = SnapshotState(original)
        self.snapshots['observed'].update(source_fingerprint=self.observed.fingerprint(),
            pages=len(self.observed.pages), cores=len(self.observed.rows))
        self.github = GitHub()
        self.binding = {'run_id': '900', 'head_sha': 'b' * 40, 'run_attempt': 1,
            'repository': evidence.REPOSITORY, 'workflow_path': evidence.WORKFLOW,
            'actor': evidence.OWNER, 'triggering_actor': evidence.OWNER,
            'event': 'workflow_dispatch', 'head_branch': 'main',
            'source_sha256': deepcopy(HASHES), 'mode': 'reconcile'}
        native = {}
        for identity in recovery.FAILURES:
            identity_int = int(identity)
            job_id = str(self.github.jobs[identity_int]['jobs'][0]['id'])
            log = b'synthetic closed native diagnostic\n'
            self.github.logs['/actions/jobs/' + job_id + '/logs'] = log
            native[identity] = {'run': evidence._projection(self.github.runs[identity_int]),
                'jobs_sha256': content_hash(self.github.jobs[identity_int]), 'job_count': 1,
                'artifacts_sha256': content_hash({'total_count': 0, 'artifacts': []}), 'artifact_count': 0,
                'logs_sha256': {job_id: sha(log)}, 'artifacts': {}}
        patches = [patch.object(evidence, 'require_reviewed_pins'),
            patch.object(evidence, 'reviewed_proof', side_effect=lambda: deepcopy(self.proof)),
            patch.object(evidence, 'SNAPSHOTS', self.snapshots),
            patch.object(evidence, 'INCIDENTS', native), patch.object(evidence, 'SNAPSHOT_NATIVE', {}),
            patch.object(evidence, 'HISTORICAL_SOURCES', {evidence.WORKFLOW: HASHES[evidence.WORKFLOW]}),
            patch.object(evidence, '_local_sources', return_value=deepcopy(HASHES)),
            patch.object(recovery, 'BASELINE_HASH', content_hash(self.exported)),
            patch.object(recovery, '_preserved_native'), patch.object(subject, 'contained_cohort'),
            patch.object(subject, 'RECEIPT_PATH', self.root / 'receipt.json'),
            patch.object(subject, 'COMPLETE_ARCHIVE_PATH', self.root / 'complete.aesgcm')]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        self.rows = recovery.resolution_records(recovery.reviewed_plan(), self.binding)
        complete = deepcopy(original)
        complete['captured_at'] = '2026-10-28T01:00:15Z'
        complete['pages'].extend(entry(row) for row in self.rows)
        self.wrapper = {'schema': 'apply-cloud-snapshot/v1', 'snapshot': complete,
            'exported_at': '2026-10-28T01:00:30Z'}
        self.key_path = self.root / 'key'; self.key_path.write_bytes(self.key); self.key_path.chmod(0o600)
        self.archive = self.encrypt(self.wrapper, 'valid')
        self.backup_proof, cipher_hash, _ = subject.restore_and_validate_archive(
            self.archive, self.key, self.binding, exported=self.exported)
        self.summary = {'verified': True, 'correction_binding': self.binding,
            'backup_verification': self.backup_proof,
            'backup': {'sha256': cipher_hash, 'restore_verified': True,
                'records': self.backup_proof['complete_pages']}}
        self.package()

    def encrypt(self, wrapper, name):
        source = self.root / (name + '.json')
        source.write_text(json.dumps(wrapper))
        archive = self.root / (name + '.aesgcm')
        create_backup({'cloud-records.json': source}, archive, key_path=self.key_path,
                      private_destination_verified=True)
        return archive

    def package(self, *, selected=900, cipher=None, summary=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('maintenance-summary.json', json.dumps(summary or self.summary))
            archive.writestr('cloud-backup-' + str(selected) + '.aesgcm', cipher or self.archive.read_bytes())
        self.github.zip = stream.getvalue()
        self.github.artifact = {'id': 2000, 'name': 'index-custody-evidence-' + str(selected),
            'size_in_bytes': len(self.github.zip), 'digest': 'sha256:' + sha(self.github.zip),
            'expired': False, 'created_at': '2026-10-28T01:01:00Z', 'updated_at': '2026-10-28T01:01:00Z',
            'expires_at': '2026-11-11T01:01:00Z', 'workflow_run': {'id': selected,
                'head_sha': 'b' * 40, 'head_branch': 'main'}}
        self.github.listing['artifacts'] = [deepcopy(self.github.artifact)]

    def prepare(self, selected=900):
        return subject.prepare_receipt(self.github, str(selected), self.key, self.exported)

    def admit(self, selected=900):
        receipt, cipher = self.prepare(selected)
        subject.RECEIPT_PATH.write_text(json.dumps(receipt))
        subject.COMPLETE_ARCHIVE_PATH.write_bytes(cipher)
        return receipt

    def matches(self):
        rows = {row['id']: row for row in self.rows}
        return subject.custody_resolution_matches(recovery.FAILURES[0], self.rows[-1],
            self.github.fetch, evidence.REPOSITORY, read_record=rows.get)

    def test_actual_native_aes_admission_and_runtime_without_artifact_retention(self):
        value = self.admit()
        self.assertEqual(set(value), subject.RECEIPT_FIELDS)
        self.assertTrue(value['independently_authenticated'])
        self.assertEqual(value['snapshot_proof']['historical_records_unchanged'], 757)
        self.assertFalse(value['external_effects_reconciled'])
        self.github.calls.clear()
        self.assertTrue(self.matches())
        self.assertFalse(any('/artifacts' in path or '/logs' in path or 'download:' in path
                             for path in self.github.calls))

    def test_later_successful_verification_admits_original_binding(self):
        self.github.add(901, mode='verify')
        self.github.history.append(deepcopy(self.github.runs[901]))
        self.package(selected=901)
        value = self.admit(901)
        self.assertEqual(value['correction_binding']['run_id'], '900')
        self.assertEqual(value['verification_run']['id'], 901)
        self.assertTrue(self.matches())

    def test_full_cohort_missing_receipt_never_clears(self):
        self.assertFalse(self.matches())

    def test_later_main_commit_with_identical_protocol_can_verify_original_binding(self):
        self.github.add(901, mode='verify')
        self.github.runs[901]['head_sha'] = 'c' * 40
        self.github.jobs[901]['jobs'][0]['head_sha'] = 'c' * 40
        self.github.history.append(deepcopy(self.github.runs[901]))
        self.package(selected=901)
        self.github.artifact['workflow_run']['head_sha'] = 'c' * 40
        self.github.listing['artifacts'] = [deepcopy(self.github.artifact)]
        value = self.admit(901)
        self.assertEqual(value['correction_binding']['head_sha'], 'b' * 40)
        self.assertEqual(value['verification_run']['head_sha'], 'c' * 40)
        self.assertTrue(self.matches())

    def test_corrupt_receipt_or_cipher_never_clears(self):
        receipt = self.admit()
        for name in ['receipt', 'cipher', 'truth', 'extra']:
            subject.RECEIPT_PATH.write_text(json.dumps(receipt))
            subject.COMPLETE_ARCHIVE_PATH.write_bytes(self.archive.read_bytes())
            if name == 'receipt': subject.RECEIPT_PATH.write_text('{broken')
            if name == 'cipher': subject.COMPLETE_ARCHIVE_PATH.write_bytes(b'wrong')
            if name in {'truth', 'extra'}:
                value = deepcopy(receipt)
                if name == 'truth': value['activation_authorized'] = True
                else: value['unrecognized'] = True
                value['receipt_content_sha256'] = content_hash({k: v for k, v in value.items()
                                                              if k != 'receipt_content_sha256'})
                subject.RECEIPT_PATH.write_text(json.dumps(value))
            with self.subTest(name=name): self.assertFalse(self.matches())

    def test_new_failed_queued_or_foreign_maintenance_attempt_fences_runtime(self):
        self.admit()
        for status, event in [('completed', 'workflow_dispatch'), ('queued', 'workflow_dispatch'), ('completed', 'push')]:
            extra = run(902, active=False); extra.update(status=status, event=event, conclusion='failure')
            self.github.history = [deepcopy(self.github.runs[900]), extra]
            with self.subTest(status=status, event=event):
                self.assertFalse(self.matches())
                with self.assertRaisesRegex(ReconciliationBlocked, 'custody_maintenance_requires_reconciliation'):
                    subject.verify_maintenance_history(lambda identity: self.rows[-1], self.github.fetch,
                                                       evidence.REPOSITORY)

    def test_self_rehashed_receipt_cannot_change_typed_artifact_or_snapshot_witness(self):
        receipt = self.admit()
        for change in ('artifact_id', 'artifact_run', 'artifact_extra', 'proof_extra'):
            value = deepcopy(receipt)
            if change == 'artifact_id': value['artifact_witness']['id'] = 0
            if change == 'artifact_run': value['artifact_witness']['workflow_run']['head_sha'] = 'd' * 40
            if change == 'artifact_extra': value['artifact_witness']['unknown'] = True
            if change == 'proof_extra': value['snapshot_proof']['unknown'] = True
            value['receipt_content_sha256'] = content_hash({k: v for k, v in value.items()
                                                          if k != 'receipt_content_sha256'})
            subject.RECEIPT_PATH.write_text(json.dumps(value))
            with self.subTest(change=change):
                self.assertFalse(self.matches())

    def test_history_blocks_admission_before_durable_output(self):
        self.github.history.append({**run(902, active=False), 'conclusion': 'failure'})
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_receipt_unreviewed_maintenance_attempt'):
            self.prepare()
        self.assertFalse(subject.RECEIPT_PATH.exists())

    def test_changed_existing_raw_source_rejected_even_with_exact_counts(self):
        changed = deepcopy(self.wrapper)
        changed['snapshot']['pages'][2]['blocks'][3]['paragraph']['rich_text'][1]['plain_text'] = 'unknown'
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_receipt_post_backup_source_changed'):
            subject.validate_completed_snapshot(changed, self.binding, exported=self.exported)

    def test_unknown_extra_or_missing_page_rejected(self):
        for change in ['extra', 'missing', 'replacement']:
            wrapper = deepcopy(self.wrapper)
            if change == 'extra': wrapper['snapshot']['pages'].append(entry(record('unknown', 'audit_events', {})))
            if change == 'missing': wrapper['snapshot']['pages'].pop(0)
            if change == 'replacement': wrapper['snapshot']['pages'][0] = entry(record('unknown', 'audit_events', {}))
            with self.subTest(change=change), self.assertRaises(ReconciliationBlocked):
                subject.validate_completed_snapshot(wrapper, self.binding, exported=self.exported)

    def test_missing_changed_or_partial_resolution_snapshot_rejected(self):
        for change in ['missing', 'changed']:
            wrapper = deepcopy(self.wrapper)
            if change == 'missing': wrapper['snapshot']['pages'].pop()
            else: wrapper['snapshot']['pages'][-1] = entry(record(self.rows[-1]['id'], 'audit_events', {}))
            with self.subTest(change=change), self.assertRaises(ReconciliationBlocked):
                subject.validate_completed_snapshot(wrapper, self.binding, exported=self.exported)

    def test_aes_authentication_failure_cannot_be_replaced_by_summary(self):
        raw = bytearray(self.archive.read_bytes()); raw[-1] ^= 1
        self.package(cipher=bytes(raw))
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_receipt_archive_authentication_failed'):
            self.prepare()

    def test_authenticated_unknown_source_cannot_be_replaced_by_claimed_summary(self):
        changed = deepcopy(self.wrapper)
        changed['snapshot']['pages'][0] = entry(record('unknown', 'contacts', {}))
        cipher = self.encrypt(changed, 'unknown').read_bytes()
        self.package(cipher=cipher)
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_receipt_post_backup_source_changed'):
            self.prepare()

    def test_native_incident_log_mismatch_blocks_admission(self):
        key = next(iter(self.github.logs)); self.github.logs[key] = b'changed'
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_native_log_changed'):
            self.prepare()

    def test_artifact_digest_members_expiration_or_summary_mismatch_blocks(self):
        for change in ['digest', 'expired', 'summary']:
            self.package()
            if change == 'digest': self.github.artifact['digest'] = 'sha256:' + 'a' * 64
            if change == 'expired': self.github.artifact['expired'] = True
            if change == 'summary':
                summary = deepcopy(self.summary); summary['backup_verification']['complete_pages'] += 1
                self.package(summary=summary)
            if change != 'summary': self.github.listing['artifacts'] = [deepcopy(self.github.artifact)]
            with self.subTest(change=change), self.assertRaises(ReconciliationBlocked): self.prepare()

    def test_snapshot_backup_must_fall_within_native_operation(self):
        wrapper = deepcopy(self.wrapper); wrapper['exported_at'] = '2026-10-28T01:01:01Z'
        archive = self.encrypt(wrapper, 'late')
        proof, cipher_hash, _ = subject.restore_and_validate_archive(archive, self.key, self.binding,
                                                                  exported=self.exported)
        summary = deepcopy(self.summary); summary['backup_verification'] = proof; summary['backup']['sha256'] = cipher_hash
        self.package(cipher=archive.read_bytes(), summary=summary)
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_receipt_backup_outside_native_operation'):
            self.prepare()

    def test_mutable_artifact_reread_after_aes_rejected(self):
        path = '/actions/artifacts/2000'
        def mutate(current, github):
            if current == path and github.calls.count(path) == 2:
                github.artifact['updated_at'] = '2026-10-28T02:00:00Z'
        self.github.callback = mutate
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_receipt_native_evidence_changed'):
            self.prepare()

    def test_verifier_native_hosting_and_terminal_steps_required(self):
        self.github.add(901, mode='verify'); self.github.history.append(deepcopy(self.github.runs[901]))
        self.package(selected=901)
        self.github.jobs[901]['jobs'][0]['runner_group_name'] = 'Default'
        with self.assertRaises(ReconciliationBlocked): self.prepare(901)

    def test_repository_privacy_change_on_native_reread_is_rejected(self):
        path = '/actions/runs/900'
        def mutate(current, github):
            if current == path and github.calls.count(path) == 2:
                github.runs[900]['repository']['private'] = False
        self.github.callback = mutate
        with self.assertRaises(ReconciliationBlocked): subject._verify_run(self.github.fetch, '900')


if __name__ == '__main__':
    unittest.main()
