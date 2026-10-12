# Preserved local workflow primitives

These are recovery and provenance copies of the version 4 local coordination
implementation observed on October 11, 2026. They are not a second cloud writer
and are not enabled by the cloud scheduler. `PROVENANCE.json` records each source
and ported SHA-256, including the one safe default-path change to the local
mirror. The original full policies/runbooks are retained in the encrypted source
archive; no live personal logs or SQLite state belong in this directory.

`outputs/` preserves the deterministic research planner, local SQLite queue,
immutable ledger/mirror, and read-only public ATS scanner. The source ATS scanner
is kept for recovery; production discovery reuses JobScout. The policy snapshot
omits private alumni facts and retains old application approval behavior solely
as historical configuration. The V2 standing application authorization and new
Notion/GitHub single-writer architecture govern live execution.

The queue and mirror require a disposable state directory when used in recovery.
Never point them at the original live source during cloud execution. Never use
their local SQLite claims as distributed Notion locks. No script in this port
performs LinkedIn sending or application submission.

Run the preserved synthetic tests:

```sh
python3 -m unittest discover -s cloud/legacy/work -p 'test_*.py' -q
```

October 11 local result: **85 tests passed**. No network calls or external sends
are needed by this suite.
