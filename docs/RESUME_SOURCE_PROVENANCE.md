# Resume source migration provenance

The repo previously held an older 32-bullet AI Engineer source. On October 11, 2026 the newer verified Mac master was imported as the baseline, before tailoring; its content is not a per-role variant. The source uses 23 bullets and the Data Platforms & Applied AI narrative. At initial import, the ground-truth mirror differed from the Mac original only in its mirror prolog, line wrapping and the arcs filename reference. The reviewed repository addition below is separate from that original import.

| Source | Destination | SHA-256 |
|---|---|---|
| `/Users/naren/Downloads/files/Narendranath-Edara-Resume.tex` | `resumes/master/Narendranath-Edara-Resume.tex` | `950b228677940e27d2295b096288253d64659d9dcbe26086b96b68b3e3ff9e47` |
| `/Users/naren/Downloads/files/v8preamble.tex` | `resumes/master/v8preamble.tex` | `12d30c1145aca2e7e2758a6b370d0e3f7eadf5680b00eb6023d59603222fe19c` |
| `/Users/naren/Downloads/files/resume_qa.py` | `scripts/resume_qa.py` | `4779bc232c3851f82236b2288be745b55fd1f36fd451c4be916f56f6ca687d1d` |

The frozen preamble is copied byte for byte. The original QA script is also unchanged. Preparation captures its full lint, budget and layout results without printing private source diagnostics. It additionally verifies real PDF text, ordering, hashes and rendered previews. A missing compiler/parser/renderer or failed QA blocks eligibility. No new PDF compilation was claimed on the Mac: TeX was not on the accessible runtime path. The cloud runner installs its toolchain and must pass actual compilation/layout checks.

The reviewed claim manifest is tied to the exact current master and ground-truth mirror hashes. Changed source requires manifest review. Corrected Portfolio Risk Analytics remains explicitly in progress and uses only the source-documented Delta ingestion fragment; blocked latency/end-to-end claims are excluded. Every application records its actual source, PDF, answer, JD and evidence hashes.

## Additive fact-source review, October 12, 2026

[Repository commit 194d6e2](https://github.com/narendranathe/apply/commit/194d6e2) added a paragraph recording solid hands-on Retool and Figma experience as `[STATED]`, attributed there to the user's direct statement. This audit establishes repository provenance; it does not independently establish the original conversation or upgrade the statement to `[DOC]`. The statement supports tool-use keywords only, with no specific employer, deliverable, production scope, or timeline.

Manifest review confirmed that all 82 evidence-table lines and every row used by the 23-entry master claim map remain byte-identical to the previously reviewed source. The accepted fact-source SHA-256 is now `e09e1bb05ea9c164e5f543903d1d1c70bc90727361f6d5fff71fd47213add74f`; the master remains `950b228677940e27d2295b096288253d64659d9dcbe26086b96b68b3e3ff9e47`. The hash update accepts only this reviewed additive source revision. It does not add Retool/Figma claims to the existing master or cloud skill map, nor waive review for later source changes.

## Hosted build verification

The first hosted build compiled two pages but used Computer Modern fallback fonts because the `lmodern` LaTeX package was absent. Its log disabled font expansion; strict QA correctly rejected a 232-character summary wrapping onto a stub third line and a 222-character project bullet wrapping onto three lines. Both master and preamble hashes matched the imported sources exactly. The existing Mac PDF passed the same geometry checks with Latin Modern fonts, using the same pinned PDF parser version.

Installing `lmodern` explicitly in the hosted toolchain fixed the cause. [Hosted CI run 38193282341](https://github.com/narendranathe/apply/actions/runs/38193282341) then ran the actual master compilation and strict PDF QA successfully: 121 tests passed with no skips, plus 85 legacy tests. No resume facts, master source, frozen preamble, or QA thresholds changed for this fix. Runner workflows install the same font dependency.

QA records contain safe failure measurements. Full diagnostic text is retained only with the private packet and encrypted hosted diagnostics; plaintext PDFs, renderings, logs and restored diagnostic files remain excluded from Git.

The later `16de4ac` upstream QA script and its source rule are preserved verbatim in the encrypted delta. The original imported QA script retains its exact hash. The cloud preparation layer adds the compatible reverse-chronological gate separately: exact employers and descending source start dates plus the compiled PDF's Experience order. New tests reject misplaced roles and later dates; the hosted build must pass both gates.
