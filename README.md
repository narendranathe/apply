# apply

Private code for one coordinated job application and relationship workflow. GitHub Actions executes the lanes; the existing Notion Hiring & Outreach source holds operational records. SQLite and full outreach logs are authenticated encrypted recovery archives.

The [V2 master prompt](docs/migration/ONE_PASS_CLOUD_MIGRATION_PROMPT_V2.md) and [cutover checklist](docs/migration/ONE_PASS_CUTOVER_CHECKLIST_V2.md) govern this migration. Reports distinguish verified engineering from deployment gates.

| Lane | America/Chicago schedule | Behavior |
|---|---|---|
| Accepted relationships | 06:00 daily | Replies, relationships, referrals and verified-context drafts |
| First touch | 06:30 daily | Fresh roles, contact routes, prioritization and drafts |
| Applications | Separate phase after 06:30 discovery | Qualify, prepare, permitted submission, verified receipts |

Applications have standing authorization when every truth, dedup, artifact, answer, site-permission and receipt gate passes. Unknown facts or unavailable methods create exceptions while other roles continue. Attempts never count as verified applications. LinkedIn has no automated sender here; execution remains human absent a permitted integration.

One writer uses deterministic immutable records, fresh preflight and readback. Notion has no transactional uniqueness guarantee; uncertainty requires reconciliation. applications/registry.json is an ignored compatibility import/export view, not competing cloud state.

Run validation:

```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 scripts/security_check.py
python3 scripts/check_workflows.py
```

Actions manual dry-run provides cloud shadow validation, with writes in memory and no sends/submissions. Schedules remain guarded by CLOUD_ENABLED and verified Notion cutover evidence until history comparison, restored backups, shadow tests and retirement of local writers all pass.

See [cutover and rollback](docs/runbooks/CUTOVER.md), [Notion schema](docs/NOTION_SCHEMA.md), [submission capabilities](docs/ATS_CAPABILITIES.md), and [inventory](docs/MIGRATION_INVENTORY.md).
