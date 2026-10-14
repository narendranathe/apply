#!/usr/bin/env python3
"""Local JSON-lines / MCP stdio adapter; no HTTP listener or external writer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.agent_protocol import AgentProtocol, ProtocolError, SCHEMA
from cloud.notion import NotionStore
from cloud.records import canonical_json, validate_record

ACTION_SCHEMA = {"type": "object", "required": ["type", "entity_id", "idempotency_key", "expected_manifest_id", "expected_projection_hash"],
    "properties": {"type": {"type": "string", "enum": ["research", "prepare_application", "submit_application", "review_outreach", "prepare_interview", "review_referral", "reconcile", "configure_workflow"]},
        **{key: {"type": "string", "minLength": 1, "maxLength": 500} for key in
           ("entity_id", "idempotency_key", "expected_manifest_id", "expected_projection_hash", "task_id")}},
    "additionalProperties": True}
EVENT_SCHEMA = {**ACTION_SCHEMA, "required": ACTION_SCHEMA["required"] + ["occurred_at"],
    "properties": {**ACTION_SCHEMA["properties"], "type": {"type": "string", "enum": ["conversation_observed", "manual_action_observed"]},
                   "occurred_at": {"type": "string", "format": "date-time"}}}
SUPPORTED_MCP_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}

TOOLS = {
    "get_campaign_brief": {},
    "get_ready_tasks": {"agent": {"type": "string"}, "capabilities": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
    "get_entity_context": {"entity_id": {"type": "string"}, "depth": {"type": "integer", "enum": [1, 2, 3]}, "task_id": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 5}},
    "get_recent_changes": {"since_cursor": {"type": ["string", "null"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
    "get_interaction_history": {"contact_id": {"type": "string"}},
    "get_application_state": {"job_id": {"type": "string"}},
    "propose_action": {"action": ACTION_SCHEMA, "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}},
    "get_action_result": {"action_id": {"type": "string"}},
    "report_observed_outcome": {"event": EVENT_SCHEMA, "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}},
}
REQUIRED = {"get_entity_context": ["entity_id"], "get_interaction_history": ["contact_id"],
    "get_application_state": ["job_id"], "propose_action": ["action", "evidence"],
    "get_action_result": ["action_id"], "report_observed_outcome": ["event", "evidence"]}


def tool_definitions():
    return [{"name": name, "description": "Apply agent protocol v1: " + name,
             "inputSchema": {"type": "object", "properties": properties,
                             "required": REQUIRED.get(name, []), "additionalProperties": False}}
            for name, properties in TOOLS.items()]


def load_registry(path):
    """The deployment-owned local launcher binds one principal, not a request."""
    if not path:
        return {}, "reader"
    target = Path(path)
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ProtocolError("trusted_registry_requires_private_owned_file")
    data = json.loads(target.read_text())
    if (not isinstance(data, dict) or data.get("schema") != "apply-agent-registry/v1"
        or not isinstance(data.get("agents"), dict) or data.get("principal") not in data["agents"]):
        raise ProtocolError("trusted_registry_invalid")
    # Local file ownership is the trust boundary; remote deployments must bind
    # identities using the authenticated host/GitHub actor, never this label.
    return data["agents"], data["principal"]


def _private_outbox(path):
    """Explicit staging is confined to private ephemeral storage, never a repo."""
    if not path:
        return None
    target = Path(path).absolute()
    if target.is_symlink():
        raise ProtocolError("outbox_requires_private_temporary_directory")
    resolved = target.resolve()
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/private/tmp").resolve()}
    if not any(resolved != root and root in resolved.parents for root in roots):
        raise ProtocolError("outbox_requires_private_temporary_directory")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = resolved.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ProtocolError("outbox_requires_private_temporary_directory")
    return resolved


def stage_intake(envelope, outbox):
    """Atomic immutable staging; repeated intent reuses bytes, conflicts stop."""
    if outbox is None:
        return False
    record = validate_record(envelope)
    name = record["id"].split(":")[-1] + ".json"
    path = outbox / name
    data = (canonical_json(record) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ProtocolError("outbox_idempotency_conflict") from None
        return True
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # A partial file is a durable conflict on retry, never an executable
        # valid envelope. Dispatcher must validate hash/schema before intake.
        raise ProtocolError("outbox_write_unconfirmed") from None
    return True


class Gateway:
    def __init__(self, protocol, *, outbox=None):
        self.protocol, self.outbox = protocol, _private_outbox(outbox)
        self.initialized = False
        self.negotiated = False

    def call(self, name, arguments):
        if name not in TOOLS or not isinstance(arguments, dict) or set(arguments) - set(TOOLS[name]):
            raise ProtocolError("invalid_operation_or_arguments")
        if any(key not in arguments for key in REQUIRED.get(name, [])):
            raise ProtocolError("required_argument_missing")
        result = getattr(self.protocol, name)(**arguments)
        if "envelope" in result["data"]:
            envelope = result["data"].pop("envelope")
            result["data"]["intake_hash"] = envelope["hash"]
            result["data"]["staged"] = stage_intake(envelope, self.outbox)
            result["data"]["acknowledged"] = False
            # Staging/printing an action ID cannot establish Notion persistence.
        return result

    def handle(self, request):
        if not isinstance(request, dict):
            raise ProtocolError("invalid_request")
        mcp = request.get("jsonrpc") == "2.0"
        if mcp and "id" not in request:
            if request.get("method") == "notifications/initialized" and self.negotiated:
                self.initialized = True
            return None  # MCP notifications need no stdout response.
        try:
            if mcp:
                method = request.get("method")
                if method == "initialize":
                    requested = request.get("params", {}).get("protocolVersion")
                    version = requested if requested in SUPPORTED_MCP_VERSIONS else "2025-11-25"
                    self.negotiated = True
                    result = {"protocolVersion": version, "capabilities": {"tools": {}},
                              "serverInfo": {"name": "apply-agent-gateway", "version": "1.0.0"}}
                elif method != "ping" and not self.initialized:
                    raise ProtocolError("mcp_initialization_required")
                elif method == "tools/list":
                    result = {"tools": tool_definitions()}
                elif method == "ping":
                    result = {}
                elif method == "tools/call":
                    params = request.get("params", {})
                    value = self.call(params.get("name"), params.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": canonical_json(value)}], "isError": False}
                else:
                    raise ProtocolError("unsupported_rpc_method")
                return {"jsonrpc": "2.0", "id": request["id"], "result": result}
            return self.call(request.get("operation"), request.get("arguments", {}))
        except ProtocolError as exc:
            if mcp:
                return {"jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32000, "message": exc.code}}
            return {"schema": SCHEMA, "error": {"code": exc.code}}
        except Exception:
            # Never include source payloads, input lines, private paths, tokens,
            # traceback locals or HTTP response bodies in stdout/stderr.
            if mcp:
                return {"jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32000, "message": "request_failed"}}
            return {"schema": SCHEMA, "error": {"code": "request_failed"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-file", help="Private deployment-owned principal/capability configuration")
    parser.add_argument("--manifest-id", help="Exact immutable index manifest ID")
    parser.add_argument("--private-outbox", help="Explicit private directory below system temporary storage")
    args = parser.parse_args(argv)
    try:
        registry, principal = load_registry(args.registry_file)
        protocol = AgentProtocol(NotionStore.from_env(), registry=registry, principal=principal, manifest_id=args.manifest_id)
        gateway = Gateway(protocol, outbox=args.private_outbox)
        while True:
            line = sys.stdin.buffer.readline(128001)
            if not line:
                break
            if len(line) > 128000:
                raise ProtocolError("request_too_large")
            try:
                request = json.loads(line)
                response = gateway.handle(request)
            except (ValueError, UnicodeError):
                response = {"schema": SCHEMA, "error": {"code": "invalid_json"}}
            if response is not None:
                print(canonical_json(response), flush=True)
        return 0
    except ProtocolError as exc:
        print(canonical_json({"schema": SCHEMA, "error": {"code": exc.code}}), flush=True)
        return 1
    except Exception:
        print(canonical_json({"schema": SCHEMA, "error": {"code": "gateway_unavailable"}}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
