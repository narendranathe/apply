# Resume source migration provenance

The repo previously held an older 32-bullet AI Engineer source. On October 11, 2026 the newer verified Mac master was imported as the baseline, before tailoring; its content is not a per-role variant. The source uses 23 bullets and the Data Platforms & Applied AI narrative. The ground-truth mirror differs from the Mac original only in its mirror prolog, line wrapping and the arcs filename reference.

| Source | Destination | SHA-256 |
|---|---|---|
| `/Users/naren/Downloads/files/Narendranath-Edara-Resume.tex` | `resumes/master/Narendranath-Edara-Resume.tex` | `950b228677940e27d2295b096288253d64659d9dcbe26086b96b68b3e3ff9e47` |
| `/Users/naren/Downloads/files/v8preamble.tex` | `resumes/master/v8preamble.tex` | `12d30c1145aca2e7e2758a6b370d0e3f7eadf5680b00eb6023d59603222fe19c` |
| `/Users/naren/Downloads/files/resume_qa.py` | `scripts/resume_qa.py` | `4779bc232c3851f82236b2288be745b55fd1f36fd451c4be916f56f6ca687d1d` |

The frozen preamble is copied byte for byte. The original QA script is also unchanged. Preparation captures its full lint, budget and layout results without printing private source diagnostics. It additionally verifies real PDF text, ordering, hashes and rendered previews. A missing compiler/parser/renderer or failed QA blocks eligibility. No new PDF compilation was claimed on the Mac: TeX was not on the accessible runtime path. The cloud runner installs its toolchain and must pass actual compilation/layout checks.

The reviewed claim manifest is tied to the exact current master and ground-truth mirror hashes. Changed source requires manifest review. Corrected Portfolio Risk Analytics remains explicitly in progress and uses only the source-documented Delta ingestion fragment; blocked latency/end-to-end claims are excluded. Every application records its actual source, PDF, answer, JD and evidence hashes.
