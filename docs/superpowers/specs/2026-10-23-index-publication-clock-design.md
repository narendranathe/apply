# Index publication clock

Actual authenticated research run 39809642771 froze the index time at
16:39:11.546727 UTC on October 22. Three official job observations were made
556–561 seconds later during that operation. Their projections therefore had
unknown freshness even though those observations preceded publication. Each
also retained independent ambiguous-history suppression; correcting the clock
does not make those jobs eligible.

The minimized regression follows `execute` through `_publish_agent_index` and
`publish_index`, stages an official observation after run start, and expects
freshness at a later trusted publication time. It fails on the current code
with `unknown != fresh`.

The selected design adds an optional keyword-only `publication_clock` callable.
The publisher samples it once after fresh source and previous-manifest reads,
before constructing any generated record. The sample must be an aware datetime
at or after the supplied logical run time and any previous manifest time;
invalid or backwards samples stop before generated writes. The sample is used
for index construction and appears only in `manifest.payload.generated_at`.

Runtime `execute(now=None)` supplies the UTC clock automatically. Explicit
`execute(now=...)`, `_publish_agent_index(..., now, ...)`, and
`publish_index(..., now)` remain deterministic unless a publication clock is
explicitly provided. Slot, worker, lane and reporting times keep their existing
logical run time. The pure historical index constructor is unchanged.

Sampling in the orchestrator before source reads would still misdate records
observed during those reads. Advancing time to the largest source timestamp
would turn genuine future or skewed observations into trusted current state.
Neither alternative is used. Freshness rules, duplicate suppression,
reconciliation authority, immutable writes and independent readback remain
unchanged.

Verification covers the complete runtime seam, sampling after both reads,
genuine future and stale observations, independent uncertain-application
suppression, invalid/backwards clocks, no generated writes after a bad clock,
and exact deterministic historical construction. The change stays local while
the central recovery head is frozen; no hosted publication or activation is
authorized by these tests.
