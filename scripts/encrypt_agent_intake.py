#!/usr/bin/env python3
"""Seal a private outbox envelope using the public deployment key."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.intake_transport import seal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, default=Path('config/agent-intake-public-key.json'))
    args = parser.parse_args()
    sealed = seal(json.loads(args.input.read_text()), json.loads(args.public_key.read_text())['public_key'])
    with args.output.open('x') as stream:
        json.dump({'envelope_ciphertext': json.dumps(sealed, separators=(',', ':'))}, stream)
    print(json.dumps({'sealed': True, 'plaintext_logged': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'error_type': type(error).__name__}))
        raise SystemExit(2)
