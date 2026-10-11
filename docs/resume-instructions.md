# Resume engine — project instructions (mirror)

> Mirrored from the Mac original (`/Users/naren/Downloads/files/PROJECT_INSTRUCTIONS.md`) on
> 2026-10-11 for the multi-agent application pipeline. Mac original wins on conflict.

You build one resume per job description. `ground-truth.md` is the only source of facts. `arcs.md`
holds the six story arcs. The master `resumes/master/` is the base — tailor from it, never edit it
in place. (The Mac original references `v8preamble.tex` frozen and `EXAMPLE_2PAGE.tex` for shape;
the compiled master PDF in `resumes/master/` shows the target shape.)

Two readers must both be satisfied. The **technical reader** wants mechanism: what was built, in
which system, at what scale — and probes any bullet for five minutes. The **non-technical reader**
wants consequence: what changed and what it was worth. A bullet satisfying only one is incomplete.

**Nothing ships that is not in `ground-truth.md`.** No invention, no extrapolation, no rounding up.
Anything tagged `[VERIFY]` or `[BLOCKED]` does not ship — say so and stop that line.

## Input
`Company: / Role: / Job Description: / Notes: (URL, posting age, referral, ATS, page limit)`
All present → no questions. Run the Gate, then Steps 1–9.

## Gate — on fail, two lines of reason, then stop
- **G1** Sponsorship: recent H-1B filings. Nothing in two fiscal years → skip unless referred.
- **G2** Work-authorization line, free-text fields only, **never on the resume**: *"Current H-1B
  holder working in the United States. Seeking a change of employer petition filed from within the
  US — no cap-subject lottery selection required."* Do not assert exemption from the $100,000
  proclamation fee; that is contested and under appeal.
- **G3** ≥70% of must-haves truthfully evidenced, else recommend skipping.
- **G4** Posting ≤72h preferred, >14 days deprioritize.
- **G5** 3 years hands-on data engineering, 6 years professional. "7+ years pipelines" or Staff scope
  → skip unless referred. The 2018–2021 analytics years never inflate a pipeline-tenure claim.
- **G6** List every knockout question the posting implies — work authorization, minimum years,
  location. These cause most fast rejections, not the resume. Answer honestly and completely.

## Step 1 — Requirement map, then translation map
Rank top-5 must-haves and nice-to-haves. List the JD's recurring domain nouns; they must appear
inside Experience bullets, not only Skills. Map each must-have to exactly one ground-truth ID. No
evidence = GAP: use the Adjacency table or name it. A bullet mapping to nothing is filler.

Then name each point's consequence: **money** (dollars per period), **time** (hours or cycle time),
**risk** (what could have failed and now cannot), **scale** (who or how much is served — context,
not a result). Scale alone is not a consequence. Every bullet needs mechanism **plus** money, time,
or risk. Missing consequence → pull a different point or flag `[NEEDS TRANSLATION]`. Never invent.

Write the **thesis** — the one sentence this resume argues — before any bullet.

### AI/data-platform narrative check

When an AI or AI-platform role is in scope, rank claims by owned and shipped work: (1) deployed
employer systems or context models, (2) research with an attributable publication or evaluation,
(3) independent model, retrieval, or agent projects, (4) use of coding assistants. Show the
progression across employers and projects rather than concentrating all AI language in the current
role. For each proposed AI bullet, record its artifact, inputs/context, user, validation step, and
observed money/time/risk consequence in ground truth. Keep AI-assisted development separate
from building or training an ML model; a schema/semantic context model is not a trained model.
Never move an independent project into employer Experience. Do not attribute a metric from one
system to another. A publication title and DOI must identify the same paper in a primary source.

## Step 2 — Arc
One primary arc from `arcs.md`, chosen by the JD's own language. **Every two-page build is primary
arc on page 1, Arc F on page 2.** Never two primary arcs.

## Step 3 — Page mode
Default two pages. Page 1 must stand alone: the scan is 6–10 seconds and reviewers rarely reach page
2 during it. The last page must fill ≥60%, or it reads as failure to edit. **Page 2 is earlier
employment, projects, and education — never diluted ExponentHR content.** One page when the posting
demands it, the role needs under three years, or a hard limit applies. A real two pages needs ~34
bullets and ~1,300–1,400 words.

## Step 4 — Composition
**Page 1:** header · summary 3 lines · Technical Skills 3–4 rows · **ExponentHR** role-context +
6–8 bullets · **Missouri S&T** role-context + 3 bullets.
**Page 2** (flows naturally): **Zomato** + 3–4 · **Udaan** + 2–3 · **C2FO** + 2 · **Projects** 3–5
with 1–2 bullets each · Education & Certifications.

Every role opens with a one-line role-context (105–129 chars): naming the system, who it serves, the
scale, and what he owns. Never open a role with a bullet.

## Step 5 — Budgets (plain-text characters, spaces included)
| Element | Budget |
|---|---|
| Context line, 1-line bullet | **105–129** |
| DEAD ZONE — BANNED | **130–199** |
| 2-line bullet | **200–245** |
| 3-line bullet | BANNED |
| Summary 2-line / 3-line | ≤**268** / **375–400** |
| Summary DEAD ZONE | 269–374 |
| Skills row | **105–132**, or two full lines 240–268 |

Lengthen with a metric, named system, or consequence — never adjectives. Shorten by cutting
adjectives or the weakest tool — never the number. Aim mid-band.

## Step 6 — Bullets
`[consequence: money/time/risk] + [mechanism: action, system, technology] + [scale if it fits]`

Both halves mandatory. **Every metric needs a baseline** — "30 minutes to under 8 minutes" beats
"67% faster". **Pair speed with the reliability it held**; a speedup hiding a regression is the
bait-and-switch reviewers watch for.

**Jargon rule:** any acronym a non-technical reader would not know — CDC, AAG, MERGE, HPA, SSIS,
QLoRA, GRN — sits beside a plain-language clause in the same bullet or that role's context line.
Keep the acronyms for ATS; surround them.

**Verbs:** Built, Reengineered, Automated, Shipped, Cut, Compressed, Migrated, Optimized, Owned,
Recovered, Root-caused, Standardized, Hardened, Traced, Delivered, Held, Filtered, Grew, Indexed.

**Banned** (generic phrasing measurably underperforms on keyword match): leveraged, spearheaded,
utilized, passionate, dynamic, results-driven, exuberant, seamless, cutting-edge, synergy, robust,
"responsible for", "strong business acumen", "recognised for", bare "cross-functional collaboration".

## Step 7 — ATS and bolding
Exact-match terminology from the JD wins; their noun beats yours. **Never keyword-stuff, never hide
text** — both are actively detected and flag an application as manipulative. If a budget will not
fill truthfully, cut the bullet.

Bold only exact JD terms or a headline metric. Max 3 words per bold, max 1 per bullet, **5–8 on page
1, 3–5 on page 2**. Titles, section names, Skills labels and context lines don't count. At least one
bold in each of the first two ExponentHR bullets.

Parse safety: single column, no tables or images, 10pt, standard section names exactly
`Technical Skills`, `Experience`, `Projects`, `Education & Certifications`, dates `Mon. YYYY - Mon. YYYY`,
plain hyphens only.

## Step 8 — Assemble
Tailor `resumes/master/Narendranath-Edara-Resume.tex` per the arc and JD. Keep the frozen preamble
geometry: letterpaper, 10pt, 0.33in side margins, single column, no tables.

## Step 9 — Verify
Lint for live `% $ _ & #`. Check every bullet against the budgets in Step 5. Compile and confirm
2 pages, per-page fill (non-last 90–100%, last ≥60%), no overfull boxes. Read the ATS text
extraction for ordering and garbage. Glance at a rendered preview.

**Fill Order:** 7th–8th ExponentHR bullet → 4th Zomato → 3rd Udaan → C2FO to a full block → 2nd
bullet per project → 4th–5th project → 4th Skills row.
**Cut Order:** 5th project → 2nd bullets on weakest projects → 3rd Udaan → 4th Zomato →
weakest-mapped ExponentHR bullet → 3rd Summary sentence → one page.

## Three tests
1. **6-second, page 1:** name + Summary sentence 1 + Skills row 1 + ExponentHR context + bullet 1.
   Would this recruiter shortlist from those five lines? If not, rewrite them first.
2. **VP test:** someone who does not know what CDC means must be able to say what he owns, what
   changed, and roughly what it was worth.
3. **Probe test:** every bullet survives five minutes of "walk me through exactly how, and how you
   know that number."

## Output contract (per application folder)
```
Mode: 1-page | 2-page  (one line of reason)
Gate: PASS | FAIL      (evidence; knockout questions listed)
Arc: primary + F
Thesis: one sentence
Map: must-have -> ground-truth ID -> consequence; GAPs and [BLOCKED] flagged
Bolds: page 1, then page 2
QA: pages, per-page fill, bullets, flags
```
Then: `changelog.md` (every change vs master + JD mapping), the tailored `.tex`/`.pdf`,
`answers.md`. Naren approves all three before anything is submitted.

**One JD per application folder.** Iterate by patch, not regeneration.
