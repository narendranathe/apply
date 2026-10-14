# Changelog vs master resume — Coinbase Senior Software Engineer, Data Platform (2026-10-14)

**Arc:** D primary (velocity/platform — the JD is about foundational data services, self-service
tooling, and observability) + Arc F page 2.
**Thesis:** builds the foundational data services other teams build on — idempotent pipelines,
CI/CD, self-serve observability — and raises the whole team's shipping rate, not just his own.
**Gate:** PASS with flags (G1 Coinbase H-1B filer; G3 ~75-80%, Airflow unclaimed, access-control
partial; G4 0.7d fresh; G5 6 yrs vs 5 asked).

## Summary (rewritten, Arc D)

- Headline carries "Senior Data Engineer" (target title, per his rule) — title itself is
  HR-verifiable and untouched elsewhere.
- Maps to JD: "foundational data services", "self-serve observability", the release-velocity ask.
- 400 chars, 3 lines.

## Skills (4 rows re-framed for the platform JD)

- **Row 1 Data Platforms:** Python, SQL, T-SQL, Kafka, PySpark, Spark Structured Streaming, CDC,
  idempotent pipelines, data modeling, data validation. Maps to: Python-or-Java backend,
  batch (Spark, SQL), streaming (Kafka). PySpark/Spark Structured Streaming mirror master's
  vetted rows; Airflow deliberately absent.
- **Row 2 Delivery & DevOps:** Azure DevOps, CI/CD, Docker, Kubernetes, AKS, GitHub Actions,
  Prometheus, Grafana, Microsoft Fabric, release pipelines. Maps to: orchestration-adjacent
  tooling, monitoring.
- **Row 3 Reliability:** monitoring, runbooks, incident recovery, self-healing, data quality,
  observability, anomaly detection, alert tuning, MTTR reduction. Maps to: "manage, monitor,
  and troubleshoot", data security and observability.
- **Row 4 AI & Tooling:** FastAPI, REST APIs, LLM APIs, RAG, pgvector, MLflow, LightGBM,
  Delta Lake, PyTorch, drift monitoring, model serving. Maps to: "utilizes generative AI
  responsibly" + FastAPI serving evidence.

## Experience (order verified strict reverse-chronological in .tex AND compiled PDF text)

### ExponentHR — reordered to lead with platform/DevEx evidence; context line adds "data platform"
- **E2-CICD moved to bullet 1** (was 5th): release compression 3 months to 14 days, ~11 weeks
  idle time removed per release. Maps to: CI/CD ownership, platform productivity.
- **E-IDEM moved to bullet 2** (was 4th): idempotent copy/restore, $300K/yr, 5,000 eng-hrs.
  Maps to: reusable, reliable data services.
- **E-OBS (bullet 3):** "self-service visibility" phrasing mirrors JD's self-service
  applications; control-table validation turns silent drift into alertable failures.
  Maps to: data quality tracking, observability.
- **E-SELFHEAL (bullet 4):** monitoring + self-healing for SQL Agent jobs. Maps to: monitoring,
  troubleshooting, proactive mitigation.
- **E1-CDC (bullet 5):** incremental merge-upserts, 30 min to under 8 min, compute -67%.
  Maps to: batch processing, scalable pipelines.
- **E5-RECOVERY (bullet 6):** runbook-driven AAG failover recovery, MTTR under 1 hr.
  Maps to: operational reliability of data services.
- **E-REPORTAUTO (bullet 7):** Python Git/Azure DevOps API workflows, 390 hrs/yr. Maps to:
  internal tooling, Python backend.
- **E-AICTX (bullet 8, replaces E3-FABRIC here):** LLM coding-agent context harness, schema
  metadata + hand annotations, safer SQL joins. Maps to: generative AI responsibly.
- **E3-FABRIC removed from page 1** (Arc D keeps page 1 tight; $243K ticket story lives on
  page 2 of other variants, not lost).

### Missouri S&T — Arc D order (observability first)
- M2 (alert tuning, 1:5 to 1:1.2) first: direct map to observability/quality monitoring.
- M3 (AKS migration, $3,200/mo) second: scale-out infra patterns.
- M1 (anomaly detector, 95%) third: production monitoring.
- **NLP paper bullet removed from page 1** (DOI still in Education; AI flavor carried by
  E-AICTX + P-FRAUD + skills row 4).

### C2FO — unchanged (2 bullets).

### Udaan — kept 4: GMV, forecasting/Power BI, BCP+RCA bridge, hired-and-managed team.
No platform-content edits; Arc F translator page unchanged in shape.

### Zomato — trimmed to 4 (from 6): Z-COMP, Z-ES (Elasticsearch bold), Z-CAMPAIGN, Z-PNL.
Removed Z-DASH and Z-SEARCH to fit Arc D page budget; pivot story intact.

## Projects (4; FinTune dropped for Arc D focus)
- **P-HOOKS first** (was 4th): verifiable Python package — answers "is the Python real?"
  (330+ tests, PyPI, Sigstore). Maps to: Python backend services at scale.
- **P-AUTOAPPLY second:** FastAPI + 355 tests + six-provider LLM routing. Maps to: internal
  tooling, generative AI responsibly.
- **P-FRAUD third:** Kafka, FastAPI, MLflow, Prometheus/Grafana, idempotent writes
  (README-corrected wording kept), P99 1.12 ms. Maps to: streaming, observability.
- **P-JOBSCOUT fourth:** TF-IDF matching, circuit breakers. Trimmed to 1 bullet (master shape).
- **FinTune removed** (AI-platform flavor; Arc D keeps the AI story in skills + E-AICTX).

## Honesty notes for Naren
- **Airflow is NOT claimed anywhere** (P-FRAUD `airflow/` is empty scaffolding per
  ground-truth honesty gate); SQL Agent job scheduling is the evidenced orchestrator.
- Access-control specifics (JD's access-controls line) are a named gap; observability and
  data-quality monitoring are the covered halves.
- Bold audit: page 1 = 8 (14 days, $300K, self-service, 8 minutes, 1:1.2, $3,200, 95%,
  summary title); page 2 = 4 (Elasticsearch, 330+ passing tests, pgvector, 1.12 ms).
  Max 1 per bullet, max 3 words each; first two ExponentHR bullets carry bolds.

## QA
- **LINT:** PASS (no live `% $ _ & #`)
- **BUDGET:** PASS (all bullets 105-129 or 200-245; summary 400; all skill rows 105-132;
  all context lines 105-129)
- **ORDER:** PASS (`scripts/resume_qa.py order` on .tex; verified in PDF text extraction too)
- **BUILD:** 2 pages, 0 overfull boxes; page 2 fill ~61% (33 vs 54 nonblank lines)
