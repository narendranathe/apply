#!/usr/bin/env python3
"""Reject unsafe tracked files/content; emit filenames, never matching secrets."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(rb'(?:ghp_|github_pat_|ntn_|sk-proj-|secret_)[A-Za-z0-9_\-]{24,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'(?i)(?:access_token|refresh_token|api_key|client_secret|notion_token|migration_backup_key|github_token|gh_token)["\x27]?\s*[=:]\s*["\x27]?[A-Za-z0-9_+/=\-]{24,}'),
]


def unsafe(path, data):
    name = path.lower()
    if any(x in name for x in ('.migration-private/', 'outreach_log', 'cookies', 'browser-profile')):
        return True
    if name.endswith(('.sqlite', '.sqlite3', '.db', '.agekey', '.pem', '.key', '.env')):
        return True
    if Path(name).name.startswith('.env.') and not name.endswith('.env.example'):
        return True
    if name.startswith(('applications/', 'resumes/variants/')):
        return True
    return data.startswith(b'SQLite format 3') or any(p.search(data) for p in PATTERNS)


def main():
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    errors = []
    for path in filter(None, paths):
        absolute = ROOT / path
        if absolute.exists() and unsafe(path, absolute.read_bytes()):
            errors.append(path)
    if errors:
        print('Unsafe tracked content: ' + ', '.join(errors))
        return 1
    print(f'PASS: inspected {len(list(filter(None, paths)))} tracked paths; no forbidden plaintext state or detected credentials')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
