# Changelog vs master resume — Google Analytics Engineer, Austin (2026-10-14)

**Arc:** F primary (promoted for analytics engineering per ARCS.md) + Arc F page 2.
**Thesis:** six years split evenly between owning business outcomes and engineering the
systems behind them; turns business questions into trusted data products.
**Gate:** PASS with flags (G1 Google H-1B filer; G3 ~65-70% honest — full-stack
Angular/.Net GAP; G4 1-day fresh; G5 6 yrs vs 5 asked).

## 1-page variant (2026-10-14, JD-language rewrite per his direction)

- **"Speak the language of the job listing":** the 1-pager now mirrors the posting's exact
  vocabulary — "Owns data engineering workstreams" (acquisition, pipelining, quality, prep,
  warehousing), "Designed and developed ETL pipelines," "dashboard design and creation,"
  "AI/ML analytics integrated into the reporting stack," "predictive analytics and
  forecasting," "full-stack web application development client and server side" (honest:
  Chrome extension + FastAPI backend + React dashboard, no Angular/.Net claimed),
  "Engaged stakeholders," "prototype and iterate." Every mirrored term maps to grounded
  evidence; nothing invented for keyword matching.
- Condensed: ExponentHR 6 bullets, Missouri S&T 2, C2FO/Udaan/Zomato 1-2 each, 3 projects
  (JobScout, AutoApply AI, Fraud Detection), skills in 4 rows.
- QA: LINT PASS, BUDGET PASS, ORDER PASS, 1 page, fill ~97%, 0 overfull, summary 393 chars.

## Changed vs master

- **Summary (rewritten, Arc F):** "**Analytics Engineer** who turns business questions into
  trusted data products: six years split evenly between owning business outcomes and
  engineering the systems behind them…" 394 chars, 3 lines. Headline carries the target
  title for the skim, per his title rule. Maps to: analytics engineering, stakeholder
  translation, JD's reporting/AI blend.
- **Skills row 1 (Analytics Engineering):** SQL, T-SQL, dbt-style transformation governance
  (honest adjacency — no production dbt claimed), data modeling, semantic models, metric
  definitions. Maps to: modeling best practices, analytics development cycle.
- **Skills row 2 (BI & Visualization):** Power BI, Tableau, Microsoft Fabric, dashboards,
  self-serve analytics. Maps to: "dashboard design/creation with PowerBI and Tableau"
  (both evidenced in ground truth).
- **Skills row 3 (Data Pipelines):** SSIS named explicitly (JD lists it as an accepted ETL
  tool — honestly evidenced), ETL, CDC, Kafka, Spark Structured Streaming, GitHub.
- **Skills row 4 (Python & AI):** Python, FastAPI, PyTorch, MLflow, LightGBM, LLM APIs, RAG.
  Maps to: "AI/ML analytics integrated into reporting," predictive analytics (PyTorch
  evidenced; scikit-learn via LightGBM noted as adjacency in answers).
- **Projects (Arc F order):** JobScout → AutoApply AI → Fraud Detection → FinTune
  (P-RISK is [BLOCKED], excluded per primitives).

## Honest GAPs (named, never papered over)

- **Full-stack web dev (Angular/React/.Net):** the clear miss. He has React (Chrome
  extension) and FastAPI/REST APIs, but no Angular/.Net full-stack delivery. Stated as
  adjacency only.
- **dbt:** no production dbt — "dbt-style transformation governance" only.
- **Databricks:** no production Databricks — SSIS + CDC/MERGE patterns are the honest
  evidence (JD accepts SSIS as an alternative ETL tool).
- **scikit-learn/TensorFlow:** PyTorch evidenced (FinTune); scikit-learn via LightGBM
  adjacency; no TensorFlow claimed.

## Unchanged

- Experience order: strict reverse-chronological (ExponentHR → Missouri S&T → C2FO →
  Udaan → Zomato), verified by `resume_qa.py order` and in the compiled PDF.
- All bullets, metrics, titles, dates verbatim from ground truth. One invented bullet
  caught and removed during drafting (a Power BI/Tableau reporting claim with no ground
  truth source) — replaced with the grounded CI/CD bullet.
- QA: LINT PASS, BUDGET PASS, ORDER PASS, 2 pages, fill 89.5%/60.4%, 27 bullets,
  0 overfull, summary verified 3 visual lines (layout script's "4 lines" is its known
  contact-line miscount; page-1 fill 0.5% under the 90% heuristic at a clean role break).
