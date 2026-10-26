# Orchestrator Failure Artifact Implementation Plan

> **For agentic workers:** Use the assigned implementation agent and root's independent reviewer. Root owns Git operations, deployment and hosted validation; this unit must not dispatch a workflow or mutate operational state.

**Goal:** Retain the orchestrator's existing safe failure JSON at the requested artifact path before its original nonzero exit.

**Architecture:** A shared parser and `_run(args)` preserve `main()`'s raising interface. The actual CLI wrapper adds only best-effort private local diagnostic persistence and the existing safe stdout/exit behavior.

**Tech Stack:** Python standard library, unittest, temporary directories, mocked transports, existing Notion diagnostic sanitizers.

## Global Constraints

- Modify only `cloud/orchestrator.py`, the new test file and these design/plan files.
- Preserve all business flow, migration/reconciliation guards, modes, defaults, budgets and successful output.
- Persist only the existing `_failure_summary(error)` dictionary; never serialize an exception, body, header, credential, contact or message.
- No backup, retry, network/Notion/provider operation or recovery action is added after failure.
- Use private owned output paths, modes 0700/0600 and directory/file `O_NOFOLLOW` checks. Filesystem failure must preserve original diagnostics and exit 1.
- Keep `main()` raising the original operational exception. Preserve successful `runpy` module execution without a new `SystemExit(0)`.

## Task 1: Actual CLI failure retention

**Files:**
- Modify: `cloud/orchestrator.py` (parser/main at the end of the file; operational statements stay unchanged).
- Create: `tests/test_orchestrator_failure_artifact.py`.

**Interfaces:**
- `_argument_parser()` returns the existing `argparse.ArgumentParser` configuration.
- `_run(args)` consumes the parsed namespace and executes the existing main body.
- `main()` calls `_run(_argument_parser().parse_args())` and does not catch its exception.
- `_persist_failure_summary(failure, destination)` writes only the supplied safe dictionary using local owned file/directory descriptors.
- `_cli()` parses once, calls `_run(args)`, and on an exception persists/prints one computed safe dictionary before `SystemExit(1)`.

- [x] Write real `runpy.run_module('cloud.orchestrator', run_name='__main__')` tests in a private temporary working directory. Inject a failure through the existing `cloud.baseline.restore_baseline` boundary, patch backend transport and backup to detect any later call, and test both default and custom output paths.
- [x] Run `/Users/naren/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_orchestrator_failure_artifact -v`. Confirm default/custom retention and permission assertions fail on the original entry point because no file exists.
- [x] Factor parser construction and the existing main body without changing the operational statements. Implement the CLI error path as:

```python
def _cli():
    args = _argument_parser().parse_args()
    try:
        return _run(args)
    except Exception as error:
        failure = _failure_summary(error)
        try:
            _persist_failure_summary(failure, args.output)
        except OSError:
            pass
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(1)
```

- [x] Implement filesystem persistence with descriptor-relative traversal, private owned final parent, `O_NOFOLLOW`, regular/single-link target validation before truncation, 0600 mode and reliable descriptor cleanup. No failure-handling path calls a backend.
- [x] Add adversarial filesystem cases and direct/wrapped Notion private-sentinel cases. Assert exact stdout/file dictionary equality and original exit 1 even when artifact persistence is blocked.
- [x] Assert direct `main()` preserves the original exception object; mock the shared runner to exercise successful CLI return/output behavior without changing it.
- [x] Run the new suite and related existing Notion, application, index, research-fallback and maintenance failure-artifact suites. Run compile and whitespace checks. Inspect the source diff to confirm operational statements are unchanged.
- [x] Freeze owned files and report exact red/green and final results to root for independent review. Do not stage, commit or deploy.

Validation: the original entry point failed the default/custom/wrapped artifact tests because no retained file existed; the first ten-test run had three failures and three expected missing-interface/stale-file errors. The completed new suite contains 12 passing tests; the selected related suites total 80 passing tests. Compile and whitespace checks pass. AST comparison proves the operational body, argument options, `execute`, diagnostics and both activation gates are unchanged. Root's combined full suite, independent review, deployment and hosted artifact validation remain separate required work.
