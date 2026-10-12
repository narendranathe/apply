# Changelog vs master resume — GS Product Manager Analyst - API (2026-10-12)

Variant: `resumes/variants/2026-10-12-goldman-sachs-product-manager-analyst-api-programmatic-channels-transactio.tex/.pdf`
(compiled PDF in this folder is the working copy; the `.tex` is the source of truth)

Mode: 2-page (JD is a product-analyst role rewarding breadth: APIs + docs + metrics + product requirements)
Gate: PASS — G1 Goldman Sachs is a major H-1B sponsor (presumed pass; large bank, regular filings);
G3 all top-5 must-haves truthfully evidenced (see Map); G4 posting ~2 days old; G5 0-2yr role, no
tenure inflation; G6 knockouts answered honestly in answers.md (H-1B transfer, Dallas location, 6yr/3yr).
Arc: F primary (product-facing) + F page 2.
Thesis: Naren builds APIs and the developer tooling around them, writes the docs developers onboard
with, and has already done product-requirements work inside a B2B fintech — the shape of an API
product analyst for Programmatic Channels.

## Requirement map (must-have -> ground-truth ID -> consequence)
1. REST APIs, JSON, OpenAPI, SDKs -> P-FRAUD (FastAPI REST API, idempotent writes; P99 1.12ms
   per-request scoring latency), P-AUTOAPPLY (FastAPI backend, 40+ REST endpoints), P-FINTUNE
   (FastAPI serving). Adjacency: hand-written OpenAPI specs and SDK authorship not directly
   evidenced; FastAPI auto-generates OpenAPI docs and JobScout consumes REST APIs.
2. Figma / Framer / Retool -> [STATED] 2026-10-12 solid hands-on Retool and Figma (added to
   ground-truth.md). Framer not evidenced; JD says "or similar design tools".
3. Analytical/problem-solving -> M1 (95% anomaly detection, leak caught 4h early), Z-COMP
   (9% share gain), U-FORECAST (ROI +7%).
4. Written communication / technical documentation -> E-DOCS (new hires onboard from his docs),
   M4-PAPER (peer-reviewed publication, DOI 10.1080/10495142.2025.2525123).
5. 0-2 years technology/fintech/payments/banking -> exceeded: 6 years professional, 3 years data
   engineering; fintech evidence via C2FO (B2B fintech) and P-FRAUD (payments-adjacent fraud scoring).
Nice-to-haves: product requirements/user stories -> C-PRD (PRDs at C2FO, resource time -50%);
testing/validation -> P-HOOKS 330+ tests, P-AUTOAPPLY 355 tests, P-FINTUNE 35+ tests;
AI-enabled dev workflows -> E-AICTX (Claude Code harness, team-adopted), P-HOOKS (LLM agent tooling);
payments/treasury interest -> P-FRAUD, C2FO, E-PTO (money-adjacent, not on resume).

## Changes vs master

### Summary — REWRITTEN
Was Arc E (AI platform engineer). Now: "AI Engineer and API builder with a data-platform
foundation..." routing to API product work in transaction banking (JD: APIs, developer experience,
foundation in product management). 391 chars, 3-line band.

### Technical Skills — RESTRUCTURED
- Row 1 REPLACED "AI & ML" lead with "APIs & Developer Platforms: FastAPI, REST APIs, JSON,
  OpenAPI documentation, API testing, pgvector, SDK integration" (JD must-have: REST/JSON/OpenAPI/SDKs).
- Row 4 REPLACED "Serving & Reliability" with "Cloud, Tools & Design: Azure (AKS, DevOps, Data
  Factory), Microsoft Fabric, Docker, Retool, Figma, CI/CD, Grafana" (JD must-have: Figma/Retool).
- "AI & ML" and "Data & Streaming" rows kept, reordered after the API row.

### ExponentHR (8 bullets)
- ADDED E-DOCS as bullet 1: onboarding docs new hires read (JD responsibility #1: developer-facing
  documentation). Was absent from master.
- ADDED E-REPORTAUTO as bullet 6: automated Git and Azure DevOps API workflows in Python, 390 hrs /
  $23K yr (JD: APIs, implementation tooling). Was absent from master.
- MOVED E-AICTX to bullet 2, reframed as "internal developer tooling" (JD: developer tooling,
  AI-enabled development workflows).
- REMOVED E5-RECOVERY (Contained AAG failover runbooks) and E-RETENTION (hard-delete history
  recovery): reliability bullets with no JD mapping; page-1 space reserved for DX evidence.
- KEPT E3-FABRIC, E1-CDC, E2-CICD, E-IDEM, E-SELFHEAL (reordered API/DX-first).

### Missouri S&T (3 bullets) — KEPT, minor wording
- "95\%" -> "95 percent", "dynamic" -> "autoscaled" (banned-word compliance). Content unchanged.

### Zomato (4 bullets)
- REMOVED Z-PNL (portfolio unit economics -18 to +2 Rs/order) and Z-GROWTH (14% quarterly growth):
  commercial bullets with no JD mapping.
- Z-ES EXPANDED: added DX consequence ("turning the support queue into a self-serve search box")
  (JD: developer experience, support).
- Z-CAMPAIGN REFRAMED to lead with analytics work rather than revenue (JD: analyze metrics).
- KEPT Z-COMP, Z-SEARCH.

### Udaan (3 bullets)
- REMOVED U-BCP (business continuity plan) and U-TEAM (hired 10+ sourcing team): no JD mapping.
- ADDED U-RCA as standalone bridge bullet: root-cause discipline (Arc F bridge; JD: analytical
  problem-solving).
- KEPT U-FORECAST (ROI +7%; $4M [BLOCKED] held out), U-GMV.

### C2FO (2 bullets) — KEPT
- C-PRD kept, "product requirement documents" bolded (JD: product requirements, user stories).
- C-SQL trimmed to 1-line band (119c).

### Projects (5 projects, 10 bullets) — REORDERED API-first, honesty corrections applied
- P-FRAUD moved first: "Built a FastAPI REST API..." — REMOVED "100+ TPS sustained" phrasing per
  Oct 1 README correction (1.12 ms is per-request P99, 100 TPS is producer rate); synthetic-data
  honesty stated plainly in bullet 2.
- P-AUTOAPPLY: "40+ REST endpoints" bolded (JD: REST APIs); 355 tests kept.
- P-FINTUNE: kept; ADDED circuit-breaker 2nd bullet.
- P-HOOKS: kept; ADDED stdlib/DX 2nd bullet ("bar for developer trust").
- P-JOBSCOUT: kept, trimmed.
- REMOVED "exactly-once" (never claimed; ground truth says idempotent/no-op replays).

### Education — UNCHANGED

## Bolds
Page 1 (6): onboarding docs, developer tooling, 40 percent, under 8 minutes, API workflows, $300K.
Page 2 (5): 80 percent, product requirement documents, REST API, 40+ REST endpoints, 330+ passing tests.
All are exact JD terms or headline metrics; max 3 words, max 1 per bullet.

## QA
LINT PASS · BUDGET PASS (29 bullets, summary 391c, all contexts/skills in band) · LAYOUT PASS
(pages=2, fill 90.9%/68.8%, 0 overfull, 1328 words, ATS text extraction reads in order).

### 2026-10-12 revision (his feedback: experience order)
- Experience reordered to reverse-chronological: ExponentHR → Missouri S&T → C2FO Product Intern
  → Udaan → Zomato (was: ExponentHR → S&T → Zomato → Udaan → C2FO). Titles, dates, bullets unchanged.
- C2FO bullet 2 expanded into the 2-line band ("shaping the preferred-offers roadmap") to hold
  page-1 fill; explicit page break before the Zomato block (page 1: 90.9%, page 2: 68.8%).
- Note: the master resume carries the same non-chronological order; left untouched per
  never-edit-master-in-place — flagging in case he wants the master fixed too.
