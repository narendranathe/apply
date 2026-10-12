# Changelog vs master resume — JPMorganChase Data Engineer III (2026-10-12)

Variant: `resumes/variants/2026-10-12-jpmorganchase-data-engineer-iii-pyspark-databricks-mongo-sql-python.tex`
(compiled PDF in this folder is the working copy; the `.tex` is the source of truth)

Mode: 2-page (JD is a senior-IC data engineering role; depth of evidence wins over a 1-page scan)
Gate: PASS — G1 JPMC is a major H-1B sponsor; no "no sponsorship" language in the posting. G3
~5.5 of 6 required must-haves truthfully evidenced (see Map; Mongo working-knowledge is a named
gap). G4 posting surfaced ~10h before capture (jooble, same title/team/location; canonical post
date unverified). G5 tenure bar is "5+ years in Data Engineering" — under the 7+ skip threshold;
role is IC "seasoned member of an agile team", not Staff scope. G6 knockouts answered honestly
in answers.md (H-1B transfer; 3yr DE / 6yr professional vs the 5+ ask; Jersey City relocation).
Arc: B primary (financial-data correctness) + F page 2.
Thesis: Naren ships money-adjacent correctness under external deadlines and formal change control —
the same correctness problem External Reporting and Finance Control data poses.

## Requirement map (must-have -> ground-truth ID -> consequence)
1. 5+ years of experience in Data Engineering and ETL use cases -> E1-CDC (CDC reengineered to
   idempotent incremental merge-upserts; 30 min -> under 8 min), E-SELFHEAL (self-healing recovery
   for SQL Agent jobs), E-LIVEREC (live CDC failure restored in place), M1-M3 (Azure pipelines,
   observability, AKS). Duration qualifier: he has 3 years hands-on DE + 6 years professional;
   tenure is a G5 matter, answered honestly in answers.md — never inflated on the resume.
2. Experience across the data lifecycle -> E1-CDC (ingestion/merge), E-OBS pattern (validation),
   E2-CICD (release: 3 months -> 14 days), E5-RECOVERY (ops: CDC capture restored under 1 hr).
3. Advanced at SQL (joins and aggregations) -> E1-CDC (MERGE upserts), E-PTO (direct SQL
   diagnostics root-caused the defect), E-RETENTION (fn_cdc_get_all_changes recovery), E3-FABRIC
   (OLAP index/stored-procedure tuning, 12s -> under 4s).
4. Working understanding of NoSQL databases such as Mongo -> **GAP, named not papered over.**
   No Mongo in ground truth. Adjacent: pgvector (vector DB) in P-AUTOAPPLY; "working
   understanding" only is the bar, but no Mongo claim appears anywhere on the resume.
5. Significant experience with statistical data analysis -> M4-PAPER (peer-reviewed VADER/RoBERTa
   study, DOI 10.1080/10495142.2025.2525123), M1 (algorithm-per-profile anomaly detection, 95%+),
   M2 (tunable per-service thresholds, signal-to-noise 1:5 -> 1:1.2).
6. Experience customizing changes in a tool to generate product -> E-AICTX (business-context
   harness built for the team's Claude Code agents, hand-annotated business meaning),
   E-REPORTAUTO (Git/Azure DevOps API workflows automated in Python, ~390 hrs/yr).

Nice-to-haves: PySpark -> skills row (PySpark, Spark Structured Streaming); controls/data-quality
mindset -> E-PTO, E-SECURE2, E-OBS patterns; Python -> P-HOOKS/P-FRAUD/P-AUTOAPPLY (shipped,
tested, signed artifacts).
**Gaps (not claimed, not papered over):** Databricks workspace (permanent exclusion — never
claimed; preferred-only), AWS (honest gap; preferred-only), Mongo (required but "working
understanding"; not evidenced), Java (preferred "exposure ... is a plus"; not evidenced).

## Changes vs master

### Summary — REWRITTEN
Was Arc E (AI platform engineer). Now Arc B: money-adjacent correctness, formal change control,
SECURE 2.0, CDC pipelines at 400-DB scale, routing to external reporting and finance control.
398 chars, 3-line band.

### Technical Skills — RESTRUCTURED
Was: AI&ML / Data&Streaming / Cloud&DevOps / Serving&Reliability (Arc E).
Now: Data Engineering (SQL, T-SQL, PySpark, Kafka, Spark Structured Streaming, CDC, ETL, data
modeling, joins and aggregations) / Data Quality & Controls (validation, control-table checks,
alerting, runbooks, incident recovery, change-request review, governance) / Cloud & DevOps /
Python & Analytics (statistical data analysis, LightGBM, MLflow, FastAPI, REST APIs, pgvector).
Maps to the JD's "Advanced at SQL", "ETL", "data lifecycle", "controls", "statistical data
analysis". No Mongo, Databricks, or AWS added (gaps named above).

### ExponentHR — REORDERED + 2 bullets rewritten, arc B evidence first
Order was master/Arc E (E3-FABRIC first). Now: E-PTO -> E-SECURE2 -> E1-CDC -> E-IDEM ->
E5-RECOVERY -> E-LIVEREC -> E-SELFHEAL -> E-RETENTION. Dropped from page 1: E-OBS, E2-CICD,
E-AICTX, E-REPORTAUTO, E-UPTIME, E-DOCS (kept out to hold the Arc B argument; E2-CICD's release
velocity appears via the data-lifecycle map only).
- E-PTO bullet rewritten to lead with "via SQL diagnostics" and "formal change-request review"
  (JD: Advanced at SQL; "supports review of controls"). Consequence unchanged (risk: corrected
  logic shipped once, reviewed, to every client tenant).
- E-SECURE2 bullet rewritten to end on "external regulatory controls" (JD: "review of controls").
- E5-RECOVERY context kept: Contained AAG spelled correctly (never "containerized").

### Missouri S&T — REORDERED (M1 -> M4 -> M2)
M1 first (risk: caught memory leak 4h before outage — controls-adjacent), then M4-PAPER
(peer-reviewed NLP study — maps to "statistical data analysis" must-have), then M2
(alert-threshold tuning — maps to data-quality/controls mindset). Dropped M3 (AKS cost
optimization) — Arc D evidence, off-arc for this JD.

### Zomato — ADDED Z-CAMPAIGN (5th bullet, Arc F page 2)
"200 percent campaign revenue growth with funnel instrumentation and A/B tests..." — needed for
page-2 fill (layout was 46%, now 63.3%). Arc F evidence, unchanged facts.

### C2FO — unchanged (C-SQL, C-PRD)

### Projects — RESTRUCTURED for Arc B + 2 ADDED for fill
Order was Arc E (FinTune, AutoApply, Fraud, repo-context-hooks, JobScout). Now: Real-Time Fraud
Detection first (financial-data correctness: idempotent writes, MLflow tracking, P99 1.12 ms —
headline finance-domain proof), FinTune (PII guardrails, drift monitoring — controls-adjacent),
repo-context-hooks (shipped, signed, 330+ tests — "production-ready, well tested" JD language),
then ADDED AutoApply AI (pgvector vector-DB retrieval — the only NoSQL-adjacent evidence) and
ADDED JobScout (Python/SQLite/GitHub Actions) per fill order. Omitted P-RISK (BLOCKED as written)
and P-RECON (in progress, undefined).

### Education & Certifications — unchanged

## Bolds
Page 1 (5): SQL (JD term), controls (JD term), under 8 minutes (metric), $300K (metric), 95%
(metric). At least one in each of the first two ExponentHR bullets.
Page 2 (5): plus 2 rupees (metric), 80 percent (metric), 1.12 ms (metric), PII guardrails
(controls-adjacent), pgvector (NoSQL-adjacent evidence).
All <=3 words, max 1 per bullet.
QA: LINT PASS, BUDGET PASS, LAYOUT PASS — 2 pages, 100.3%/63.3% fill, 31 bullets, 0 overfull, 1274 words.
