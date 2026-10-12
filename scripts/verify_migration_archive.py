#!/usr/bin/env python3
"""Verify the real encrypted source archive using the runner secret."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.encrypted_backup import restore_archive


def verify(archive, receipt):
    expected = json.loads(Path(receipt).read_text())
    actual = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if actual != expected['archive_sha256']:
        raise ValueError('Ciphertext differs from migration receipt')
    key = base64.b64decode(os.environ.get('MIGRATION_BACKUP_KEY', ''), validate=True)
    if len(key) != 32:
        raise ValueError('Secure runner archive credential is required')
    with tempfile.TemporaryDirectory(prefix='apply-hosted-restore-') as folder:
        root = Path(folder)
        key_path = root / 'recovery-key.bin'
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        result = restore_archive(Path(archive), root / 'restored', key_path=key_path)
        exported = json.loads((root / 'restored/normalized/merged-historical-records.json').read_text())
        if exported.get('blocked'):
            raise ValueError('Archive contains unresolved source conflicts')
        count = len(exported['records'])
    summary = {'archive_sha256': actual, 'restored_files': result['restored_files'], 'hashes_verified': result['hashes_verified'], 'normalized_records': count}
    print(json.dumps(summary, sort_keys=True))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', default='migration-archives/2026-10-11-v2-history.aesgcm')
    p.add_argument('--receipt', default='migration-archives/2026-10-11-v2-history.receipt.json')
    args = p.parse_args()
    verify(args.archive, args.receipt)
