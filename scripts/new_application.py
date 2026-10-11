#!/usr/bin/env python3
"""new_application.py — thin wrapper: dedup-check then claim in one step.

Usage:
  python3 scripts/new_application.py --company "Acme" --role "AI Engineer" \\
      --url "https://..." --job-id "123" --agent milo

Builds the dedup key as: url if given else company:job_id (normalized).
Refuses to create anything if the key already exists.
"""
import argparse
import re
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--job-id", default="")
    ap.add_argument("--agent", required=True)
    args = ap.parse_args()

    key = args.url.strip()
    if not key:
        key = f"{norm(args.company)}:{norm(args.job_id or args.role)}"

    reg = os.path.join(HERE, "registry.py")
    chk = subprocess.run([sys.executable, reg, "check", "--key", key],
                         capture_output=True, text=True)
    if chk.returncode == 0:
        print(chk.stdout)
        print("STOP — already registered.", file=sys.stderr)
        return 2
    r = subprocess.run([sys.executable, reg, "claim", "--key", key,
                        "--company", args.company, "--role", args.role,
                        "--url", args.url, "--agent", args.agent])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
