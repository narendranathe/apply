"""Restore the authenticated migration baseline in private ephemeral staging.

The returned export remains private process state. Keys, restored source files,
and their upstream error messages never enter logs or persistent staging.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile

from cloud.migration import SCHEMA
from cloud.records import content_hash, validate_record
from scripts.verify_migration_archive import restore_all


class BaselineUnavailable(RuntimeError):
    pass


def restore_baseline(directory='migration-archives', key_value=None):
    """Return ``(combined_export, content_hash(combined_export))`` after restore.

    ``restore_all`` authenticates every ciphertext and every member against its
    receipt, then rejects conflicting historical identities. TemporaryDirectory
    cleanup runs on both success and failure. No source data is printed here.
    """
    try:
        encoded_key = os.environ.get('MIGRATION_BACKUP_KEY', '') if key_value is None else key_value
        key = base64.b64decode(encoded_key, validate=True)
        if len(key) != 32:
            raise ValueError('Invalid recovery credential')
        with tempfile.TemporaryDirectory(prefix='apply-index-baseline-') as folder:
            root = Path(folder)
            root.chmod(0o700)
            key_path = root / 'recovery-key.bin'
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(key)
            restored = root / 'restored'
            summary = restore_all(restored, key_path, Path(directory))
            if not isinstance(summary, dict) or summary.get('hashes_verified') is not True:
                raise ValueError('Restore was not authenticated')
            exported = json.loads((restored / 'combined-historical-records.json').read_text())
            if (not isinstance(exported, dict) or exported.get('schema') != SCHEMA
                    or exported.get('blocked') != [] or not isinstance(exported.get('records'), list)
                    or not exported['records'] or summary.get('normalized_records') != len(exported['records'])):
                raise ValueError('Incomplete restored baseline')
            identities = set()
            for row in exported['records']:
                canonical = validate_record(row)
                if canonical['id'] in identities:
                    raise ValueError('Duplicate baseline identity')
                identities.add(canonical['id'])
            manifest_hash = content_hash(exported)
        return exported, manifest_hash
    except Exception:
        # Do not propagate filesystem paths, archive member names, private
        # JSON parsing excerpts, recovery credentials or library diagnostics.
        raise BaselineUnavailable('authenticated_migration_baseline_unavailable') from None
