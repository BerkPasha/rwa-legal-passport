# Contributing to RWA Legal Passport

## English development standard

**Status:** permanent project policy, effective 2026-10-15 (established at the end of the English migration; see `ENGLISH_MIGRATION_FINAL.md`).

All project text is written in clear, professional English. This applies to every change, in every file under version control.

### What must be in English

| Area | Includes |
|---|---|
| Code | Comments, docstrings, identifiers, `# noqa` / `# type:` explanations |
| Documentation | README, guides, Markdown files, `.env.example` comments, OpenAPI `summary` / `description` / tag text, `Field(description=…)` |
| API messages | `HTTPException.detail`, middleware error bodies, validation and exception messages, informational responses such as `GET /` |
| Logs | Log prose. Event keys (`access_config.missing`, `storage.failure`, …) are stable identifiers: never rename or translate them |
| CLI | `app.access_tool` labels, help text, errors and prompts |
| Tests | Test names, docstrings, assertion and failure messages, skip reasons, mock messages, and test data unless the test needs multilingual input |
| Frontend | All UI text, labels, placeholders, error and empty states, `aria-*` text |

Commit messages and pull-request descriptions should also be in English.

### Legitimate exceptions

The following content is **not** translated. It must be preserved exactly:

1. **Multilingual test fixtures.** Non-ASCII and non-English input that a test needs on purpose, for example Unicode filenames, titles, reviewer notes and corrections that must round-trip unchanged.
2. **Original legal evidence.** Quotations from source documents, supporting passages, extracted text and anything that reproduces a third-party document. Evidence is never translated.
3. **User data and credentials.** Database contents, uploaded documents, `.env`, `access_control.json`, `secrets/`.
4. **Historical records.** `PRODUCT_READINESS.md`, `ENGLISH_MIGRATION_PLAN.md`, `ENGLISH_MIGRATION_VERIFICATION.md` and `ENGLISH_MIGRATION_FINAL.md` quote the original text and are kept as written.
5. **Stored AI disclaimers.** Every extraction keeps the disclaimer it was created with. Since Stage 9B-4 step 10, `UNVERIFIED_DISCLAIMER` and the prompt page labels in `app/services/ai.py` are English (decisions D1 and D3). Records created earlier may carry the earlier Turkish disclaimer; they are historical data and are never rewritten.

The user interface and the API are English-only. There is no language selector or translation layer; optional localization would be a separate, approved phase.

### Automated check

`tests/test_language_policy.py` runs with the backend test suite (`python -m pytest`). It fails when Turkish-specific letters appear in application code, tests, frontend source or project documentation outside a reviewed exception. It also fails when a reviewed exception line is changed or removed, which protects the multilingual fixtures from accidental edits, and when an exception is added outside `tests/`: application and interface text has none.

To add a legitimate exception:

1. Confirm it belongs to one of the categories above.
2. Add the exact stripped line to `ALLOWED_LINES` as an escaped string (for example `ascii(line.strip())`), so the test file stays ASCII.
3. Make sure its file has a reason in `EXCEPTION_REASONS`.
4. Get the exception reviewed together with the change.

**Limitation:** the check detects Turkish-specific letters only. Turkish written in plain ASCII and other languages are not detected, so reviewers must still check the language of new text.

### Writing API and error messages

- **Status codes and response structure are the contract.** Clients must branch on the HTTP status code, never on `detail` text. Message wording may be improved in a later change without notice.
- Keep messages short, specific and actionable: "Document not found.", "File exceeds the 10 MB limit."
- Never include secrets, tokens, internal paths, stack traces, SQL or operating-system details.
- A resource outside the caller's scope returns exactly the same response as a missing resource.
- When a test asserts on message text, update the test in the same change. Never weaken the assertion.
- Log messages start with a stable event key followed by `key=value` fields and optional English prose.
