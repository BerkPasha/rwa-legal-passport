# Stage 9B-3 Database Migration Plan — awaiting approval

**Status: APPLIED to the local development database on 2026-10-15, after approval.**

| | Before | After |
|---|---|---|
| `rwa_legal_passport.db` SHA-256 | `fe84578d885ca588…` | `1cccebdaefdbf980…` |
| Records | 0 | 0 |
| Schema | Pre-9B-3 | Identical to the models |

Backups:
- `backups/rwa_legal_passport.pre-9b3.20261015-234220.db`, written by `--apply` and verified;
- the full project snapshot at `C:\Users\berkf\rwa-legal-passport-backups\pre-9b3-migration-20261015-234159\`.

The rest of this document is the plan as approved.

## 1. Why a migration is needed

Stage 9B-3 binds every approval to the exact document version, evidence and material asset information. It also tracks material asset changes. This needs new columns:

| Table | Column | Type | Existing rows get |
|---|---|---|---|
| `assets` | `material_revision` | `INTEGER NOT NULL DEFAULT '1'` | `1` |
| `review_approvals` | `document_id` | `VARCHAR(36) REFERENCES documents (id)` | `NULL` |
| `review_approvals` | `document_version_id` | `VARCHAR(36) REFERENCES document_versions (id)` | `NULL` |
| `review_approvals` | `document_version_number` | `INTEGER` | `NULL` |
| `review_approvals` | `document_sha256` | `VARCHAR(64)` | `NULL` |
| `review_approvals` | `evidence_snapshot` | `TEXT` | `NULL` |
| `review_approvals` | `evidence_digest` | `VARCHAR(64)` | `NULL` |
| `review_approvals` | `asset_material_revision` | `INTEGER` | `NULL` |
| `review_approvals` | `asset_snapshot` | `TEXT` | `NULL` |
| `review_approvals` | `asset_snapshot_digest` | `VARCHAR(64)` | `NULL` |
| `review_approvals` | `binding_digest` | `VARCHAR(64)` | `NULL` |

Nothing is deleted, renamed or rewritten. No table or index is added.

## 2. Effect on existing data

- The current development database is **empty** (0 rows in all 8 tables), so no record is affected.
- In general: assets start at revision 1. Approvals created before the migration stay in the history with `NULL` bindings. The application treats them as never current (`unbound_legacy_approval`), so their passport returns 409 until a reviewer re-reviews and approves again. This is intentional: an approval whose exact content was never recorded cannot be presented as current.

## 3. Procedure

Script: `scripts/migrate_9b3_approval_integrity.py`.

```powershell
cd C:\Users\berkf\rwa-legal-passport
# 1. Dry run (default): read-only, runs on an in-memory copy, prints the plan and the verification result
.venv\Scripts\python scripts\migrate_9b3_approval_integrity.py --db rwa_legal_passport.db
# 2. Apply (only after approval)
.venv\Scripts\python scripts\migrate_9b3_approval_integrity.py --db rwa_legal_passport.db --apply
```

`--apply` does the following:

1. Writes `backups/rwa_legal_passport.pre-9b3.<timestamp>.db` with the SQLite online-backup API, and verifies it (`integrity_check`, identical row counts).
2. Runs the 11 `ALTER TABLE … ADD COLUMN` statements in one `BEGIN IMMEDIATE` transaction.
3. Commits only if all of these hold: `integrity_check` is ok, `foreign_key_check` is empty, the row counts are unchanged, and the schema equals a fresh `create_all` of the models (columns, types, NOT NULL, defaults, primary keys, foreign keys, indexes). Otherwise it rolls back.
4. Is idempotent: on an already migrated database it only verifies.

After applying, start the server as usual; the startup schema check must pass.

## 4. Verification already performed (without touching the real database)

| Check | Result |
|---|---|
| Dry run against `rwa_legal_passport.db` | PASS: 11 statements, verification PASS, file hash unchanged, no backup written |
| Apply on four byte copies of the dev DB (scratch) | PASS: backup written and verified, schema identical to the models |
| Server on an **un-migrated** copy | Refuses to start (`SchemaOutOfDateError` listing the missing columns); copy unchanged |
| `tests/test_migration_9b3.py` (5 tests, real pre-9B-3 schema in `tests/fixtures/schema_pre_9b3.sql`) | Startup refused without changes; dry run changes nothing; apply backs up, migrates, keeps legacy rows and is idempotent; failed verification rolls back everything; a migrated legacy approval is never current until re-reviewed |
| Live HTTP suites on migrated copies | 9B-3 31/31, local setup 37/37, error messages 28/28, secure storage 29/29 |

## 5. Rollback

- **Before applying:** nothing to do.
- **After applying:** stop the server and replace `rwa_legal_passport.db` with the `backups/rwa_legal_passport.pre-9b3.<timestamp>.db` file written by `--apply`. The pre-9B-3 code then works with it again. The complete pre-9B-3 project snapshot, including this database, is at `C:\Users\berkf\rwa-legal-passport-backups\pre-9b3-20261015-230814\` (see its `RESTORE.md`).

## 6. Decision needed

Approve applying the migration to `rwa_legal_passport.db` (step 3, `--apply`). Until then, run Stage 9B-3 only against migrated copies.
