#!/usr/bin/env python3
"""Keep an archive key in macOS Keychain and GitHub secrets; never print it."""
import argparse
import base64
from pathlib import Path
import subprocess
from macos_keychain import read_secret, add_secret

SERVICE = 'narendranathe.apply.cloud-migration.v2'
ACCOUNT = 'migration-backup-key'


def secure_key(key_path, repository, github=False):
    key_path = Path(key_path)
    if key_path.stat().st_mode & 0o077:
        raise ValueError('External key must be mode 0600')
    key = key_path.read_bytes()
    if len(key) != 32:
        raise ValueError('Expected 32-byte AES-256 key')
    encoded = base64.b64encode(key)
    existing = read_secret(SERVICE, ACCOUNT)
    if existing is not None:
        if existing != encoded:
            raise ValueError('Existing secure-store key differs; never replace historical custody')
    else:
        add_secret(SERVICE, ACCOUNT, encoded)
    readback = read_secret(SERVICE, ACCOUNT)
    if readback != encoded:
        raise RuntimeError('Keychain custody readback failed')
    if github:
        result = subprocess.run(['gh', 'secret', 'set', 'MIGRATION_BACKUP_KEY', '--repo', repository], input=encoded, capture_output=True)
        if result.returncode:
            raise RuntimeError('Actions backup key secret setup failed')
    print('PASS: external secure-store key verified' + (' and Actions backup secret configured' if github else ''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', required=True)
    parser.add_argument('--repository', default='narendranathe/apply')
    parser.add_argument('--github', action='store_true')
    args = parser.parse_args()
    secure_key(args.key, args.repository, args.github)
