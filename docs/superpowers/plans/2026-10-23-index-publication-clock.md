# Index publication clock implementation

1. Reproduce through the runtime call seam before changing implementation.
   Run `python -m unittest tests.test_index_publication_clock -v`; the official
   observation after run start must fail with `unknown != fresh`.
2. Add the optional publisher clock after the source and previous-manifest
   reads. Validate an aware, nondecreasing sample before building or writing.
   Preserve the existing explicit-time path without sampling a wall clock.
3. Thread the clock through the orchestrator. Select a UTC callable only when
   its logical run time was not explicitly supplied, or honor an explicit
   caller-provided publication clock. Preserve lane and reporting times.
4. Add focused regressions for ordering, invalid/backwards times, genuinely
   future and stale records, duplicate suppression, and historical replay.
   Run the focused suite plus publisher, index, orchestration and research
   tests. Check compilation and whitespace. Independently review the diff.
5. Freeze the local files and report the red/green evidence. Do not stage,
   commit, push, change the frozen main head, or make operational writes.
