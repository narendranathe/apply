#!/usr/bin/env python3
"""registry.py — the dedup registry for the job application pipeline.

Usage:
  python3 scripts/registry.py check --key <dedup_key>
      Exit 0 + prints record if the key exists (STOP: do not source it again).
      Exit 1 if the key is new (safe to claim).

  python3 scripts/registry.py claim --key <k> --company <c> --role <r> --url <u> --agent <a>
      Register a new role (status=sourced) and scaffold applications/<folder>/. Fails if key exists.

  python3 scripts/registry.py set --key <k> --status <s> [--resume <variant>] [--note <text>]
      Move an application through sourced -> draft_ready -> approved -> applied -> terminal.

  python3 scripts/registry.py list [--status <s>]
      Show registry entries, optionally filtered.

Registry lives at applications/registry.json, keys are dedup keys. Keep it sorted for clean diffs.
"""
import argparse
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_PATH = os.path.join(HERE, "applications", "registry.json")
APPS_DIR = os.path.join(HERE, "applications")

STATUSES = ["sourced", "draft_ready", "approved", "applied",
            "interview", "rejected", "withdrawn", "stale"]


def load():
    if not os.path.exists(REG_PATH):
        return {}
    with open(REG_PATH) as f:
        return json.load(f)


def save(reg):
    os.makedirs(os.path.dirname(REG_PATH), exist_ok=True)
    with open(REG_PATH, "w") as f:
        json.dump(reg, f, indent=2, sort_keys=True)
        f.write("\n")


def slug(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60]


def cmd_check(args):
    reg = load()
    rec = reg.get(args.key)
    if rec:
        print(json.dumps({args.key: rec}, indent=2))
        print("EXISTS — do not source this role again.", file=sys.stderr)
        return 0
    print("NEW — safe to claim.")
    return 1


def cmd_claim(args):
    reg = load()
    if args.key in reg:
        print(f"EXISTS — {args.key} is already registered. Stop.", file=sys.stderr)
        return 2
    folder = f"{date.today().isoformat()}-{slug(args.company)}-{slug(args.role)}"
    folder = os.path.join(APPS_DIR, folder)
    os.makedirs(folder, exist_ok=False)
    # scaffold the application folder
    for name, body in [
        ("jd.md", f"# {args.role} — {args.company}\n\nURL: {args.url}\n\n<!-- paste the JD snapshot here -->\n"),
        ("changelog.md", "# Changelog vs master resume\n\n<!-- every bullet changed/added/removed, with the JD requirement it maps to -->\n"),
        ("answers.md", "# Knockout answers (draft — needs Naren's approval)\n\n<!-- work auth, sponsorship, location, years, comp, posting-specific questions -->\n"),
        ("status.json", json.dumps({"status": "sourced", "claimed_by": args.agent,
                                    "claimed_at": date.today().isoformat()}, indent=2) + "\n"),
    ]:
        with open(os.path.join(folder, name), "w") as f:
            f.write(body)
    reg[args.key] = {
        "company": args.company,
        "role": args.role,
        "url": args.url,
        "folder": os.path.basename(folder),
        "status": "sourced",
        "claimed_by": args.agent,
        "claimed_at": date.today().isoformat(),
        "resume_variant": None,
        "applied_at": None,
        "notes": [],
    }
    save(reg)
    print(f"Claimed {args.key} -> {folder}")
    return 0


def cmd_set(args):
    reg = load()
    rec = reg.get(args.key)
    if not rec:
        print(f"UNKNOWN KEY — {args.key} is not registered.", file=sys.stderr)
        return 2
    if args.status not in STATUSES:
        print(f"BAD STATUS — use one of: {', '.join(STATUSES)}", file=sys.stderr)
        return 2
    rec["status"] = args.status
    if args.resume:
        rec["resume_variant"] = args.resume
    if args.status == "applied" and not rec.get("applied_at"):
        rec["applied_at"] = date.today().isoformat()
    if args.note:
        rec.setdefault("notes", []).append(f"{date.today().isoformat()}: {args.note}")
    # mirror into the folder's status.json
    sj = os.path.join(APPS_DIR, rec["folder"], "status.json")
    if os.path.exists(sj):
        with open(sj) as f:
            s = json.load(f)
        s["status"] = args.status
        with open(sj, "w") as f:
            json.dump(s, f, indent=2)
            f.write("\n")
    save(reg)
    print(f"{args.key} -> {args.status}")
    return 0


def cmd_list(args):
    reg = load()
    for key in sorted(reg):
        rec = reg[key]
        if args.status and rec["status"] != args.status:
            continue
        print(f"{rec['status']:12s} {rec['company']:28.28s} {rec['role'][:50]:50s} {key[:60]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--key", required=True)
    c = sub.add_parser("claim")
    c.add_argument("--key", required=True); c.add_argument("--company", required=True)
    c.add_argument("--role", required=True); c.add_argument("--url", required=True)
    c.add_argument("--agent", required=True)
    c = sub.add_parser("set")
    c.add_argument("--key", required=True); c.add_argument("--status", required=True)
    c.add_argument("--resume", default=None); c.add_argument("--note", default=None)
    c = sub.add_parser("list"); c.add_argument("--status", default=None)
    args = ap.parse_args()
    fn = {"check": cmd_check, "claim": cmd_claim, "set": cmd_set, "list": cmd_list}[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
