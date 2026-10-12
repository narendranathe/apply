# GROUND_TRUTH.md — the only source of facts (mirror)

> Mirrored from the Mac original (`/Users/naren/Downloads/files/GROUND_TRUTH.md`) on 2026-10-11
> for the multi-agent application pipeline. If this copy and the Mac original ever disagree,
> the Mac original wins — then update this mirror.

Nothing goes on a resume, a LinkedIn profile, or into an interview answer unless it is in this file.
No invention, no extrapolation, no rounding up. When a JD demands something absent here, use the
Adjacency table or name the gap.

**Evidence status**
- **`[DOC]`** — backed by a primary document (the 2021 resume, a repo, a publication).
- **`[STATED]`** — you confirmed it directly; no document. Usable, but you must be able to walk it
  end to end under five minutes of questioning.
- **`[VERIFY]`** — unresolved conflict or missing value.
- **`[BLOCKED]`** — does not ship until the stated problem is fixed.

**User clarification, Sept. 28, 2026:** `[VERIFY]` records an evidence or wording gap, not a
denial that the work was done. For the master resume, the user explicitly asked to include useful
work in this category. Use the stated details where they do not conflict; resolve conflicting
numbers, verbs, or attribution before using those particular claims. `[BLOCKED]` contradictions
remain excluded.

**User skill clarification, Sept. 28, 2026:** Preserve Python and Spark in the master resume and
include Delta Lake / Delta tables and lakehouse architecture among technical skills. The specific
project and production status of the Delta/lakehouse work are pending the user's clarification;
do not assign that work to an employer or claim a delivered production lakehouse without it.

Every point carries an **ID** (cite it in the requirement map), a **consequence** in money / time /
risk / scale, and the **arcs** it serves.

---

## Identity

Narendranath Edara · +1 (573) 466-6656 · edara.narendranath@gmail.com
linkedin.com/in/narendranathe · github.com/narendranathe · narendranathe.github.io
**Dallas, TX** `[VERIFY]` — one draft says Addison. Pick one and use it everywhere, including LinkedIn.

**Titles — HR-verifiable, never adjusted per application**
| Employer | Title | Dates | Location |
|---|---|---|---|
| ExponentHR | Data Engineer | July 2024 – Present | Dallas, TX |
| Missouri S&T | Data Engineer | Aug. 2023 – July 2024 | Rolla, MO |
| C2FO | Product Intern | June 2023 – Aug. 2023 | Kansas City, KS |
| Udaan | Business Intelligence Analyst `[STATED]` | Sept. 2020 – Mar. 2021 | Nashik, India |
| Zomato | Business Analyst `[STATED]` | Mar. 2018 – Sept. 2020 | Hyderabad, India |

The 2021 resume records Sales Manager at Zomato and Area Lead at Udaan. You have stated both are
different from the HR title. **Confirm both against offer or relieving letters.** Title is the one
field a background check verifies; a mismatch there is unrecoverable, and it is the single highest
risk in this file.

**Tenure.** Analytics and BI Mar. 2018 – Mar. 2021 = 37 months. Data engineering Aug. 2023 – present
= ~36 months. **6 years professional, 3 in data engineering.** Gap Mar. 2021 – Jan. 2022 (~9 months)
before the MS — have one sentence ready. Never claim seven years of anything, and never claim eight
years of T-SQL: the 2018–2021 years ran on Excel, Google Data Studio, and Tableau. Claim depth,
not duration.

**Permanent exclusions.** Never claim ownership of production dbt or dbt Cloud · Terraform modules
or state · Databricks workspace, Unity Catalog, Delta Live Tables, lakehouse delivery · production
Snowflake, AWS, or GCP. Never mention Foundry BI Engine · Architecture 4 · FAISS concept index ·
NetworkX join resolver · Claude query planner · DAX.

**Honest gaps, named not papered over:** AWS (32.9% of the cloud AI market — a real gap) · Snowflake ·
dbt · production Airflow · GCP.

**Tooling `[STATED]`, Oct. 12, 2026:** solid hands-on experience with Retool and Figma (user's direct
statement). Usable in Technical Skills rows and as JD keyword evidence for frontend/design-tool
requirements. Do not invent specific deliverables, employers, or timelines for this work.

---

## ExponentHR — Data Engineer · July 2024 – Present · Dallas, TX

**Scale, for the context line** `[VERIFY against internal records]`: 400 databases (320 × ~100 GB,
80 × ~500 GB) = **~70 TB across 10 servers**, ~7 TB each, **15-minute refresh cadence**. ~160-person
HR and payroll vendor. This does more for a senior reader than any percentage.

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **E-SSISDB** | `[STATED]` User clarified Sept. 30, 2026 that a separate stored procedure reads SSISDB to detect failed SQL Agent jobs. It records which SSIS packages ran, when they ran, and execution evidence around the jobs. The evidence alerts the team and supports investigation and targeted fixes rather than repeated blind retries. Do not describe this detection procedure as automatically retrying or self-healing jobs. | Risk/time: failed runs become visible and diagnosable, enabling faster root-cause resolution; no quantified metric for this procedure. | A, B |
| **E-SELFHEAL** | Proactive monitoring and self-healing recovery for production SQL Agent jobs, instrumented against three failure modes — missed runs, stuck runs, silent failures — before CDC log retention could make a late-caught failure permanently unrecoverable. Owned creation through shipped fix. This recovery work is distinct from the SSISDB-backed failure-detection procedure in E-SSISDB; do not merge their mechanisms into one claim. | Risk: closed a detection gap with zero prior coverage | A, B |
| **E-IDEM** | Database copy and restore pipeline for Contained AAG databases rewritten fully idempotent. Four latent failure modes closed: orphaned CDC cleanup jobs now properly terminated rather than metadata-only; a guard that kills active replication sessions before disabling CDC; silently-defaulting restore parameters fixed; stuck-restore detection with pre-flight race checks. | Time: 5,000 eng-hrs/yr, **~$300K, 2.5 FTEs**. Risk: safe retry instead of manual cleanup | A, B, D |
| **E1-CDC** | CDC ETL reengineered from full-table reloads to idempotent incremental merge-upserts; a failed run reruns clean with no manual cleanup. Now the platform's standing CDC pattern. **Always pair the speed number with the SLA it held** — a speedup that hides a reliability regression is what reviewers watch for. | Time: 30 min -> under 8 min. Money: compute -67%. Scale: ~99.9% less data moved (2.46M TB -> 257 TB/yr) | all |
| **E-LIVEREC** | Live CDC incremental-load failure diagnosed and restored in place under production pressure, rather than defaulting to a slow full reload or leaving stale data. | Risk: avoided both extended downtime and stale client-facing payroll data | A, B |
| **E-PTO** | Payroll accrual defect root-caused to a payroll-period boundary calculation via direct SQL diagnostics, then shipped through formal change-request review rather than a direct patch. Money-adjacent: PTO balances convert to payout at termination. | Risk: corrected logic shipped once, reviewed, to every client tenant | B, C |
| **E-SECURE2** | SECURE 2.0 retirement-plan compliance delivered against a legislated deadline outside the team's control, alongside a Clock Out Type enhancement, in the Sprint 2025.12.15 release. | Risk: regulatory delivery — an evidence class ordinary bug-fixing cannot provide | B |
| **E2-CICD** | CI/CD owned end to end through Azure DevOps, with Scrum aligning dev, test, IT, and business on shared priorities. SSRS and Power BI release steps automated. Copilot-assisted code gated through review and tests. | Time: 3 months -> 14 days, 6.5x, **~11 weeks of cross-team idle time removed per release** | D, all |
| **E-AICTX** | `[STATED]` User confirmed Sept. 30, 2026 that he built and internally deployed his own business-context harness for **Claude Code CLI**. Its context model describes primary/foreign-key relationships and table grain so the assistant can reason about how columns relate across tables; he uses it to diagnose defects, write and fix code, and guide safer SQL joins. Copilot is also used in current engineering work; E2-CICD records the review/test gate. `[STATED]` User confirmed Oct. 1, 2026 that **his team uses the harness**, and that its context is a mix: primary/foreign keys and table grain are **generated from schema metadata**, then **annotated with business meaning by hand**. | Risk/time: reduced risk of wrong-grain joins, fewer support tickets, faster bug resolution, per user; no quantified baseline yet. **Do not attribute E3-FABRIC's 40% ticket reduction to this separate harness.** Team adoption is confirmed; the number of users and before/after resolution times are not recorded. This is a semantic context model, not a trained ML model. | C, E, A |
| **E3-FABRIC** | Legacy SQL Server reporting migrated onto Microsoft Fabric semantic models with natural-language-to-SQL agent access; index and stored-procedure tuning on OLAP paths. | Time: 12s -> under 4s (67%). Money: client support tickets -40%, **~$243K/yr** analyst capacity | C, E, F |
| **E-OBS** | SSIS execution history surfaced in SQL for IT and business visibility. Control-table-driven validation turned silent data drift into an alertable failure. For the separate SSISDB-backed SQL Agent failure-detection procedure and its package-level execution evidence, use E-SSISDB. | Risk: drift stops being invisible; named owner, documented response | A |
| **E-RETENTION** | Hard-delete history loss traced to CDC retention expiry on PMnEmployeeReview and PMnEmpGoals; missing change history recovered through `fn\_cdc\_get\_all\_changes` before the window closed. | Risk: recovered otherwise permanently lost audit history | A, C |
| **E5-RECOVERY** | AAG failover recovery with documented runbooks; CDC capture jobs restored under 1 hr, including the silent post-failover capture-job failure found and closed. | Risk: **MTTR under one hour**; a specialist-only restore turned into an on-call runbook | A, B |
| **E-UPTIME** | Platform uptime 98% -> 99.9%; annual downtime 7.3 days -> 8.8 hours. `[STATED]` User confirmed Sept. 28, 2026 that he drove the improvement. Uptime percentages read as operations framing — prefer MTTR unless the JD names uptime. | Risk | A |
| **E-REPORTAUTO** | Git and Azure DevOps API workflows automated in Python, giving the team real-time delivery visibility. | Time: 15+ hrs/sprint, ~390 hrs/yr, **~$23K/yr** | C, D |
| **E-DOCS** | `[STATED]` User confirmed Oct. 1, 2026 that new hires read his docs on GitHub Codespaces, git branch strategy, and work-item resolution instead of asking him (wording first appeared on his GitHub profile README). | Time/scale: onboarding questions answered by documentation; a team-enablement signal. No quantified metric; do not claim a headcount of new hires. | D, F |

**Two hard wording rules.** The word is **Contained** AAG, never "containerized" — different thing,
and anyone who knows Always On will catch it. And **never attribute the 12s -> 4s query gain to
Azure DevOps**; that is CI/CD tooling, not a query engine. The gain came from Fabric semantic models
and SQL Server tuning. Either error ends a technical screen.

---

## Missouri S&T — Data Engineer · Aug. 2023 – July 2024 · Rolla, MO

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **M1** | Azure AI Anomaly Detector pipelines selecting an algorithm per time-series profile, 95%+ accuracy. | Risk: production memory leak caught 4 hrs before outage | A, E |
| **M2** | Data observability with tunable per-service thresholds; ~250 weekly non-actionable P3 alerts filtered, signal-to-noise 1:5 -> 1:1.2. | Time: on-call triage load. Risk: ended alert blindness masking real incidents | A |
| **M3** | Anomaly-detection workloads migrated from static D-series VMs to AKS with horizontal pod autoscaling; 20 static nodes -> 4–8 dynamic. | Money: **-$3,200/mo, $38.4K/yr**. Time: CPU 12% -> 64% | D |
| **M4-PAPER** | `[DOC]` Coauthored a peer-reviewed NLP paper with David Bojanic and Jane Zhang: *An Examination of Sentiment Analysis as a Tool for Gathering Visitor Insights from Online Review Sites for a Museum*. The study compared Python-based VADER and RoBERTa sentiment analysis on TripAdvisor and Yelp museum reviews and evaluated adjective sentiment against visitor ratings. Results varied across sources and methods. Published online in 2025 and in the 2026 issue of *Journal of Nonprofit & Public Sector Marketing*, DOI 10.1080/10495142.2025.2525123. User identifies this as Missouri S&T research experience; publication followed the stated employment dates. | Output: peer-reviewed publication; methodological risk: a single source or NLP technique could give a misleading sentiment reading. **Do not bind the 10,000+ review count to this paper without a source.** | E, F |
| **M4-LITE** | `[VERIFY]` Separate "Experiential Learning with LITE" article or project, Department of Business and Information Technology; earlier notes also describe NLP pipelines over 10,000+ reviews with spaCy/NLTK, dimensional models, and Power BI. The relation between that work and the museum paper is not established. | Do not attach the museum-paper DOI to LITE or the 10,000+ count to the paper. | E, F |

---

## Zomato — Business Analyst · Mar. 2018 – Sept. 2020 · Hyderabad, India

Portfolio of 300+ restaurant partners per quarter across South India markets; launch team for new
city operations.

**Analytics** `[STATED]` — these carry the pivot story:

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **Z-COMP** | Real-time competitor analytics platform tracking pricing, delivery times, and coverage, including ingestion and refresh cadence so pricing ran on current rather than week-old signals. | Money: **9% market share gain** in contested metros | F |
| **Z-SEARCH** | Search relevance work supplying and evaluating contextual signals — time, location, cuisine affinity — feeding ranking. **Analyst framing only: never claim you trained the models.** | Scale: millions of daily queries. Money: search-to-conversion lifted | F, E |
| **Z-ES** | Elasticsearch index over 100K+ internal documents for self-serve search. | Time: **support desk volume -80%**. Scale: 100K+ docs | F, A |
| **Z-CAMPAIGN** | Campaign analytics: funnel instrumentation, A/B tests on campaigns and discount strategy, spend reallocated to what converted; Tableau dashboards on menu-to-cart and cart-to-order. | Money: **campaign revenue +200%** | F |
| **Z-DASH** | Central dashboard for Zomato Markets on category-level, city-wise product launch percentage. *Your earliest analytics artifact — the legitimate seed of the pivot.* | Scale | F |

**Commercial** `[DOC]`:

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **Z-PNL** | Portfolio unit economics turned positive — contribution from **-₹18 to +₹2 per order** across a 300-restaurant portfolio — by rebalancing discount sharing against ad-revenue mix. | Money. *A contribution-margin turnaround. Strongest single business bullet you own.* | F |
| **Z-GROWTH** | **14% compounded growth in one quarter across 300 restaurants**, campaign coverage lifted to 70%. | Money | F |
| **Z-LOYALTY** | **200+ partners** onboarded to a loyalty program, cutting company discount burn **7%** for that cohort. | Money | F |
| **Z-C100** | Project C100 launch team — operations opened in 4 cities (Vijayawada, Guntur, Manipal, Udipi); **1,000 orders/day within 3 days in Guntur**. | Scale | F |
| **Z-EXCL** | **4 competitor-exclusive brands** won, 300–500 orders/day per outlet each. | Money | F |
| **Z-SELFPICK** | **150+ clients** acquired onto the self-pickup product. | Scale | F |
| **Z-LOGI** | Logistics cost cut by launching a self-logistics model in Markapur and Puttur. | Money | F |
| **Z-RECOG** | "Meal for One Champion" — 10% order growth, South region, 2020. | — | F |

---

## Udaan — Business Intelligence Analyst · Sept. 2020 – Mar. 2021 · Nashik, India

New-city Staples category launch on a B2B commerce marketplace; owned GMV, procurement turnaround,
and a 9-person procurement bench.

**BI** `[STATED]`:

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **U-FORECAST** | Demand-forecasting and inventory/financial models. Tooling: **Power BI** `[STATED]`, plus **Excel macros** for daily inventory-lead-time and demand reporting and **Google Data Studio** for market-penetration planning `[DOC]`. Name whichever the JD asks for. | Money: **ROI +7%**. `[BLOCKED]` on the $4M figure | F |
| **U-FULFILL** | Statistical demand analysis for capacity planning under JIT and warehouse models. | Risk: **99.3% order fulfillment** sustained | F, A |
| **U-TEAM** | `[STATED]` User confirmed Sept. 28, 2026 that he **hired and managed a 10+ person sourcing team**. The 2021 resume instead says trained and mentored 9+ recruits; retain the user's confirmed wording for the master resume. | Scale | F |

**Commercial** `[DOC]`:

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **U-GMV** | City GMV grown **400 percentage points to ₹5 Cr/month**, matching the incumbent; **₹3.9 Cr in December at 1.3x MoM** three months after the Nashik launch. | Money, Scale | F |
| **U-BUYERS** | Demographic supply-demand gaps identified driving a **19% increase in buyers**. | Money | F |
| **U-SALIENCE** | Rice, flour, and pulses category salience **11% -> 27%** via an in-app discount campaign. | Money | F |
| **U-RCA** | Root-cause analysis on recurring value-chain failures with process flows rewritten to prevent repeats; vendor onboarding turnaround cut. **Bridge bullet.** | Time, Risk | F, A |
| **U-BCP** | Business continuity plan devised and executed to keep delivery cycles running through application downtime. **Bridge bullet.** | Risk | F, A |
| **U-GRN** | GRN process flow managed with operations, reducing out-of-stocks. | Money | F |
| **U-RECOG** | Supply Champion — highest first-party seller sale share, West Region 2021. | — | F |

### `[BLOCKED]` — the $4M does not reconcile
Documented peak city GMV is ₹5 Cr/month ≈ **$8.1M/year**. A 7% ROI lift on that base is **~$568K**.
Producing $4M requires a base of **~$57M/year — 7x the GMV you documented.** Either it spans all of
Udaan and is not yours to claim, or the base is something other than GMV (total procurement spend,
category inventory value) and must be named explicitly in the bullet, or it never had a base.
**Ship the 7%. Hold the $4M** until you can name the base in one sentence, because "7% of what?" is
the first question anyone asks.

---

## C2FO — Product Intern · June 2023 – Aug. 2023 · Kansas City, KS

| ID | Point | Consequence | Arcs |
|---|---|---|---|
| **C-SQL** | SQL-driven product and user-behavior analysis across a B2B fintech working-capital transaction platform; findings turned into product decisions with the owning team. | Scale | F, B |
| **C-PRD** | Product requirement documents authored for a preferred-offers tool through stakeholder interviews across product and operations. | Time: resource-allocation time -50% `[STATED]` | F |
| **C-CSPO** | Certified Scrum Product Owner. | — | F |

---

## Projects

| ID | Project · stack · year | Points | Honesty gate | Arcs |
|---|---|---|---|---|
| **P-HOOKS** | **repo-context-hooks** · Python, GitHub Actions, Sigstore, CodeQL · PyPI | Zero-dependency pre/post-commit hooks snapshotting repo context for LLM agents between sessions, standard library only at runtime. Release path hardened with Sigstore-signed OIDC Trusted Publisher flow, CodeQL on every PR, Dependabot on the Actions ecosystem. **330+ passing tests** at v1.0. | None — publicly installable and independently verifiable. | A, C, D |
| **P-FINTUNE** | **FinTune** · Mistral-7B, QLoRA, FastAPI, PyTorch · 2026 | Mistral-7B fine-tuned via QLoRA (4-bit NF4 + LoRA adapters) for financial sentiment; FastAPI serving behind pre-inference PII-redaction guardrails and KL-divergence drift monitoring; three-state circuit breaker autonomously reloads the model, drops batch size under memory pressure, or falls back to a quantized model. **35+ tests** across data, model, guardrails, serving, recovery. | **Benchmark table is unfilled placeholders. Cite architecture and test count — never F1 or latency.** | A, E |
| **P-FRAUD** | **Real-Time Fraud Detection Platform** · Kafka, LightGBM, MLflow, Airflow, Prometheus, Grafana, Docker · 2026 | Kafka event streaming at 100+ TPS into a FastAPI scoring service; LightGBM tracked in MLflow; exactly-once processing; Prometheus/Grafana observability; containerized. **P99 serving latency 1.12 ms at 100+ TPS sustained.** | **Synthetic data — 100K generated transactions at ~2% fraud rate, not live payment traffic. Say so plainly if asked; the engineering is real regardless.** `[DOC]` **Repo README check, Oct. 1, 2026, corrects three points above:** (1) the README says "Not exactly-once, but replays are no-ops" (auto-committed offsets plus `ON CONFLICT (transaction_id) DO NOTHING`), so say **idempotent writes / replays are no-ops**, never "exactly-once"; (2) 1.12 ms is **per-request P99 scoring latency** (Prometheus histogram on `/predict`, single sample, no batching) and 100 TPS is the **producer** rate; the documented 75-minute run scored ~0.5 predictions/s before the consumer fix, so never write "1.12 ms at 100+ TPS sustained"; (3) `airflow/` holds only empty `.gitkeep` scaffolding, so Airflow is not evidenced. | B, E, A |
| **P-RISK** | **Real-Time Portfolio Risk Analytics** · Kafka, Spark Structured Streaming, FastAPI, Streamlit · 2026 | Kafka market-tick ingestion into Spark Structured Streaming on 5-second tumbling windows; FastAPI computing historical-simulation VaR at 95% and 99% confidence; Streamlit dashboard. | **`[BLOCKED]` as written on your live Visa resume.** The Spark-to-FastAPI handoff is console-only — the CSV sink is planned, not shipped — so the API falls back to generated data. Frame as **in progress**, never "live end-to-end." The "15K+ records, 47.8 TPS, sub-5s latency, live risk views" claim is **not supported by the repo.** `[DOC]` Source check, Oct. 1, 2026: `src/streaming/delta_lake_writer.py` streams Kafka through Spark Structured Streaming into a **Delta Lake bronze layer** with checkpointing; `spark_kafka_consumer.py` uses 5-second windows with a 10-second watermark. Usable as project evidence for Delta Lake. The repo README still says "47.8 TPS sustained"; do not cite it. | B |
| **P-AUTOAPPLY** | **AutoApply AI** · Chrome MV3, FastAPI, pgvector, React, Fly.io · 2026 | Chrome MV3 extension plus FastAPI backend automating discover -> tailor -> apply -> track. Six-provider LLM routing cascade (Claude, GPT-4o, Kimi, Ollama, Gemini, Groq), pgvector retrieval grounding answers in prior work history, 11 ATS adapters handling shadow-DOM isolation and offline queuing. 40+ endpoints, 9-page React dashboard, **355 backend tests**. | **Private production deployment. Public launch gated behind tracked security fixes — never call it a public product.** | C, D, E, F |
| **P-JOBSCOUT** | **JobScout** · Python, SQLite, GitHub Actions, TF-IDF · 2025 | 109 career pages monitored, TF-IDF matching against 95+ resume variants, Discord and Telegram alerts, zero-cost GitHub Actions architecture, SQLite WAL, self-healing circuit breakers isolating a failing scraper. | `[VERIFY]` FastAPI vs Flask against the repo. | F, A |
| **P-RECON** | **Self-Healing Trade & Position Reconciliation** · in progress | CDC mismatch detection generalized to trade and position reconciliation with automated recovery. | **In progress — label as such or omit.** `[VERIFY]` whether this is what earlier notes call SENTINEL. SENTINEL is referenced as the lead finance project but defined nowhere. Resolve the name or drop it. | B |

**P-HOOKS is your highest-value project and it has appeared on no resume.** It is the only artifact a
reviewer can verify without asking you — installable, signed, statically analyzed, 330 tests on PyPI.
It answers "is the Python real?" in one line, which no private repo can do.

---

## Education and credentials
- **MS Information Science and Technology**, Missouri University of Science and Technology,
  GPA 4.0/4.0, Jan. 2022 – Dec. 2023
- **B.Tech Mechanical Engineering**, Gayatri Vidya Parishad College of Engineering, Visakhapatnam,
  2017, 76.28%, First Class
- DP-700 Microsoft Certified Fabric Data Engineer Associate · CSPO · Tableau Analyst badge
- Publication DOI 10.1080/10495142.2025.2525123 · OSS contributor: andrewyng/context-hub `[VERIFY]`
  GitHub check, Oct. 1, 2026: PRs #160 and #163 are open and #161 closed without merge; nothing
  merged. Do not put "contributor" on a resume until a PR merges.
- Sar Pass Himalayan trek, 13,850 ft (YHAI) — only if the JD or culture page invites it

---

## Dollar translation — for the non-technical reader

**Defensible total ~$566K/year in recovered cost and capacity, about 5.4 FTEs.**

| Change | Hours/yr | Dollars/yr | Derivation |
|---|---|---|---|
| E-IDEM provisioning | 5,000 | **$300,000** | 20 runs/day × 1 hr active engineering × 250 days × $60/hr |
| E3-FABRIC tickets -40% | 5,400 | **$243,000** | 600 fewer tickets/mo × 45 min × $45/hr |
| E-REPORTAUTO | 390 | **$23,400** | 15 hrs/sprint × 26 sprints × $60/hr |

Ticket sensitivity, pick the true handle time: 30 min -> $162K · **45 min -> $243K** · 60 min -> $324K ·
4 hrs -> $1.30M, which implies 14.4 FTEs, 9% of a 160-person company, and fails on sight.

**Plain-English phrasings for recruiters, LinkedIn, and skip-levels**
- "Database setup was an hour of manual work, twenty times a day. I automated it — 5,000 engineering
  hours a year, about $300,000, or two and a half engineers back on product."
- "Cutting support tickets 40% freed roughly 5,400 analyst hours a year, about $243,000. They can
  add clients without adding support headcount."
- "The old design copied every database in full every fifteen minutes — two and a half million
  terabytes a year. The new one moves only what changed: 257 terabytes."
- "Downtime went from about seven days a year to under nine hours. On payroll, that's the difference
  between people getting paid on time and not."
- "Releases went from once a quarter to every two weeks. Client changes ship in weeks, not seasons."

### `[BLOCKED]` dollar claims
- **$609,809/yr CDC savings.** Models a 15-minute full-refresh architecture that was never running;
  the real before-state was a ~30-minute batch. ~$246K is cloud egress you likely do not pay on-prem,
  and $150K is estimated SLA penalties, not incurred. Cost avoided under a hypothetical, not saved.
  Use as an architecture argument only. *To make it real: get the monthly infrastructure bill for
  those pipelines before and after. A five-minute ask that becomes your best business number.*
- **$1.29M support savings** — see sensitivity above.
- **$250K preserved client revenue** — the 2.5 churning clients are not evidenced, and $100K per
  client contradicts the $37.8K/yr benchmark inside the same analysis.
- **Per-server throughput.** 70.31 TB over 10 servers is 7.03 TB each, needing ~**7,800 MB/s**
  sustained, not 781. The argument gets stronger; the stated figure is off by 10x.

---

Arcs, project selection, and the adjacency table live in `arcs.md`.
