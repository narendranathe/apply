#!/usr/bin/env python3
"""Export historical sources privately; reconcile read-only unless --write."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.migration import SCHEMA, export_history, merge_history, reconcile_records


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_private_destination() -> bool:
    """Check actual destination visibility, never trust a workflow Boolean alone."""
    repository, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
        return False
    request = Request("https://api.github.com/repos/" + repository,
                      headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        result = json.load(response)
    return result.get("private") is True and result.get("full_name", "").casefold() == repository.casefold()


def main(argv=None, *, store=None, private_verifier=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", type=Path)
    p.add_argument("--log", type=Path, action="append", default=[])
    p.add_argument("--snapshot", type=Path, action="append", default=[])
    p.add_argument("--output", type=Path)
    p.add_argument("--input", type=Path)
    p.add_argument("--merge-notion-records", type=Path)
    p.add_argument("--merge-notion-events", type=Path)
    p.add_argument("--write", action="store_true")
    p.add_argument("--private-destination-verified", action="store_true")
    args = p.parse_args(argv)
    if args.journal:
        if not args.output:
            p.error("--output in an ignored private directory is required")
        if ".migration-private" not in args.output.resolve().parts:
            p.error("Raw historical exports must be inside .migration-private")
        export = export_history(args.journal, args.log, args.snapshot)
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(export, stream, ensure_ascii=False, sort_keys=True)
        print(json.dumps({"records": len(export["records"]), "blocked": len(export["blocked"]),
                          "source_files": len(export["source_manifest"]), "raw_export": "private_local_only"}))
    elif args.input:
        export = json.loads(args.input.read_text())
        if export.get("schema") != SCHEMA:
            p.error("Unsupported historical export")
        if args.merge_notion_records or args.merge_notion_events:
            if not args.merge_notion_records or not args.merge_notion_events or not args.output:
                p.error("Merge requires both Notion exports and --output")
            if args.write:
                p.error("Merge and operational write are separate reviewable steps")
            if ".migration-private" not in args.output.resolve().parts:
                p.error("Raw merged exports must be inside .migration-private")
            export = merge_history(export, json.loads(args.merge_notion_records.read_text()),
                                   json.loads(args.merge_notion_events.read_text()))
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(export, stream, ensure_ascii=False, sort_keys=True)
            print(json.dumps({"records": len(export["records"]), "blocked": len(export["blocked"]),
                              **export["remote_event_comparison"]}, sort_keys=True))
            return 0
        if export.get("blocked") and args.write:
            p.error("Source conflicts must be reconciled before import")
        # Import late so source export works without cloud credentials/dependencies.
        from cloud.notion import NotionStore
        if store is None and not os.environ.get("NOTION_TOKEN"):
            print(json.dumps({"status": "blocked", "reason": "notion_runner_credential_missing"}))
            return 2
        try:
            store = store or NotionStore.from_env()
            private_verified = False
            if args.write:
                private_verified = args.private_destination_verified and (private_verifier or github_private_destination)()
                if not private_verified:
                    print(json.dumps({"status": "blocked", "reason": "github_private_destination_unverified"}))
                    return 2
            report = reconcile_records(export["records"], store, dry_run=not args.write,
                                       private_destination=private_verified)
        except Exception as error:
            print(json.dumps({"status": "blocked", "reason": "history_reconciliation_failed",
                              "error_type": type(error).__name__}))
            return 2
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, sort_keys=True))
    else:
        p.error("Specify --journal or --input")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
