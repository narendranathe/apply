import base64
import json
from pathlib import Path
import tempfile
import unittest

from cloud.backup import backup_store, validate_key
from scripts.encrypted_backup import restore_archive


class RuntimeBackupTests(unittest.TestCase):
    def test_missing_key_blocks(self):
        with self.assertRaises(ValueError):
            validate_key('bad')

    def test_full_snapshot_and_human_fields_restore(self):
        key = b'x' * 32
        class Store:
            def snapshot_all(self):
                return {'complete': True, 'pages': [{'manual_properties': {'suppressed': True}, 'body': 'exact private source'}]}
        with tempfile.TemporaryDirectory(prefix='apply-runtime-test-') as folder:
            root = Path(folder)
            receipt = backup_store(Store(), root / 'ciphertext', base64.b64encode(key).decode())
            key_path = root / 'key.bin'
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            result = restore_archive(Path(receipt['path']), root / 'restored', key_path=key_path)
            snapshot = json.loads((root / 'restored/cloud-records.json').read_text())
            self.assertTrue(result['hashes_verified'])
            self.assertEqual(snapshot['snapshot']['pages'][0]['body'], 'exact private source')
            self.assertTrue(snapshot['snapshot']['pages'][0]['manual_properties']['suppressed'])


if __name__ == '__main__':
    unittest.main()
