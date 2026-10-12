#!/usr/bin/env python3
"""Read the authoritative Notion application history; JSON is an export view.

check exits 0 for a duplicate, 1 only for a verified-new key, 2 for unavailable
or ambiguous state. claim/set intentionally refuse competing local mutations.
Import existing JSON through the centrally serialized migration workflow.
"""
import argparse
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloud.applications import canonical_url, fold_applications
from cloud.preparation import digest
from cloud.notion import NotionStore

REG_PATH = ROOT / "applications/registry.json"


def compatibility_view(store):
    return {record["payload"].get("dedup_key", key): {**record["payload"], "status": record["status"]}
            for key, record in fold_applications(store.query(kind="applications")).items()}


def key_matches(key, record):
    return key == record.get("dedup_key") or canonical_url(key) in {
        canonical_url(url) for url in [record.get("url"), record.get("posting_url"), *record.get("url_aliases", [])] if url}


def main(argv=None, *, store=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--key", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--status")
    exporting = sub.add_parser("export")
    exporting.add_argument("--output", required=True)
    packet = sub.add_parser("packet", help="Restore exact private packet from Notion for review/manual action")
    packet.add_argument("--application-id", required=True)
    packet.add_argument("--output", required=True)
    # Parse historical invocations and fail with a clear cutover instruction.
    for command in ("claim", "set"):
        old = sub.add_parser(command)
        for option in ("key", "company", "role", "url", "agent", "status", "resume", "note"):
            old.add_argument("--" + option)
    args = parser.parse_args(argv)
    if args.command in {"claim", "set"}:
        print("BLOCKED: local registry mutations are retired. Use the central cloud application/migration lane; Notion is authoritative.", file=sys.stderr)
        return 3
    try:
        authoritative = store or NotionStore()
        if args.command == "packet":
            application = fold_applications(authoritative.query(kind="applications")).get(args.application_id)
            reference = (application or {}).get("payload", {}).get("packet", {})
            material = authoritative.get(reference.get("record_id", ""))
            if not material:
                raise ValueError("recoverable_packet_unavailable")
            payload = material["payload"]
            destination = Path(args.output)
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            files = {"jd.md": payload["jd"].encode(),
                     "answers.json": (json.dumps({"fields": payload["answers"], "evidence": payload["answer_evidence"], "missing": payload["missing"]}, indent=2) + "\n").encode(),
                     "changelog.md": ("# Changelog vs master\n\n" + json.dumps(payload["changelog"], indent=2) + "\n").encode()}
            if payload.get("resume_source"):
                files["resume.tex"] = payload["resume_source"].encode()
                if digest(files["resume.tex"]) != payload["source_sha256"]:
                    raise ValueError("source_hash_mismatch")
                files["v8preamble.tex"] = (ROOT / "resumes/master/v8preamble.tex").read_bytes()
            if payload.get("resume_pdf_base64"):
                files["resume.pdf"] = base64.b64decode(payload["resume_pdf_base64"], validate=True)
                if digest(files["resume.pdf"]) != payload["resume_sha256"]:
                    raise ValueError("pdf_hash_mismatch")
            if digest(payload["answers"]) != payload["answers_hash"]:
                raise ValueError("answers_hash_mismatch")
            for name, content in files.items():
                target = destination / name
                target.write_bytes(content)
                target.chmod(0o600)
            print("Restored private packet with verified hashes; do not commit these files.")
            return 0
        view = compatibility_view(authoritative)
        if args.command == "check":
            matches = [record for key, record in view.items() if key == args.key or key_matches(args.key, record)]
            if matches:
                print("EXISTS: stop; canonical application history already contains this key.")
                return 0
            print("NEW: no match in current authoritative Notion state. Only the central writer may claim it.")
            return 1
        if args.command == "export":
            destination = Path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(view, indent=2, sort_keys=True) + "\n")
            print("Exported compatibility view; do not commit plaintext application records.")
            return 0
        for key, record in sorted(view.items()):
            if not args.status or record["status"] == args.status:
                print(f"{record['status']:22s} {key}")
        return 0
    except Exception:
        print("BLOCKED: authoritative application history unavailable or ambiguous. No new-key or submission authorization inferred.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
