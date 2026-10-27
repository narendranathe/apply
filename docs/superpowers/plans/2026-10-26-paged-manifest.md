# Bounded Manifest Codec Implementation Plan

> For agentic workers: the assigned codec agent owns only the new module, its new test file and these two design documents. Root owns integration, Git and deployment.

Goal: store an arbitrarily growing logical index through bounded immutable records while preserving its complete v1 data and existing authority rules.

Architecture: retain the old pure semantic builder and place a storage codec plus lazy ManifestView at the manifest seam. Use the existing excluded index_changes physical family with a distinct page schema. The getter is injected so live Notion, shadow and offline snapshot adapters share the same verification path.

Tech Stack: Python standard library, collections.abc Mapping/Sequence and unittest.

## Global constraints

- Do not edit cloud/agent_index.py or any V3-bound file.
- Do not edit publisher, protocol, dispatcher or worker integration.
- Do not stage, commit, push, dispatch, access credentials or call a provider.
- Inline threshold is 49152 bytes; page bound is 16384 bytes; branch fanout is at most 16.
- Keep physical and logical IDs/hashes distinct; no outer complete=true on a paged root.
- Preserve every logical field, map entry, sequence item and cursor.

## Task 1: Specify the interface and reproduce capacity

- [x] Record pack_manifest and ManifestView contracts in the companion design.
- [x] Add synthetic legacy-builder fixtures and interface tests in tests/test_index_manifest.py.
- [x] Run python -m unittest tests.test_index_manifest -q and observe the missing-module failure before implementation.

The first contract test calls pack_manifest on a one-contact legacy manifest and expects no pages and an unchanged CORE. The capacity regression calls the unchanged builder for 334 contacts, verifies the original manifest exceeds 48 KiB, then expects bounded physical pages and exact materialization.

## Task 2: Implement the pure codec

- [x] Create cloud/index_manifest.py with static error codes, strict descriptors and content-addressed page records.
- [x] Build sorted map and ordered sequence trees bottom-up, with byte and fanout bounds checked before returning.
- [x] Implement Mapping/Sequence views and injected-getter page validation, bounded lookup/window and cursor positioning.
- [x] Materialize to the original canonical v1 record and compare its checksum.
- [x] Run all codec tests, then related existing index/protocol tests without modifying their sources.

## Task 3: Freeze and hand off

- [x] Add tamper, legacy-reader, Unicode, reuse and bounded-read regressions.
- [x] Run compile and whitespace checks and verify the old builder SHA remains 7a63ce93fb1fdeee5c1df41bee9e3fd4c4ad07bec9b36a9bbd9dabfafe9bf6d5.
- [x] Report exact files, signatures, tests and remaining integration gates to root.

Local verification: twenty-two codec tests plus the existing agent-index and agent-protocol suites passed, sixty-eight tests total. Python compilation and whitespace checks passed. The 334-contact frozen-builder fixture was 235048 canonical UTF-8 bytes; packing produced twenty-seven pages, maximum 16346 bytes, and a 10097-byte physical root. No existing tracked module was edited by the codec task, and no Git, provider, credential or cloud operation was performed.

Deployment is prohibited until root has authenticated the failed production snapshot, selected the correction and completed hosted validation.
