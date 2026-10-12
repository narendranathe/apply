# Encrypted source backup and restoration

Plaintext historical records stay in the ignored `.migration-private/` directory
with restrictive permissions. Git contains source code, sanitized inventories
and authenticated ciphertext only. Keys, session cookies, passwords and provider
credentials are excluded from archives and Git.

## Format and key custody

`scripts/encrypted_backup.py` prefers **age** when an approved recipient/identity
is supplied. Neither `age` nor `age-keygen` was installed during this migration.
The available fallback uses **AES-256-GCM** from `cryptography==50.0.2`, a fresh
96-bit random nonce for each archive, and an external 256-bit random key. The
format/version header is authenticated as associated data; GCM checks the entire
ciphertext before any restore file is written. It never uses unauthenticated
`openssl enc`, home-grown encryption, or a password-derived weak key.

The key was created outside every Git checkout, with mode 0600, then placed in the
user's secure Keychain by the coordinator. Exact readback matched. The Keychain
service is `narendranathe.apply.cloud-migration.v2`, account
`migration-backup-key`; the runner's `MIGRATION_BACKUP_KEY` secret was also set
through secure controls. No key material is printed, included
in reports, or committed. Keychain custody must be confirmed before deleting the
temporary external key. A key loss prevents recovery; keep an independently
protected recovery copy under the user's control. Do not place a key in GitHub
repository files or alongside the ciphertext.

## Reproducible backup

Use Python with the pinned cryptography dependency installed. The bundled local
Python discovered by `load_workspace_dependencies` already provides version
50.0.2; plain system Python did not. `sqlite_snapshot(source, destination)` uses
SQLite's backup API against a read-only source, includes committed WAL changes,
and verifies `PRAGMA quick_check`. It does not checkpoint or mutate the source.
Original log bytes are archived unchanged. A copied database snapshot is a
historical backup, never the cloud operational database.

Create a key in the secure staging directory only when a key does not exist:

```sh
python3 scripts/encrypted_backup.py keygen --key /private/tmp/approved-external-key.bin
```

Create and immediately verify an authenticated archive after the actual private
GitHub destination has been checked. Each file uses a unique archive-relative
name; never add browser or credential files:

```sh
python3 scripts/encrypted_backup.py create \
  --key /private/tmp/approved-external-key.bin \
  --private-destination-verified \
  --file journal/journal.sqlite3=.migration-private/journal-snapshot.sqlite3 \
  --file normalized/historical-export.json=.migration-private/historical-export.json \
  --output migration-archives/source-history.aesgcm
```

Production migration archives include both full logs, the original automation
prompts, non-credential workflow state, complete Notion source exports, and any
available application history. The authenticated tar manifest binds every
member name, byte length and SHA-256. A sanitized outer receipt records the
ciphertext hash, algorithm and restore result. Archive creation fails if the
private destination is not affirmed and refuses to overwrite an existing archive.

## Restore and verify

Retrieve the decryption key through secure local controls into a temporary file
outside Git with mode 0600. Never paste it into chat or put it in a shell command.
Restore into a new disposable directory:

```sh
python3 scripts/encrypted_backup.py restore \
  --key /private/tmp/approved-external-key.bin \
  --archive migration-archives/source-history.aesgcm \
  --destination /private/tmp/apply-disposable-restore
```

The restorer authenticates before writing, rejects absolute/traversal/symlink and
duplicate members, compares the member set to the manifest, checks every length
and SHA-256, uses 0700 directories and 0600 files, and refuses a nonempty target.
`create_backup` performs this restore automatically and independently rechecks
every restored hash. A failed restore removes the candidate archive. Inspect the
restored SQLite with read-only connections and `PRAGMA quick_check`.

## Historical import and rollback

`scripts/verify_migration_archive.py` now verifies every committed archive and
receipt, restores each into separate private directories and combines only
identical immutable identities. The hosted importer uses
`combined-historical-records.json`. The October 12 application delta adds seven
records to the unchanged 703-record baseline, for 710 combined records. Its
41 members independently restored with exact hashes; its receipt uses opaque
member IDs so private source paths remain inside authenticated ciphertext.
Historical packet export through `scripts/registry.py packet` verifies every
source byte/hash and preserves dependency files, without certifying fresh QA.
The subsequent `16de4ac` delta adds seven more immutable revisions for 717
combined records. Its 44 members preserve the externally reported Goldman
submission and contradictory draft folder, which normalize to uncertainty
rather than a verified receipt. The three-archive restore verifies 682 + 41 + 44
members and fails on changed immutable identities.

`scripts/migrate_history.py` exports only beneath `.migration-private/` and prints
counts, never personal payloads. The export retains raw source provenance beside
unchanged canonical event bodies. A reconciliation run is read-only by default;
`--write` additionally requires a live GitHub API private-destination check, verified runner
credentials and no source conflicts. Imports use stable IDs, canonical JSON
payload hashes, exact status comparisons and post-write readback. Changed rows
become conflicts. An uncertain write is not retried. No historical invitation,
message, active claim or application is replayed by the importer.

Before cutover, keep both original local tasks active and cloud external effects
disabled. After a validated cutover, pause cloud execution before restoring local
operation, reconcile ambiguous in-flight effects and import the final delta.
Never restore a SQLite snapshot as a competing live cloud database or re-enable
both writers. Archive source corrections append new records and new ciphertext;
they do not rewrite prior evidence.

## Verification evidence and retention

The migration suite passed **14 tests**, covering exact source precision/actors, both matching mirrors,
hash validation, dry-run/private gates, duplicate IDs, human state conflicts,
uncertain readback, authenticated encryption, wrong-key/tamper rejection, path
and credential-class rejection, and committed SQLite WAL restoration. The
preserved legacy suite passed **85 synthetic tests**. The real source archive
`migration-archives/2026-10-11-v2-history.aesgcm` was restored into a disposable
directory: **682 files / 22,615,298 bytes**, every SHA-256 matched. Its 2,161,171
ciphertext bytes have SHA-256
`c98be96dc71d26b11ae26ad53e5476c89819ff22b2c6d2c1f0d6fa78686976d1`.
The sanitized `2026-10-11-v2-history.receipt.json` binds the archive and member
hashes. The cloud importer restores the member
`normalized/merged-historical-records.json`, containing the 703-record V1
historical-export envelope. The archive includes both logs, the consistent
journal and JobScout snapshots, original automation files, all 592 Notion bodies,
paged source rows, normalized inputs and three recent-turn chat references.

For a final source delta, `merge_history` verifies both ordinary ID/hash equality
and the two narrowly scoped legacy metadata variants with correction proofs
present in the freshly fetched remote event set. Reproduce the merge separately
from a live import, using a new output filename:

```sh
python3 scripts/migrate_history.py \
  --input .migration-private/historical-export.json \
  --merge-notion-records .migration-private/notion-normalized-records.json \
  --merge-notion-events .migration-private/notion-event-payloads.json \
  --output .migration-private/reconciled-delta.json
```

The implementation reproduced the exact 703 record bodies in the archived input:
352 exact remote event matches, two approved variants, no remote-only events and
zero unresolved conflicts. Future unexpected differences block the merge.

Keep verified ciphertext until the user chooses a retention policy. Raw exports
may be removed after full reconciliation and secure-key custody are confirmed;
existing original logs are retained. Remove disposable restores and temporary
key files after verification. No automated plaintext or key deletion is performed
while source reconciliation or Keychain custody remains incomplete.
