# Stage 9B-4 Step 7 Database Migration — correction page history

**Status: APPLIED to the local development database on 2026-10-16, after explicit approval.** This document records that migration and is the current procedure for any other database. Script: `scripts/migrate_9b4_correction_evidence.py`. Tests: `tests/test_migration_9b4.py`.

| | Before | After |
|---|---|---|
| `rwa_legal_passport.db` SHA-256 | `1cccebdaefdbf980…` | `09844f2c0ed0aed0…` |
| Records | 0 in every table | 0 in every table |
| Schema | Stage 9B-3 | Identical to the models |

Backups of the development database, both in `backups/` (not in version control):
- `rwa_legal_passport.pre-9b4s7-manual.20261016-155503.db`: manual copy taken before the run;
- `rwa_legal_passport.pre-9b4s7.20261016-160508-830515.db`: written and verified by `--apply`.

Verified after applying:
- existing data and the DDL of every other table unchanged;
- the new columns are NULL;
- a second dry run reports nothing to change;
- the startup schema check passes, and the application starts with `/health` 200.

Re-checked read-only during Stage 9B-4 step 11 (2026-10-16): dry runs of both migration scripts report nothing to change, `integrity_check` is ok, `foreign_key_check` is empty, and the file hash is unchanged.

---

## 1. Why the migration was needed

Before step 7, a reviewer correction recorded the value, unknown flag and quotation before and after, but not the cited page (9A finding D5). A changed page, possibly the page that makes a quotation verify, left no audit trace. Step 7 records it:

| Table | Column | Type | Existing rows get |
|---|---|---|---|
| `review_corrections` | `original_source_page` | `INTEGER` NULL | `NULL` |
| `review_corrections` | `corrected_source_page` | `INTEGER` NULL | `NULL` |
| `review_corrections` | `evidence_audit_version` | `INTEGER` NULL | `NULL` |

Nothing is deleted, renamed or rewritten, and no table or index is added. New corrections store `evidence_audit_version = 1`.

The API reports `page_history_recorded: true` for these records. Older records report `false`: their pages were never recorded, and the application does not invent them.

## 2. Procedure

Prerequisite: the database is already in the Stage 9B-3 state (`assets.material_revision`, `review_approvals.binding_digest`). Otherwise the script stops without changes.

```powershell
cd <project directory>
# 1. Dry run (default): opens the database read-only, migrates an in-memory copy, verifies, writes nothing
.venv\Scripts\python scripts\migrate_9b4_correction_evidence.py --db rwa_legal_passport.db
# 2. Apply (only after approval; stop the server first)
.venv\Scripts\python scripts\migrate_9b4_correction_evidence.py --db rwa_legal_passport.db --apply
# Optional: --backup-dir <directory>   (default: <database directory>\backups)
```

`--apply` does the following:
1. Writes `backups/rwa_legal_passport.pre-9b4s7.<timestamp>.db` with the SQLite online-backup API and verifies it (`integrity_check`, identical row counts).
2. Runs the three `ALTER TABLE … ADD COLUMN` statements in one `BEGIN IMMEDIATE` transaction.
3. Commits only if all of these hold:
   - `integrity_check` is ok and `foreign_key_check` is empty;
   - row counts are unchanged and every existing correction row is unchanged;
   - the schema equals a fresh `create_all` of the models.

   Otherwise it rolls back.
4. Is idempotent: on an already migrated database it only verifies.

**Before applying to any database:**
- stop the application;
- take and keep an independent copy of the file;
- record its SHA-256;
- run the dry run and read its report (row counts, statements, number of existing corrections).

**After applying:**
- a second dry run must report `statements: none`;
- the application must start, with the startup schema check passing.

## 3. Migration order for an older database

The application never alters the schema itself. A database must reach the current schema in this order:

| Database created before | Required step |
|---|---|
| Stage 9B-1 | `assets.issuer_id` is required and has no automatic migration. For an empty development database: back it up, delete it, and let the application recreate it. Otherwise assign every asset to its issuer (see `ACCESS_CONTROL.md` §5) |
| Stage 9B-2 | Two unique indexes on `document_versions` (`(document_id, version_number)`, `stored_path`). They were added in place on the empty development database (`PRODUCT_READINESS.md` §10); there is no script |
| Stage 9B-3 | `scripts/migrate_9b3_approval_integrity.py --apply` ([MIGRATION_9B3_PLAN.md](MIGRATION_9B3_PLAN.md)) |
| Stage 9B-4 step 7 | `scripts/migrate_9b4_correction_evidence.py --apply` (this document) |

Since step 7, the 9B-3 script compares only the tables it migrates with the models. It therefore still verifies correctly when the models already contain the step 7 columns.

## 4. Running an outdated schema

At startup, `app/db/schema_check.py` compares every existing table with the models. If a column or a named index is missing, the application refuses to start. It raises `SchemaOutOfDateError` listing what is missing (for example `review_corrections(original_source_page, …)`) and changes nothing. This is deliberate: without the step 7 columns, page history would be silently lost.

Limitation: the check verifies that columns and named indexes exist, not their types, defaults or constraints. The migration scripts verify those.

## 5. Rollback and recovery

| Situation | Action |
|---|---|
| Before applying | Nothing to do |
| After applying, no correction recorded since | `--rollback`: verified backup first, then `DROP COLUMN` of the three columns in one transaction, verified against the pre-migration schema. Needs SQLite 3.35 or later |
| After applying, corrections with page history exist | `--rollback` refuses, because the page history would be lost. It proceeds only with `--rollback --discard-page-history`, after its own backup |
| Any failure | Stop the application and replace the database file with the backup written by `--apply` (or the independent copy). Corrections recorded after the backup are lost |

After a rollback, the Stage 9B-4 step 7 code refuses to start on the rolled-back database (§4). Only code from before step 7 works with it.

## 6. Development and production responsibilities

- **Development database:** the local `rwa_legal_passport.db`. It is migrated only by the operator, only after an explicit approval, and with the backups above. Tests never touch it: they use in-memory or scratch databases.
- **Any other database** (a demo, staging or production copy) is the responsibility of its operator. Back it up independently, run the dry run, apply in the order of §3, verify, and keep the backup until the release is accepted. The scripts are SQLite-only. Another database engine needs a managed migration tool (for example Alembic), which this project does not provide yet (9A finding I9).
- **No secrets are involved.** The scripts read only the database file and print row counts and statements, never data values or credentials.
