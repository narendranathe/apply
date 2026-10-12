# Changelog vs master resume — Anthropic, Data Engineer, Product (2026-10-12)

Variant: `resumes/variants/2026-10-12-anthropic-data-engineer-product.tex` (+ local PDF;
the `.tex` is the source of truth). Compiled copy: `applications/2026-10-12-anthropic-data-engineer-product/resume.pdf`.

Mode: 2-page (JD is a senior product-facing DE role: metrics, data models, self-serve analytics,
stakeholder partnership — breadth rewards the Arc F pairing).
Gate: PASS — G1 Anthropic filed ~80 H-1B certifications in the first half of FY2026 (JD also
states "We do sponsor visas"); G3 4 of 5 top must-haves truthfully evidenced, dbt is an honest
GAP named below (see Map); G4 posting 3 days old; G5 no 7+ year demand, no Staff title;
G6 knockouts answered honestly in answers.md (H-1B transfer, relocation to SF/NYC/Seattle, 6yr/3yr).
Arc: F primary (product-facing data role — arcs.md promotes F to primary for these) + F page 2.
Thesis: He turns product questions into trusted measurement — six years split between owning
business outcomes and building the data systems behind them.

## Requirement map (must-have -> ground-truth ID -> consequence)
1. Significant DE / data-science-analytics experience partnering with Product on company-wide
   metrics -> ExponentHR Data Engineer (payroll product metrics), E2-CICD (Scrum alignment of
   dev/test/IT/business), Z-PNL/Z-COMP/U-GMV (metrics-driven business outcomes). Adjacency:
   his metrics experience is payroll/B2B product, not consumer products; the translation
   pattern is identical. Evidenced.
2. Multi-step ETL jobs with dbt -> **GAP: no production dbt** (permanent exclusions in
   ground-truth). Adjacency used: E3-FABRIC (Fabric semantic layer — dbt-style transformation
   governance), E1-CDC (idempotent incremental pipelines). Skills row carries "dbt-style
   transformation governance" as labeled adjacency, never a dbt claim. Flagged for Naren.
3. Expertise in SQL and Python for accurate, clean data models -> E1-CDC (CDC reengineered in
   SQL), E-PTO (SQL diagnostics), E-REPORTAUTO (Python), P-HOOKS (Python, 330+ tests).
   Evidenced.
4. Reporting and dashboards (Hex) serving multiple cross-functional teams -> E3-FABRIC
   (semantic models, self-serve), SSRS/Power BI/Tableau delivery, Z-ES (self-serve search,
   support desk -80%). Hex itself not evidenced — adjacency to BI tools; named in changelog.
5. Translate partner data needs into data models/reporting/requirements; define and materialize
   metrics; self-serve analytics -> E2-CICD (stakeholder alignment), E3-FABRIC (NL-to-SQL
   self-serve), C-PRD (PRDs from stakeholder interviews), Z-COMP (metrics for product
   decisions). Evidenced.
Nice-to-haves: building a DE function from scratch -> not evidenced (no claim made); using AI
to scale productivity -> E-AICTX (Claude Code context harness, team-adopted), Copilot-gated
review (E2-CICD). Evidenced.

## Changes vs master

### Summary — REWRITTEN
Was Arc E ("AI Engineer with a data-platform foundation..."). Now Arc F: "Data Engineer who
turns product questions into trusted measurement: six years, split evenly between owning
business outcomes and building the data systems behind them. Fabric semantic layer with
NL-to-SQL self-serve, idempotent pipelines across 400 databases and 70 TB, AI tooling the
team uses daily. Brings data modeling, integrity standards, and self-serve analytics to
product metrics." 387 chars, 3-line band. (JD: metrics, data models, self-serve analytics,
consumer products.)

### Technical Skills — RESTRUCTURED
- Row 1 REPLACED "AI & ML" lead with "Data Modeling & Pipelines: SQL, T-SQL, PySpark, Kafka,
  Spark Structured Streaming, CDC, Delta Lake, idempotent pipelines, data validation,
  dbt-style transformation governance" (JD must-haves: SQL/Python, ETL, data models; dbt is
  labeled adjacency per ground-truth exclusions).
- Row 2 NEW "Reporting & Metrics: Power BI, Tableau, SSRS, Microsoft Fabric, semantic models,
  metric definitions, dashboards, self-serve analytics, stakeholder requirements" (JD:
  dashboards, metrics, self-serve).
- "Python & AI Tooling" and "Cloud & DevOps" rows kept, reordered after the two JD-first rows.

### ExponentHR (8 bullets) — reordered and reframed for JD
- MOVED E3-FABRIC to bullet 1, reframed as "making ad-hoc analysis self-serve" (JD: self-serve
  analytics, dashboards, core metrics). Kept numbers (12s -> under 4s, tickets -40%, $243K).
- MOVED E-OBS (validation turning silent drift into alertable failure) in as bullet 3 (JD:
  "set data integrity standards and SLAs so data is delivered on time and accurately"). Was
  absent from master.
- MOVED E-AICTX in as bullet 5 (JD: "Experience using AI to scale your own productivity and
  your team's without lowering the quality of the work"). Was absent from master.
- MOVED E-REPORTAUTO in as bullet 6 (JD: Python, end-to-end mindset). Was absent from master.
- MOVED E-SELFHEAL in as bullet 7 (JD: SLAs, data delivered on time). Was absent from master.
- KEPT E1-CDC (bullet 2, multi-step ETL adjacency), E2-CICD (bullet 4, stakeholder
  partnership), E-IDEM (bullet 8, $300K consequence).
- REMOVED E-PTO (payroll-accrual defect) and E-RETENTION (hard-delete history recovery): Arc B
  evidence with no JD mapping here; page-1 space reserved for metrics/self-serve evidence.
- REMOVED E5-RECOVERY (Contained AAG failover runbooks): reliability bullet with no JD mapping.

### Missouri S&T (3 bullets) — KEPT, minor wording
- "20 nodes to 4-8 dynamic" -> "20 nodes to 4-8" (banned-word hygiene, "dynamic" generic).
  Content unchanged: M1 (95 percent accuracy), M4-PAPER (peer-reviewed NLP study), M3 (AKS).

### Zomato (4 of master 6) — kept product-analytics metrics bullets
- KEPT Z-PNL, Z-COMP, Z-SEARCH, Z-ES (metrics, competitor analytics, search signals,
  self-serve search - JD: product metrics, self-serve).
- REMOVED Z-CAMPAIGN (200% campaign revenue) and Z-GROWTH (14% quarterly growth): business
  bullets weakest-mapped to a data-engineering JD; cut per fill order.

### Udaan (3 of master 4)
- KEPT U-GMV, U-FORECAST, U-BCP (bridge bullet — reliability thinking before he owned
  pipelines; arcs.md requires one bridge bullet per two-page build).
- REMOVED U-TEAM (hired/managed 10+ sourcing team): management bullet with no JD mapping.

### C2FO (2) — KEPT
- KEPT C-SQL (SQL-driven product behavior analysis) and C-PRD (PRDs from stakeholder
  interviews): direct JD evidence for "partner with Product" and "technical requirements".

### Projects (5, Arc F order with AI-reframe flavor)
- P-JOBSCOUT (1 bullet): self-serve analytics artifact; KEPT.
- P-AUTOAPPLY (2 bullets): AI tooling + ATS scale; KEPT.
- P-FRAUD (2 bullets): streaming metrics + observability (Prometheus/Grafana) — substitutes
  for P-RISK, which is [BLOCKED] as written; synthetic-data honesty preserved in bullet 2.
- P-FINTUNE (2 bullets): AI-platform evidence (guardrails, drift) per the 2026-10-01
  AI-engineering reframe; KEPT.
- P-HOOKS (2 bullets): highest-value verifiable artifact; KEPT.

### Bolds (page 1: 8, page 2: 3)
Page 1: **self-serve**, **SQL**, **data integrity**, **14 days**, **AI**, **Python**, **SLAs**,
**$300K**. Page 2: **95 percent**, **$3,200**, **9 percent**. All exact JD terms or headline
metrics, max 1 per bullet, max 3 words each.

### Flags for Naren
- dbt GAP (honest): no production dbt on record; skills row carries labeled adjacency only.
  Anthropic's JD centers dbt pipelines — screen risk is real.
- No consumer-product metrics evidence: his metrics work is payroll/B2B; framing translates.
- Salary band ($320K-$405K) far exceeds his $175K-Dallas target — no conflict, but level
  mapping is Anthropic's call.

## 2026-10-12 fix — Experience reordered to strict reverse-chronological
Naren flagged the page-2 order as wrong (his rule: latest to oldest, no exceptions).
Moved the C2FO block (Product Intern, Jun–Aug 2023) ahead of Udaan and Zomato.
Order is now ExponentHR → Missouri S&T → C2FO → Udaan → Zomato, matching the
corrected master. Recompiled: 2 pages, 0 overfull, `resume_qa.py order` PASS.
No bullet text changed — pure block move.
