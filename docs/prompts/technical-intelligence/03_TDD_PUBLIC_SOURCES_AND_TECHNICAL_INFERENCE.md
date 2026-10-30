# Prompt 3 — Technical-source ingestion, deep analysis and hypothesis testing (TDD)

Implement source discovery and engineering analysis from the approved PRD using the shared schemas and agent protocol. Prefer sanctioned public RSS/Atom feeds, official engineering blogs, documented GitHub releases/commits, company engineering announcements, public Substack feeds, and user-supplied article URLs. LinkedIn and X content may be ingested only through current authorized APIs/exports or clearly permitted sources. Do not circumvent logins, robots/access restrictions, rate limits, or paywalls.

**Tests FIRST:** Build offline HTML/RSS/API fixtures and failing tests for canonical URL dedup, RSS update cursors, revised articles, deleted/unavailable content, authorship ambiguity, URL redirects and SSRF protection, source freshness, access denial, multiple languages, rate limits, citation integrity, unsupported provider access, and retries without duplicate records. Only then implement the minimum sources and adapters.

Implement a bounded source collector with allowlists, fetch timestamps, content hashes, short excerpts and source citations. Cache research in the existing Notion knowledge architecture, adapting the archived Company Blog Cache idea without presuming its old page/database is accessible. Do not store copyrighted full articles unnecessarily. Support incremental lookups and cost-bounded context packets.

Add a technical-analysis pipeline with separate objects for:
1. **Observed engineering facts** grounded in exact evidence.
2. **Hypotheses** about probable team priorities or architecture, explicitly labeled inferred and scored for confidence.
3. **Engineering opportunities** with concrete before/after design, failure modes, feasibility, operating cost, reliability/security/performance trade-offs, validation experiments and potential counterarguments.
4. **Authentic contribution angles** linking relevant verified candidate work or independently documented projects, never inventing production AI experience.

Test with adversarial cases: a model mistaking an author's opinion for company policy, inferring a secret roadmap, inventing benchmark numbers, overstating an unrelated personal project, regurgitating source material, and producing banal generic advice. Use human-reviewed golden examples and deterministic validation before LLM output is accepted. Report evidence coverage, uncertainty, novelty, technical depth, estimated research cost and token consumption.

**Exit criteria:** Red/green/refactor evidence, source adapters or explicit unavailable capabilities, source-to-signal-to-hypothesis traceability, durable Notion summaries, privacy and rate-limit tests, and a feature PR. No automatic social actions.
