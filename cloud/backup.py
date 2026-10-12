"""Authenticated cloud-record recovery artifacts; plaintext remains ephemeral."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from scripts.encrypted_backup import create_backup


def validate_key(key_value=None):
    key = base64.b64decode(key_value or os.environ.get('MIGRATION_BACKUP_KEY', ''), validate=True)
    if len(key) != 32:
        raise ValueError('Backup credential unavailable')
    return key


def backup_store(store, output_dir='run-output', key_value=None):
    key = validate_key(key_value)
    # Include original human-editable pages and immutable Cloud rows.
    data = store.snapshot_all() if hasattr(store, 'snapshot_all') else {'records': store.query()}
    count = len(data.get('pages', data.get('records', [])))
    with tempfile.TemporaryDirectory(prefix='apply-cloud-backup-') as folder:
        root = Path(folder)
        key_path = root / 'archive.key'
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        snapshot = root / 'cloud-records.json'
        snapshot.write_text(json.dumps({'schema': 'apply-cloud-snapshot/v1', 'exported_at': datetime.now(timezone.utc).isoformat(), 'snapshot': data}, ensure_ascii=False, sort_keys=True))
        snapshot.chmod(0o600)
        output = Path(output_dir) / ('cloud-backup-' + os.environ.get('GITHUB_RUN_ID', datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')) + '.aesgcm')
        evidence = create_backup({'cloud-records.json': snapshot}, output, key_path=key_path, private_destination_verified=True)
    return {'records': count, 'sha256': evidence['archive_sha256'], 'restore_verified': evidence['restore_verified'], 'path': str(output)}
