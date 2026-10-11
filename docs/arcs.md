# ARCS.md — the six story arcs (mirror)

> Mirrored from the Mac original (`/Users/naren/Downloads/files/ARCS.md`) on 2026-10-11
> for the multi-agent application pipeline. If this copy and the Mac original ever disagree,
> the Mac original wins — then update this mirror.

Facts live in `ground-truth.md`; point IDs below refer to it.

Every build is **one primary arc on page 1 plus Arc F on page 2.** The earlier career sits on page 2
regardless, so the business-translator story comes free with the two-page format. That pairing is the
whole reason two pages beats one for you: page 1 proves you can do the job, page 2 proves most
candidates cannot tell the business why it mattered.

Pick the primary arc from the requisition's own language, not from what interests you most.

---

## Arc A — Reliability engineer who happens to work in data
**Thesis:** most data engineers ship pipelines; you ship the detection-and-recovery layer underneath.
**Recruiter concludes:** this person will take on-call and make the platform stop paging us.
**JD triggers:** incident management, root cause analysis, proactive mitigation, on-call, SRE,
"secure stable scalable", observability, MTTR, data quality, freshness SLO.
**Targets:** Mastercard · Goldman SRE Data · JPMorgan infrastructure · T-Mobile · Pindrop.
**Evidence in order:** E-SELFHEAL · E-IDEM · E-LIVEREC · E-RETENTION · E-OBS · E5-RECOVERY · M2 · M1.
**Projects:** P-HOOKS · P-FINTUNE · P-FRAUD.
**Summary shape:** *Data engineer building the detection and recovery layer under production
pipelines — 400 databases, 70 TB, 15-minute refresh. [reliability flagship]. Brings [JD terms] to
[their stated reliability problem].*

## Arc B — Financial-data correctness, proven under a regulated deadline
**Thesis:** payroll data is the same correctness problem as trading and position data — silent
money-adjacent defects, audit trails, deadlines you do not control.
**Recruiter concludes:** has shipped correct money-adjacent code under an external deadline and
understands change control. Rare in a data engineering candidate.
**JD triggers:** regulatory, compliance, audit, reconciliation, risk, trading, position, SOX,
data correctness, financial data, tenant isolation.
**Targets:** Goldman (ETO, SRE Data, Lakehouse & AI) · JPMorgan and IAM · Citadel · Two Sigma ·
Point72 · Jane Street · Bloomberg · Visa · Paychex.
**Evidence:** E-PTO · E-SECURE2 · E1-CDC · E-IDEM · E5-RECOVERY · E-LIVEREC.
**Projects:** P-RISK (in-progress framing) · P-FRAUD · P-RECON.
**Summary shape:** *Data engineer on payroll-critical systems — money-adjacent correctness, formal
change control, legislated deadlines. [E-PTO or E-SECURE2]. Brings [JD terms] to [their domain].*
**Strongest arc for your stated target list.** E-PTO and E-SECURE2 are evidence classes almost no DE
candidate has, and they sat unused for months.

## Arc C — SQL-deep platform engineer with real production Python
**Thesis:** SQL depth most candidates do not reach, paired with shipped, signed, tested Python —
not aspirational Python.
**Recruiter concludes:** passes the SQL screen and the Python screen.
**JD triggers:** "SQL and Python", expert SQL, query tuning, window functions, strong programming,
Python-or-Java, test coverage.
**Targets:** Goldman ETO (SQL-heavy format) · JPMorgan · Bloomberg · FAANG data engineering.
**Evidence:** E1-CDC · E-PTO (SQL diagnostics) · E-RETENTION · E3-FABRIC (query tuning) ·
E-REPORTAUTO · P-HOOKS · P-AUTOAPPLY.
**Projects:** P-HOOKS · P-AUTOAPPLY · P-FRAUD.
**Guard:** claim depth, not duration. No eight-year T-SQL claim.

## Arc D — Ships velocity, not just pipelines
**Thesis:** most candidates point to a pipeline they built; few own the release process that makes
pipelines safe to ship at all.
**Recruiter concludes:** raises the whole team's shipping rate, not just their own output.
**JD triggers:** developer productivity, platform engineering, internal tooling, CI/CD ownership,
FinOps, cost optimization, reduce toil, infrastructure as code.
**Targets:** FAANG platform and infra · Databricks · T-Mobile Cloud FinOps · any DevEx requisition.
**Evidence:** E2-CICD (3 months to 14 days, 11 weeks of idle time removed) · E-IDEM ($300K, 2.5 FTEs) ·
M3 ($38.4K/yr) · E-REPORTAUTO.
**Projects:** P-HOOKS · P-AUTOAPPLY.
**Note:** FinOps is a named 2026 hiring priority. This arc is stronger than its usage so far.

## Arc E — AI platform engineer, not just data engineer
**Thesis:** builds AI systems with a fault model — guardrails, drift monitoring, autonomous recovery.
**Recruiter concludes:** can put a model in production and keep it there, which is the actual gap on
most AI platform teams. Deployment is the stated bottleneck in 2026, not modeling.
**JD triggers:** LLM serving, RAG, MLOps, model deployment, inference, guardrails, drift,
fine-tuning, vector database, model monitoring.
**Targets:** AI/ML platform roles · Databricks · Snowflake · Meta and Google ML infra.
**Evidence:** P-FINTUNE · P-AUTOAPPLY · P-FRAUD · E3-FABRIC (semantic layer, NL-to-SQL) ·
M4 (NLP research plus publication).
**Projects:** P-FINTUNE · P-AUTOAPPLY · P-FRAUD.
**Market fit:** PyTorch appears in ~42% of ML engineer postings and P-FINTUNE is a PyTorch artifact;
vector databases ~4.5% and P-AUTOAPPLY uses pgvector; traditional NLP ~19.7% and M4 has a
peer-reviewed publication behind it. Your AI evidence aligns better to the posted market than any
resume has shown.

## Arc F — Business-to-data translator *(always page 2)*
**Thesis:** came up answering business questions with data, now builds the platforms that produce it.
**Recruiter concludes:** will not need a translator — can talk to a stakeholder and to the database.
**JD triggers:** partner with business stakeholders, translate requirements, ambiguous problems,
stakeholder management, analytics engineering, self-service, product-facing data.
**Targets:** every requisition, on page 2. Promote to primary for analytics engineering and
product-facing data roles.
**Evidence:** Z-PNL · Z-COMP · Z-GROWTH · U-GMV · U-FORECAST · U-BCP · U-RCA · C-PRD · E3-FABRIC ·
E2-CICD (Scrum).
**Projects:** P-JOBSCOUT · P-AUTOAPPLY.
**Summary shape:** *Six years, split evenly between owning business outcomes and engineering the
systems behind them. [technical flagship]. Brings [JD terms] to [their problem].*
**The two bridge bullets:** U-BCP (business continuity plan for app downtime — reliability thinking
three years before you owned an AAG failover path) and U-RCA (root-cause discipline now applied to
pipelines). One belongs on every two-page build. They make the pivot read as inevitable rather than
opportunistic.

---

## Project selection by arc

| Primary arc | Projects, in order |
|---|---|
| A Reliability | P-HOOKS · P-FINTUNE · P-FRAUD |
| B Financial correctness | P-RISK · P-FRAUD · P-RECON |
| C SQL + Python | P-HOOKS · P-AUTOAPPLY · P-FRAUD |
| D Velocity | P-HOOKS · P-AUTOAPPLY · P-FRAUD |
| E AI platform | P-FINTUNE · P-AUTOAPPLY · P-FRAUD |
| F Translator | P-JOBSCOUT · P-AUTOAPPLY · P-RISK |

Three to five projects on a two-page build, one to two bullets each. Never more than five.

## Adjacency — only when the JD demands a tool that is not here
| JD asks for | Truthful framing |
|---|---|
| Databricks | CDC and MERGE patterns, Spark-compatible design, lakehouse-aligned modeling |
| dbt | Fabric semantic layer, dbt-style transformation governance |
| Terraform | Azure DevOps idempotent provisioning, infrastructure-as-code aligned |
| Snowflake | SQL optimization, warehouse-style modeling, governed transformations |
| AWS / GCP | cloud data platform language only where a project actually supports it |
| MLOps | P-FRAUD and P-FINTUNE: MLflow, FastAPI, drift monitoring, guardrails |
| Kubernetes | AKS, HPA, Docker, cost optimization (M3) |
| Airflow | P-FRAUD orchestration; Azure DevOps scheduled pipelines at work |

## Blocking list, ranked by what it costs you

1. **P-RISK on the live Visa resume** — "15K+ records, 47.8 TPS, sub-5s, live risk views" is not
   supported by the repo. Fix or pull today.
2. **Both Indian titles against offer letters.**
3. **Udaan $4M** — name the base or drop it.
4. **U-TEAM verb** — hired and managed, or trained and mentored.
5. **The old Visa `.tex`** still carries the Terraform/Databricks bullet and "containerized AAG."
   Both are violations. Rebuild from this file.
6. **Support ticket handle time** — turns $243K from estimate into fact.
7. **Infrastructure bill before and after CDC** — the missing large dollar figure.
8. **E-UPTIME** confirmed as your improvement · **E1-CDC** canonical figures · **SENTINEL** defined
   or dropped.
9. **P-JOBSCOUT FastAPI vs Flask** · **Dallas vs Addison** · **400 DB / 70 TB / 10 servers** confirmed.
