# Manifest Storage Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish large frozen semantic indexes within existing Notion limits and read selected paged data without weakening authority checks.

**Architecture:** Keep semantic construction frozen and integrate an independently tested immutable storage codec at the publisher/reader seam. Preflight every physical write before publication, publish root last, and use a fresh lazy payload view for bounded reads and intake validation.

**Tech Stack:** Python standard library, unittest, existing immutable record and Notion helpers.

## Global Constraints

- Only `cloud/index_publisher.py`, `cloud/agent_protocol.py`, new integration tests and this design/plan belong to this unit.
- Keep `cloud/agent_index.py`, the five V3 files and Notion limits byte exact.
- No Git, hosted writes, configuration changes, credentials, provider effects or dispatches from this worker.
- Existing fresh readback, no-write-replay, context budgets, grants and first-40 task window remain unchanged.

---

### Task 1: Reproduce capacity failure and integrate publication

**Files:** Modify `cloud/index_publisher.py`; create `tests/test_paged_index_integration.py`.

**Interfaces:** Consume `pack_manifest(record)` and `ManifestView(record, getter).materialize()`. Preserve `publish_index` arguments and result fields; summaries identify physical root and physical write count.

- [ ] Write regression with 400 actual-builder contacts and actual frozen Notion serialization; verify current oversized root fails and prefix preflight is absent.
- [ ] Run targeted tests and record the expected failures.
- [ ] Hydrate previous roots before publication clock, pack the semantic manifest, preflight all exact requests, then use unchanged verified puts in semantic/page/root order.
- [ ] Differential-test request representation and capacity boundaries against actual NotionStore.put, including Unicode and store-wrapper parent IDs.
- [ ] Verify small v1 output and semantic COREs remain unchanged; missing prior page prevents clock/write.

### Task 2: Integrate bounded protocol reads and fresh authority validation

**Files:** Modify `cloud/agent_protocol.py`; extend `tests/test_paged_index_integration.py`.

**Interfaces:** Keep `_manifest()` a raw canonical record. Internal payload accessor returns ManifestView. Use `change_window(since_cursor, limit)` and Mapping guards; translate static codec errors at protocol boundaries.

- [ ] Add red regressions for paged healthy briefs, bounded projection/tasks/change windows and task evidence selection.
- [ ] Add red regression warming a page cache, removing/tampering a selected page, then proving fresh proposal/dispatcher validation stops before writes.
- [ ] Implement separate view cache, reset it on every fresh root read, and route lazy reads through counted `_get`.
- [ ] Preserve legacy fixtures, response shapes, byte budgets, grants and direct queued ACK lookup.
- [ ] Run new integration tests plus index, publisher, protocol, dispatcher, worker, Notion and clock regression tests.

### Task 3: Freeze for root review

- [ ] Compile changed Python files and check whitespace.
- [ ] Verify frozen semantic/V3 files have no diff from this unit.
- [ ] Report owned files, exact local test counts, source hashes and remaining root-owned deployment work; do not commit or deploy from this worker.
