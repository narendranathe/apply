"""Read-only historical normalization and idempotent, conflict-safe import.

The source journal remains historical input. This module never replays external
effects, allocates a live claim, or upgrades a historical delivery state.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Iterable, Protocol

SCHEMA = "apply-historical-export/v1"


class RecordStore(Protocol):
    def get(self, record_id: str) -> dict | None: ...
    def put(self, record: dict) -> dict: ...
    def query(self, kind: str | None = None) -> Iterable[dict]: ...


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def precision(value: Any) -> str:
    """Describe the supplied timestamp; never invent a time or timezone."""
    if not isinstance(value, str) or not value:
        return "unknown"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "date"
    fraction = re.search(r"T\d{2}:\d{2}:\d{2}\.(\d+)", value)
    if fraction:
        return f"fractional_seconds:{len(fraction.group(1))}"
    if re.search(r"T\d{2}:\d{2}:\d{2}", value):
        return "second"
    if re.search(r"T\d{2}:\d{2}", value):
        return "minute"
    return "source_text"


def record(record_id: str, kind: str, payload: dict, status: str) -> dict:
    return {"id": record_id, "kind": kind, "payload": payload, "status": status, "hash": digest(payload)}


def event_record(event: dict) -> dict:
    required = ("event_id", "content", "actor", "kind", "occurred_at", "delivery_state")
    if any(not isinstance(event.get(k), str) or not event[k] for k in required):
        raise ValueError("Missing historical event field")
    actual = hashlib.sha256(event["content"].encode("utf-8")).hexdigest()
    if event.get("content_hash") != actual:
        raise ValueError("Historical event content hash mismatch")
    return record(event["event_id"], "outreach_events", event, event["delivery_state"])


def legacy_log_events(text: str) -> list[dict]:
    """Same verbatim legacy IDs as outreach_sync.legacy_events, without writes."""
    text = text.split("<!-- outreach-event:", 1)[0]
    matches = list(re.finditer(r"^#{2,3} (.+)$", text, re.M))
    parent, date, rows = "", "", []
    for i, match in enumerate(matches):
        raw = text[match.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)].rstrip()
        heading = match.group(1)
        if match.group(0).startswith("## "):
            parent = heading
            found = re.match(r"(\w+ \d+, \d{4}):", heading)
            if found:
                date = dt.datetime.strptime(found.group(1), "%B %d, %Y").date().isoformat()
        if not date:
            continue
        profile = re.search(r"^Profile:\s*(https://(?:www\.)?linkedin\.com/in/[^\s/]+/?)", raw, re.M)
        key = profile.group(1).split("?")[0].replace("https://linkedin.com", "https://www.linkedin.com").rstrip("/") + "/" if profile else ""
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        rows.append({"event_id": "legacy-" + sha[:24], "title": heading, "actor": "Historical import",
                     "kind": "Historical record", "occurred_at": date, "content": raw,
                     "contact_key": key, "role_key": "", "intent_id": "",
                     "delivery_state": "Historical observation", "content_hash": sha,
                     "evidence": "Verbatim source log; group: " + parent + ". Imported observation, not a fresh LinkedIn check."})
    return rows


def marked_log_events(text: str) -> list[dict]:
    rows = []
    spans = []
    pattern = r"<!-- outreach-event:([^\n]+?) -->\n(.*?)\n<!-- /outreach-event:\1 -->"
    for match in re.finditer(pattern, text, re.S):
        spans.append((match.start(), match.end()))
        block = match.group(2)
        header, sep, content = block.partition("\n\n")
        # The first blank line follows the title; metadata ends at the next.
        metadata, sep2, body = content.partition("\n\n")
        fields = dict(line.split(": ", 1) for line in metadata.splitlines() if ": " in line)
        title = re.fullmatch(r"## (.*?): (.*)", header)
        if not sep or not sep2 or not title or fields.get("Event ID") != match.group(1):
            raise ValueError("Malformed marked log event")
        rows.append({"event_id": match.group(1), "title": title.group(2), "actor": fields.get("Actor"),
                     "kind": fields.get("Kind"), "occurred_at": title.group(1), "content": body,
                     "delivery_state": fields.get("Delivery state"), "content_hash": fields.get("Content SHA-256")})
    # Correction evidence may contain a verbatim earlier event block. That
    # nested marker is content, not a second source event or an incomplete one.
    if any(not any(start <= marker.start() < end for start, end in spans)
           for marker in re.finditer(r"<!-- outreach-event:", text)):
        raise ValueError("Incomplete marked log event")
    return rows


def export_jobscout_history(path: Path, *, proven_test_identities: set[tuple[str, str, str]] | None = None) -> list[dict]:
    """Import source claims conservatively; a tracker timestamp is not a receipt.

    A fixture may be excluded from the operational application lane only when
    its exact external ID/company/URL is independently demonstrated by source
    tests, supplied explicitly by the caller. It still survives as audit data.
    """
    from cloud.applications import application_key
    result = []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM applications ORDER BY id"):
            raw = dict(row)
            identity = (str(raw.get("external_id")), str(raw.get("company")), str(raw.get("url")))
            is_fixture = identity in (proven_test_identities or set())
            payload = {"source_payload": raw, "source": "jobscout_local_history", "historical_only": True,
                       "source_status": raw.get("status"), "company": raw.get("company"),
                       "role": raw.get("title"), "url": raw.get("url"), "external_id": raw.get("external_id"),
                       "source_row_id": raw.get("id"), "observed_at": raw.get("updated_at"),
                       "receipt_verified": False, "replay_allowed": False}
            key = "legacy-jobscout:" + digest([raw.get("user_id"), raw.get("external_id"), raw.get("id")])[:32]
            if is_fixture:
                payload["proven_fixture_identity"] = list(identity)
                result.append(record(key, "audit_events", payload, "source_test_artifact"))
                continue
            payload["application_id"] = key
            payload["dedup_key"] = application_key(raw.get("company"), url=raw.get("url"), role=raw.get("title"), location=raw.get("location"))
            uncertain_attempt = bool(raw.get("applied_at")) or raw.get("status") in ("applied", "interview", "offer", "rejected")
            status = "submission_unknown" if uncertain_attempt else "historical_observation"
            result.append(record(key, "applications", payload, status))
    return result


def export_history(journal: Path, logs: list[Path], snapshots: list[Path] | None = None,
                   *, jobscout: Path | None = None,
                   proven_test_identities: set[tuple[str, str, str]] | None = None) -> dict:
    records, provenance, blocked = {}, {}, []
    sources = []

    def source(path: Path, classification: str) -> str:
        sha = file_digest(path)
        sources.append({"name": path.name, "source_path": str(path), "classification": classification,
                        "sha256": sha, "bytes": path.stat().st_size})
        return sha

    def add(item: dict, origin: dict) -> None:
        key = item["id"]
        if key in records and records[key] != item:
            blocked.append({"id": key, "reason": "source_id_collision", "source": origin})
            return
        records[key] = item
        provenance.setdefault(key, []).append(origin)

    journal = Path(journal).resolve()
    sha = source(journal, "historical_sqlite")
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Historical SQLite integrity check failed")
        for row in db.execute("SELECT * FROM events ORDER BY rowid"):
            event = json.loads(row["payload"])
            origin = {"source_path": str(journal), "file_sha256": sha, "table": "events",
                      "row_id": row["event_id"], "raw_payload_sha256": hashlib.sha256(row["payload"].encode()).hexdigest(),
                      "created": row["created"], "remote_url": row["remote_url"], "legacy": bool(row["legacy"]),
                      "timestamp_precision": precision(event.get("occurred_at")),
                      "actor_ambiguous": event.get("actor") in (None, "", "Historical import"),
                      "historical_only": True, "replay_allowed": False}
            try:
                if event.get("event_id") != row["event_id"]:
                    raise ValueError("Journal row and payload identity differ")
                add(event_record(event), origin)
            except (ValueError, TypeError) as exc:
                blocked.append({"id": row["event_id"], "reason": str(exc)})
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "work_claims" in tables:
            for row in db.execute("SELECT * FROM work_claims ORDER BY rowid"):
                payload = dict(row)
                # Historical tokens are evidence, never renewed cloud locks.
                key = "legacy-claim:" + payload["token"]
                add(record(key, "audit_events", payload, "historical_" + payload["state"]),
                    {"source_path": str(journal), "table": "work_claims", "historical_only": True,
                     "replay_allowed": False, "was_active": bool(payload["active"])})
    for log in logs:
        log = Path(log).resolve()
        sha = source(log, "historical_outreach_log")
        text = log.read_bytes().decode("utf-8")
        for event in legacy_log_events(text):
            add(event_record(event), {"source_path": str(log), "file_sha256": sha,
                                     "timestamp_precision": "date", "actor_ambiguous": True,
                                     "historical_only": True, "replay_allowed": False})
        try:
            for event in marked_log_events(text):
                key = event["event_id"]
                if key not in records:
                    # Some early logs were embedded verbatim in an immutable
                    # historical-import event. Keep the original marker ID as
                    # a source alias without fabricating a second payload or
                    # an absent original Content SHA-256.
                    containers = [r for r in records.values()
                                  if r["kind"] == "outreach_events"
                                  and "<!-- outreach-event:" + key + " -->" in r["payload"].get("content", "")
                                  and event["content"] in r["payload"].get("content", "")]
                    if containers:
                        for containing in containers:
                            provenance[containing["id"]].append({"source_path": str(log), "file_sha256": sha,
                                "embedded_original_event_id": key, "original_actor": event["actor"],
                                "original_occurred_at": event["occurred_at"],
                                "original_content_hash": event.get("content_hash"),
                                "timestamp_precision": precision(event["occurred_at"]),
                                "historical_only": True, "replay_allowed": False})
                        continue
                    # Marker bodies lack optional journal fields; preserve that uncertainty.
                    add(event_record(event), {"source_path": str(log), "file_sha256": sha,
                        "timestamp_precision": precision(event["occurred_at"]), "optional_fields_unknown": True,
                        "historical_only": True, "replay_allowed": False})
                elif any(records[key]["payload"].get(k) != v for k, v in event.items()):
                    blocked.append({"id": key, "reason": "journal_log_mismatch", "source_path": str(log)})
                else:
                    provenance[key].append({"source_path": str(log), "file_sha256": sha,
                                            "historical_only": True, "replay_allowed": False})
        except (ValueError, TypeError) as exc:
            blocked.append({"source_path": str(log), "reason": str(exc)})
    for path in snapshots or []:
        path = Path(path).resolve()
        sha = source(path, "historical_workflow_snapshot")
        payload = {"source_name": path.name, "source_sha256": sha, "historical_only": True,
                   "content": json.loads(path.read_bytes().decode("utf-8"))}
        add(record("legacy-snapshot:" + sha, "conversation_observations", payload, "historical_observation"),
            {"source_path": str(path), "historical_only": True, "replay_allowed": False})
    if jobscout is not None:
        jobscout = Path(jobscout).resolve()
        sha = source(jobscout, "historical_jobscout_sqlite")
        for item in export_jobscout_history(jobscout, proven_test_identities=proven_test_identities):
            add(item, {"source_path": str(jobscout), "file_sha256": sha, "table": "applications",
                       "historical_only": True, "replay_allowed": False})
    return {"schema": SCHEMA, "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "records": sorted(records.values(), key=lambda row: row["id"]), "provenance": provenance,
            "source_manifest": sources, "blocked": blocked, "historical_only": True, "replay_allowed": False}


def merge_history(local: dict, notion_records: list[dict], notion_events: list[dict]) -> dict:
    """Combine exact historical identities; accept only scoped, fresh proof variants.

    Both source exports must be retained in the encrypted archive. A remote
    variant becomes the merged import payload only when the original explicit
    metadata authorization and exact correction event are freshly present.
    """
    from .legacy.outputs.outreach_sync import METADATA_PAIRS, METADATA_KEYS, notion_page_url
    if local.get("schema") != SCHEMA or local.get("blocked"):
        raise ValueError("Local source conflicts block merge")
    records = {}
    for item in local["records"]:
        if item.get("hash") != digest(item.get("payload")) or item["id"] in records:
            raise ValueError("Invalid or duplicate local identity")
        records[item["id"]] = item
    remote = {}
    for item in notion_events:
        key = item["payload"]["event_id"]
        if key in remote:
            raise ValueError("Duplicate remote event identity")
        event_record(item["payload"])
        remote[key] = item
    exact, added, variants = 0, 0, []
    for key, source in remote.items():
        candidate = event_record(source["payload"])
        previous = records.get(key)
        if previous is None:
            records[key] = candidate
            added += 1
            continue
        if all(canonical(previous[k]) == canonical(candidate[k]) for k in ("id", "kind", "payload", "status", "hash")):
            exact += 1
            continue
        original, incoming = previous["payload"], candidate["payload"]
        changed = set(incoming) - set(original)
        expected_url = METADATA_PAIRS.get(key)
        if (not expected_url or notion_page_url(source["notion_url"]) != expected_url
                or not changed or not changed <= set(METADATA_KEYS)
                or any(not isinstance(incoming[k], str) or not incoming[k] for k in changed)
                or any(k not in incoming or canonical(incoming[k]) != canonical(v) for k, v in original.items())):
            raise ValueError("Unapproved remote payload conflict")
        proofs = []
        for proof_record in local["records"]:
            event = proof_record["payload"]
            if proof_record["kind"] != "outreach_events" or event.get("kind") != "Correction":
                continue
            try:
                proof = json.loads(event["content"])
                time = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            if (not isinstance(proof, dict)
                    or set(proof) != {"schema", "event_id", "notion_url", "local_payload", "notion_payload", "authorization", "reason", "occurred_at"}
                    or event.get("actor") != "Codex" or event.get("delivery_state") != "Not applicable"
                    or time.tzinfo is None or time.date().isoformat() != "2026-10-01"
                    or proof.get("occurred_at") != event.get("occurred_at")
                    or proof.get("schema") != "outreach-metadata-reconciliation/v1"
                    or proof.get("event_id") != key or proof.get("notion_url") != expected_url
                    or not isinstance(proof.get("authorization"), str) or "Naren" not in proof["authorization"]
                    or "October 1" not in proof["authorization"]
                    or not isinstance(proof.get("reason"), str) or not proof["reason"].strip()
                    or canonical(proof.get("local_payload")) != canonical(original)
                    or canonical(proof.get("notion_payload")) != canonical(incoming)):
                continue
            fresh = remote.get(event["event_id"])
            if fresh is not None and canonical(fresh["payload"]) == canonical(event):
                event_record(event)
                proofs.append(event["event_id"])
        if not proofs:
            raise ValueError("Approved metadata proof is missing from fresh remote history")
        variants.append({"id": key, "local_original_payload_hash": previous["hash"],
                         "remote_canonical_payload_hash": candidate["hash"], "notion_url": source["notion_url"],
                         "proof_event_ids": sorted(proofs), "only_approved_additional_metadata": True,
                         "original_local_source_retained": "historical-export.json"})
        records[key] = candidate
    for item in notion_records:
        if item.get("hash") != digest(item.get("payload")):
            raise ValueError("Invalid normalized Notion record hash")
        previous = records.get(item["id"])
        if previous and any(canonical(previous[k]) != canonical(item[k]) for k in ("id", "kind", "payload", "status", "hash")):
            raise ValueError("Cross-source canonical identity conflict")
        records[item["id"]] = item
    return {**local, "records": sorted(records.values(), key=lambda item: item["id"]),
            "metadata_variants": variants, "remote_event_comparison": {"exact": exact,
             "approved_metadata_variants": len(variants), "remote_only_events": added, "unresolved_conflicts": 0},
            "cloud_authoritative": False}


def application_delta(source_files: dict[str, bytes], *, source_commit: str, base_commit: str, previous_records=()) -> dict:
    """Preserve source drafts as historical preparations, never QA-ready packets.

    Exact text files, including local style dependencies, are portable inside
    private packet records. Observed URL IDs are source identities; this does
    not verify an employer opening, source claims, answers, PDF or permission.
    """
    from .applications import application_key, canonical_url, fold_applications
    from .records import application_aliases
    registry_bytes = source_files["applications/registry.json"]
    registry = json.loads(registry_bytes)
    observations = json.loads(source_files.get("audit/identity-observations.json", b"{}"))
    manifest = [{"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for path, body in sorted(source_files.items())]
    manifest_hash = digest(manifest)
    records, provenance = [], {}
    previous = fold_applications([row for row in previous_records if row['kind'] == 'applications'])
    for original_key, original in sorted(registry.items()):
        folder = original["folder"]
        if not folder or "/" in folder or "\\" in folder or folder in (".", ".."):
            raise ValueError("Unsafe source application folder")
        status_path = "applications/" + folder + "/status.json"
        source_status = json.loads(source_files[status_path])
        status_conflict = source_status.get('status') != original.get('status')
        if status_conflict and not (original.get('applied_at') or original.get('status') in {'applied', 'submitting', 'submission_unknown'}):
            raise ValueError("Registry/folder source status conflict")
        url = original.get("url") or original_key
        normalized_url = canonical_url(url)
        parts = urlsplit(normalized_url)
        req = None
        if parts.hostname in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
            found = re.search(r"/jobs/(\d+)$", parts.path)
            req = found.group(1) if found else None
        elif parts.hostname == "higher.gs.com":
            found = re.search(r"/roles/(\d+)$", parts.path)
            req = found.group(1) if found else None
        elif parts.hostname == "jpmorganchase.veteran-hiring.com":
            found = re.search(r"-(\d{8,12})$", parts.path)
            req = found.group(1) if found else None
        key = application_key(original["company"], req, normalized_url, original.get("role"))
        identity = "application:" + digest(key)
        old = previous.get(identity)
        if old and old['payload'].get('source_commit') == source_commit:
            raise ValueError('Source commit already captured; reuse the authenticated existing export')
        raw_state = original["status"]
        status = {"draft_ready": "prepared", "approved": "prepared", "sourced": "discovered"}.get(raw_state, "historical_observation")
        if raw_state in ("applied", "submitting", "submission_unknown") or original.get("applied_at"):
            status = "submission_unknown"
        relevant = [entry for entry in manifest if entry["path"].startswith("applications/" + folder + "/")
                    or entry["path"] == "resumes/variants/" + folder + ".tex"]
        if not relevant:
            raise ValueError("Application has no recoverable source files")
        common = {"application_id": identity, "company": original["company"], "role": original["role"],
                  "requisition_id": req, "requisition_id_source": "observed_source_url_path" if req else "unknown",
                  "url": normalized_url, "url_aliases": sorted({canonical_url(original_key), normalized_url}),
                  "identity_aliases": application_aliases(original["company"], req, normalized_url),
                  "dedup_key": key, "source_commit": source_commit, "source_folder": folder,
                  "source_manifest_sha256": manifest_hash, "source_status": raw_state,
                  "actor": original.get("claimed_by"), "occurred_at": original.get("claimed_at"),
                  "timestamp_precision": precision(original.get("claimed_at")), "historical_only": True,
                  "official_verified": False, "receipt_verified": False, "qa_verified": False,
                  "answers_verified": False, "runtime_fact_lock_reviewed": False, "replay_allowed": False}
        common['source_status_conflict'] = status_conflict
        observed = observations.get(original_key, {})
        mirror = parts.hostname == "jpmorganchase.veteran-hiring.com"
        common.update({"employer_history_unresolved": mirror,
                       "observed_job_id": observed.get("job_id") or req,
                       "observed_requisition_id": observed.get("requisition_id"),
                       "identity_observation": observed, "identity_observation_verified": False})
        if observed.get("requisition_id"):
            common["identity_aliases"] = sorted(set(common["identity_aliases"] +
                application_aliases(original["company"], str(observed["requisition_id"]), normalized_url)))
        files = [{**entry, "content_utf8": source_files[entry["path"]].decode("utf-8")}
                 for entry in relevant]
        packet_payload = {**common, "source_files": files, "source_registry_row": original,
                          "source_status_document": source_status, "ready": False,
                          "source_pdf_present": any(entry["path"].endswith(".pdf") for entry in relevant),
                          "requires_fresh_packet": True}
        packet_id = "application-packet:" + digest(packet_payload)
        packet_record = record(packet_id, "application_packets", packet_payload, "incomplete")
        if len(canonical(packet_record).encode("utf-8")) > 190_000:
            raise ValueError("Recoverable source packet exceeds portable Notion storage limit")
        app_payload = {**common, "revision": old['payload'].get('revision', 0) + 1 if old else 0,
                       "previous_record_id": old['id'] if old else None, "source_registry_key": original_key,
                       "source_registry_row": original, "source_status_document": source_status,
                       "packet": {"record_id": packet_id, "ready": False, "qa_verified": False,
                                  "requires_fresh_packet": True}, "source_files": relevant}
        app_record = record(identity + ":historical-source:" + digest([source_commit, original_key])[:24],
                            "applications", app_payload, status)
        records.extend((app_record, packet_record))
        for item in (app_record, packet_record):
            provenance[item["id"]] = [{"git_commit": source_commit, "registry_path": "applications/registry.json",
                                      "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
                                      "source_folder": folder, "historical_only": True, "replay_allowed": False}]
    fact_patch = source_files.get("audit/ground-truth.diff", b"")
    audit_payload = {"source_commit": source_commit, "base_commit": base_commit,
                     "source_manifest": manifest, "source_manifest_sha256": manifest_hash,
                     "ground_truth_patch_utf8": fact_patch.decode("utf-8"),
                     "runtime_fact_lock_reviewed": False, "historical_only": True, "replay_allowed": False}
    audit_record = record("application-source-delta:" + digest([source_commit, manifest_hash]),
                          "audit_events", audit_payload, "requires_fact_lock_review")
    records.append(audit_record)
    provenance[audit_record["id"]] = [{"git_commit": source_commit, "base_commit": base_commit,
                                      "historical_only": True, "replay_allowed": False}]
    return {"schema": SCHEMA, "records": sorted(records, key=lambda item: item["id"]),
            "provenance": provenance, "source_manifest": manifest, "source_commit": source_commit,
            "base_commit": base_commit, "blocked": [], "historical_only": True,
            "cloud_authoritative": False, "replay_allowed": False}


def combine_exports(exports: list[dict]) -> dict:
    """Fail closed on identities that differ; preserve every input unchanged."""
    records, provenance, manifests = {}, {}, []
    for export in exports:
        if export.get("schema") != SCHEMA or export.get("blocked"):
            raise ValueError("Unsupported/conflicted migration source")
        for item in export["records"]:
            if item.get("hash") != digest(item.get("payload")):
                raise ValueError("Migration input hash mismatch")
            existing = records.get(item["id"])
            if existing and any(canonical(existing[field]) != canonical(item[field]) for field in ("id", "kind", "status", "payload", "hash")):
                raise ValueError("Migration source identity conflict")
            records[item["id"]] = item
        for key, values in export.get("provenance", {}).items():
            provenance.setdefault(key, []).extend(values)
        manifests.extend(export.get("source_manifest", []))
    return {"schema": SCHEMA, "records": sorted(records.values(), key=lambda item: item["id"]),
            "provenance": provenance, "source_manifest": manifests, "blocked": [],
            "historical_only": True, "cloud_authoritative": False, "replay_allowed": False}


def reconcile_records(records: Iterable[dict], store: RecordStore, *, dry_run: bool = True,
                      private_destination: bool = False) -> dict:
    """Never overwrite conflicts; one preflight plus readback, no blind retries."""
    report = {"dry_run": dry_run, "imported": 0, "would_import": 0, "unchanged": 0,
              "conflicted": 0, "blocked": 0, "results": []}
    records = list(records)
    incoming, collisions = {}, set()
    for item in records:
        key = item.get("id")
        core = {field: item.get(field) for field in ("id", "kind", "payload", "status", "hash")}
        if key in incoming and canonical(incoming[key]) != canonical(core):
            collisions.add(key)
        incoming[key] = core
    seen = {}
    for item in records:
        key = item.get("id")
        reason, outcome = None, None
        if key in collisions:
            if key in seen:
                continue
            seen[key] = item
            reason, outcome = "duplicate_source_id_with_different_payload", "conflicted"
        elif not key or item.get("hash") != digest(item.get("payload")) or not item.get("kind") or not item.get("status"):
            reason, outcome = "invalid_record_or_hash", "blocked"
        elif key in seen:
            continue
        else:
            seen[key] = item
            existing = store.get(key)
            if existing is not None:
                if any(existing.get(field) != item.get(field) for field in ("id", "kind", "payload", "status", "hash")):
                    reason, outcome = "existing_record_differs", "conflicted"
                else:
                    outcome = "unchanged"
            elif dry_run:
                outcome = "would_import"
            elif not private_destination:
                reason, outcome = "private_destination_not_verified", "blocked"
            else:
                store.put(item)
                confirmed = store.get(key)
                if confirmed is None or any(confirmed.get(field) != item.get(field) for field in ("id", "kind", "payload", "status", "hash")):
                    reason, outcome = "write_readback_uncertain_no_retry", "blocked"
                else:
                    outcome = "imported"
        report[outcome] += 1
        report["results"].append({"id": key, "outcome": outcome, "reason": reason})
        if reason == 'write_readback_uncertain_no_retry':
            break  # Any storage uncertainty stops every later creation.
    return report
