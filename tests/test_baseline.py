"""Authenticated baseline restoration leaves no plaintext or credential files."""
import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from cloud.baseline import BaselineUnavailable, restore_baseline
from cloud.migration import SCHEMA, combine_exports
from cloud.records import content_hash, make_record
from scripts.encrypted_backup import create_backup
from scripts.verify_migration_archive import restore_all


class BaselineRestoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='apply-baseline-test-')
        self.root = Path(self.folder.name)
        self.key = os.urandom(32)
        self.encoded_key = base64.b64encode(self.key).decode('ascii')
        self.key_path = self.root / 'fixture.key'
        self.key_path.write_bytes(self.key)
        self.key_path.chmod(0o600)
        self.export = combine_exports([{'schema': SCHEMA, 'blocked': [], 'records': [
            make_record('contacts', {'name': 'private-contact-marker'}, 'contact:synthetic')]}])
        self.source = self.root / 'source.json'
        self.source.write_text(json.dumps(self.export))
        self.archive = self.root / 'fixture.aesgcm'
        receipt = create_backup({'normalized/merged-historical-records.json': self.source}, self.archive,
                                key_path=self.key_path, private_destination_verified=True)
        receipt.update(archive=str(self.archive), canonical_records=len(self.export['records']))
        (self.root / 'fixture.receipt.json').write_text(json.dumps(receipt))

    def tearDown(self):
        self.folder.cleanup()

    def test_real_authenticated_restore_returns_exact_export_hash_and_cleans_private_staging(self):
        paths = []
        def inspect(destination, key_path, directory):
            paths.append(key_path.parent)
            self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key_path.parent.stat().st_mode), 0o700)
            self.assertEqual(key_path.read_bytes(), self.key)
            return restore_all(destination, key_path, directory)
        output = io.StringIO()
        with patch('cloud.baseline.restore_all', side_effect=inspect), redirect_stdout(output):
            exported, actual_hash = restore_baseline(self.root, key_value=self.encoded_key)
        self.assertEqual(exported, self.export)
        self.assertEqual(actual_hash, content_hash(self.export))
        self.assertEqual(output.getvalue(), '')
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_uses_existing_environment_credential_without_returning_it(self):
        with patch.dict(os.environ, {'MIGRATION_BACKUP_KEY': self.encoded_key}):
            exported, actual_hash = restore_baseline(self.root)
        self.assertEqual(actual_hash, content_hash(exported))
        self.assertNotIn(self.encoded_key, json.dumps(exported))

    def test_tampered_ciphertext_is_rejected_without_exposing_upstream_diagnostics(self):
        self.archive.write_bytes(self.archive.read_bytes() + b'corruption')
        with self.assertRaisesRegex(BaselineUnavailable, '^authenticated_migration_baseline_unavailable$'):
            restore_baseline(self.root, key_value=self.encoded_key)

    def test_invalid_or_wrong_key_never_releases_plaintext(self):
        for key in ('', 'private-token-not-base64', base64.b64encode(os.urandom(31)).decode(),
                    base64.b64encode(os.urandom(32)).decode()):
            with self.subTest(key_length=len(key)), self.assertRaises(BaselineUnavailable) as caught:
                restore_baseline(self.root, key_value=key)
            self.assertEqual(str(caught.exception), 'authenticated_migration_baseline_unavailable')

    def test_failure_after_plaintext_restore_still_removes_every_temporary_file(self):
        paths = []
        def fail_after_restore(destination, key_path, directory):
            paths.append(key_path.parent)
            restore_all(destination, key_path, directory)
            raise RuntimeError('private-contact-marker private-credential-marker')
        output = io.StringIO()
        with patch('cloud.baseline.restore_all', side_effect=fail_after_restore), redirect_stdout(output), \
             self.assertRaises(BaselineUnavailable) as caught:
            restore_baseline(self.root, key_value=self.encoded_key)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('private-', str(caught.exception))

    def test_partial_empty_or_invalid_restored_exports_are_rejected(self):
        for mutate in (lambda row: row.update(blocked=['partial']),
                       lambda row: row.update(records=[]),
                       lambda row: row['records'][0].update(hash='bad')):
            def incomplete(destination, key_path, directory):
                summary = restore_all(destination, key_path, directory)
                path = destination / 'combined-historical-records.json'
                row = json.loads(path.read_text())
                mutate(row)
                path.write_text(json.dumps(row))
                return summary
            with self.subTest(mutate=mutate), patch('cloud.baseline.restore_all', side_effect=incomplete), \
                 self.assertRaises(BaselineUnavailable):
                restore_baseline(self.root, key_value=self.encoded_key)


if __name__ == '__main__':
    unittest.main()
