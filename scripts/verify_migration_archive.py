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
from cloud.migration import SCHEMA, combine_exports
from cloud.records import validate_record


def sources(directory=Path('migration-archives')):
    receipts = sorted(Path(directory).glob('*.receipt.json'))
    if not receipts:
        raise ValueError('No authenticated migration receipts available')
    return [(Path(json.loads(path.read_text())['archive']), path) for path in receipts]


def restore_one(archive, receipt, destination, key_path):
    expected = json.loads(Path(receipt).read_text())
    actual = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if actual != expected['archive_sha256']:
        raise ValueError('Ciphertext differs from migration receipt')
    result = restore_archive(Path(archive), destination, key_path=key_path)
    # Support original clear generic member names and opaque delta receipts.
    found = {hashlib.sha256(item['name'].encode()).hexdigest(): (item['sha256'], item['bytes'])
             for item in result['manifest']['files']}
    wanted = {(item.get('member_id') or hashlib.sha256(item['name'].encode()).hexdigest()):
              (item['sha256'], item['bytes']) for item in expected['files']}
    if found != wanted:
        raise ValueError('Authenticated members differ from receipt')
    member = expected.get('normalized_member', 'normalized/merged-historical-records.json')
    exported = json.loads((destination / member).read_text())
    if exported.get('schema') != SCHEMA or exported.get('blocked'):
        raise ValueError('Archive contains unsupported or conflicted source records')
    for item in exported['records']:
        validate_record(item)
    if len(exported['records']) != expected['canonical_records']:
        raise ValueError('Normalized count differs from receipt')
    return exported, {'archive_sha256': actual, 'restored_files': result['restored_files'],
                      'hashes_verified': result['hashes_verified'], 'normalized_records': len(exported['records'])}


def restore_all(destination, key_path, directory=Path('migration-archives')):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    exports, summaries = [], []
    for index, (archive, receipt) in enumerate(sources(directory)):
        exported, summary = restore_one(archive, receipt, destination / f'archive-{index:04d}', key_path)
        exports.append(exported)
        summaries.append(summary)
    merged = combine_exports(exports)
    output = destination / 'combined-historical-records.json'
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(merged, stream, ensure_ascii=False, sort_keys=True)
    return {'archives': summaries, 'normalized_records': len(merged['records']), 'hashes_verified': True}


def verify(archive=None, receipt=None, directory=Path('migration-archives')):
    key = base64.b64decode(os.environ.get('MIGRATION_BACKUP_KEY', ''), validate=True)
    if len(key) != 32:
        raise ValueError('Secure runner archive credential is required')
    with tempfile.TemporaryDirectory(prefix='apply-hosted-restore-') as folder:
        root = Path(folder)
        key_path = root / 'recovery-key.bin'
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(key)
        if archive is not None:
            _, summary = restore_one(Path(archive), Path(receipt), root / 'restored', key_path)
        else:
            summary = restore_all(root / 'restored', key_path, directory)
    print(json.dumps(summary, sort_keys=True))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive')
    p.add_argument('--receipt')
    p.add_argument('--directory', type=Path, default=Path('migration-archives'))
    args = p.parse_args()
    if bool(args.archive) != bool(args.receipt):
        p.error('Explicit verification needs both --archive and --receipt')
    verify(args.archive, args.receipt, args.directory)
