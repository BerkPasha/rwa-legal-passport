# First live AI extraction test: operator guide (Stage 10A-3)

This guide covers **one** real request to Anthropic, made only with the synthetic fixture `tests/fixtures/real_pdfs/trust_prospectus.pdf`, by `scripts/ai_live_check.py`. Nothing in the development system changes:
- `.env`, `rwa_legal_passport.db`, `storage/` and `access_control.json` are never read or written;
- `AI_PROVIDER` stays `disabled` for the server.

**Status:** the harness has been tested offline only. No live request has been made, and real AI extraction is **not verified**. One successful run on one synthetic document would be a smoke test, not a verification.

---

## 1. What the harness does

1. **Refusals before anything starts (exit 2, nothing sent):**
   - no mode is chosen;
   - a live prerequisite is missing;
   - the fixture differs from its pinned SHA-256 (in the script and in `SHA256SUMS`);
   - the model has no verified price.
2. **A temporary environment** in the system temp directory, holding a new SQLite database, a storage directory and an access registry:
   - one fictional issuer with `"external_ai_processing": true`, and one without;
   - a fictional operator and reviewer with random tokens that are never printed;
   - Stellar anchoring unconfigured.

   The working directory becomes that directory, so `.env` is not loaded. The harness checks the loaded configuration, and refuses unless the database is inside the temporary directory.
3. **The real application** (`app.main`) is driven in-process through its HTTP API:
   1. register a fictional asset;
   2. upload the fixture (the stored SHA-256 and the text layer are checked);
   3. `POST /documents/{id}/extractions` without confirmation, which must answer **428**. The summary must describe the verified fixture: Anthropic, the chosen model, 4 pages, none without text, the confirmed version.
4. **Budget check** on the server's own `input_chars`: the worst case, assuming one token per character plus the full output allowance (thinking included), must stay within `--max-cost-usd`, `--max-input-tokens` and `--max-output-tokens`.
5. **One confirmed request** (`confirm_external_processing` + `version_id`). It goes through the guard transport:
   - one `POST https://api.anthropic.com/v1/messages` is allowed;
   - anything else, or a second request, is blocked;
   - the SDK retries are disabled.
6. **Read-back only:** the harness reads the stored extraction and reports on it. It **never approves**; the result stays a draft that needs human review.
7. **Clean-up:** the database is closed and the temporary directory deleted. The harness then checks that the development database is byte-identical.

## 2. Anthropic account (once)

1. An Anthropic Console account (`platform.claude.com`) with billing set up (prepaid credits are enough).
2. A dedicated **workspace** for this test, with a low **monthly spend limit** (for example $5). The limit is a second safeguard at the provider, independent of the harness.
3. An **API key** created in that workspace.
   - Never write it into `.env`, a file, a command line or the repository.
   - Delete it after the test if it is not needed again.
4. **Data:** only the synthetic, non-confidential fixture is sent. Before any real document is processed later, the organization must decide on Anthropic's data retention terms (zero data retention if required). That decision is not part of this test.

## 3. Set the key for one terminal only (it is never echoed)

PowerShell (Windows PowerShell 5.1):

```powershell
$secure = Read-Host "Anthropic API key" -AsSecureString
$env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password
Remove-Variable secure
```

Git Bash:

```bash
read -rs -p "Anthropic API key: " ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY; echo
```

The harness reads the key only from this process environment and only in `--live` mode. The offline modes replace it with a fictional value.

## 4. Verify that the selected model exists (no document is sent)

The following request is free. It sends only the key, to the Models API:

```powershell
Invoke-RestMethod -Uri https://api.anthropic.com/v1/models/claude-opus-5-5 `
  -Headers @{ "x-api-key" = $env:ANTHROPIC_API_KEY; "anthropic-version" = "2023-06-01" } |
  Select-Object id, display_name
```

The expected answer is `id = claude-opus-5-5`.
- **404:** the model is not available to this key; choose another supported model.
- **401:** the key is wrong.

Alternatively, check the model list in the Console.

Supported by the harness (verified prices, platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-18):
- `claude-opus-5-5` (default);
- `claude-sonnet-5-5`;
- `claude-sonnet-4-6` (legacy, active; retirement not sooner than 2027-02-17; $3 / $15 per MTok, also on its model page);
- `claude-haiku-5-5`;
- `claude-haiku-4-5`.

Confirm the prices on that page on the day of the test.

## 5. Rehearse offline (no key needed, nothing sent)

```powershell
.venv\Scripts\python.exe scripts\ai_live_check.py --dry-run
.venv\Scripts\python.exe scripts\ai_live_check.py --fake-provider all
```

- **Dry run:** must end with exit code 0 and show the following lines (the cost line is for the default model):
  - `requests sent 0`
  - `HTTP 428`
  - `stopped dry run`
  - `worst case $0.1781 vs limit $0.2500 -> within budget`
  - `development database unchanged: True`
  - `temporary environment destroyed: True`
- **Fake provider:** must show `[as expected]` for every scenario:
  - valid, wrong page, fabricated, invented page, refusal, malformed JSON, truncated, timeout, invalid credentials;
  - cost limit, missing consent, stale confirmation, summary mismatch, missing issuer authorization.

## 6. The one live run

Run it only after explicit approval of the cost and of sending the synthetic document to Anthropic.

**Authorized on 2026-10-18 (Stage 10A-4):** exactly one request, model `claude-sonnet-4-6`, estimated budget USD 0.25, executed by the project owner.

Open a **new** Windows PowerShell window outside Claude Code, without profile scripts (Win+R, then `powershell -NoProfile`). Paste the whole block below; it runs as one statement, so any failure stops everything after it:

```powershell
& {
    Set-Location C:\Users\berkf\rwa-legal-passport -ErrorAction Stop
    $Python = '.\.venv\Scripts\python.exe'
    $Model = 'claude-sonnet-4-6'
    $Report = 'scratch\ai-live-check\live-report-claude-sonnet-4-6.json'
    if (-not (Test-Path $Python)) { throw "The project's Python was not found. Nothing was sent; stop here." }

    # 1. Offline rehearsal with the same model and budget: no key is needed, nothing is sent
    & $Python scripts\ai_live_check.py --dry-run --model $Model --max-cost-usd 0.25
    if ($LASTEXITCODE -ne 0) { throw "Dry run failed (exit $LASTEXITCODE). Nothing was sent; stop here." }

    try {
        # 2. The key, typed without echo, set for this PowerShell process only
        $secure = Read-Host 'Anthropic API key (input hidden)' -AsSecureString
        $env:ANTHROPIC_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
        $secure.Dispose()

        # 3. Model availability for this key: a free Models API request, no document content
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $info = Invoke-RestMethod -Method Get -Uri "https://api.anthropic.com/v1/models/$Model" -ErrorAction Stop `
            -Headers @{ 'x-api-key' = $env:ANTHROPIC_API_KEY; 'anthropic-version' = '2023-06-01' }
        if ($info.id -cne $Model) { throw "The Models API did not confirm $Model. Nothing was sent; stop here." }
        "Model available to this key: $($info.id) ($($info.display_name))"

        # 4. The one authorized live request: synthetic fixture only, estimated budget USD 0.25
        & $Python scripts\ai_live_check.py --live --authorize-paid-request --authorize-external-transfer `
            --model $Model --max-cost-usd 0.25 --report-file $Report
        "Live check exit code: $LASTEXITCODE (0 = the one request was sent and reported; 2 = refused, nothing sent)"
    }
    finally {
        Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
        Remove-Variable secure, info -ErrorAction SilentlyContinue
        "ANTHROPIC_API_KEY still set in this process: $(Test-Path Env:ANTHROPIC_API_KEY)"
    }
}
```

What the block guarantees:
- The dry run comes first, without a key; if it fails, the key is never requested.
- The key is read with `Read-Host -AsSecureString` (no echo, not saved in the command history). It is set as a process variable of this window only, never in `.env` or the user environment, and removed in `finally` even after an error or Ctrl+C. Close the window afterwards.
- Before any document is sent, the model is confirmed available for this key (a free Models API request carrying only the key). A 401 or 404, or a different ID, stops the block.
- The harness sends at most one request, and only after its own checks: fixture SHA-256, the 428 summary, the budget.

Notes:
- `--include-claim-values` is deliberately omitted, so the report file contains no claim values or quotations. Add it to the live command only if a person is to judge the values (§8); it writes them to the report file only, and the fixture is synthetic.
- Run it **once**. A second run is a second paid request and needs its own approval.

## 7. Maximum cost

The worst case is computed by the harness before the request. It assumes the input planning bound of one token per character (3,562 characters of model input, the instructions included) and the full output allowance of 8,192 tokens:

| Model | Worst case (refused if above `--max-cost-usd`) | Typical expectation |
|---|---|---|
| `claude-opus-5-5` ($4 / $20 per MTok) | **$0.1781** | ≈ $0.02–0.07 (about 1,000–1,300 input tokens; 1,000–3,000 output tokens including thinking) |
| `claude-sonnet-5-5` ($2 / $10) | $0.0890 | ≈ $0.01–0.03 |
| **`claude-sonnet-4-6`** ($3 / $15) — authorized | **$0.1336** | ≈ $0.01–0.03 (about 1,000–1,200 input tokens with the previous tokenizer; 600–1,500 output tokens; more if the model thinks, never above the ceiling) |
| `claude-haiku-4-5` ($1 / $5) | $0.0445 | < $0.02 |
| `claude-haiku-5-5` ($0.10 / $0.50) | $0.0045 | < $0.002 |

- `max_tokens` caps the billed output, thinking included. A refused, truncated or rejected response can still be billed, but never above this ceiling.
- The report states the provider-reported token usage and the resulting actual cost.
- **The local guard is an estimate, not a billing guarantee:**
  - it uses the documented prices of the day and an assumed input bound;
  - it cannot recall a request once it is sent;
  - Anthropic bills the actual tokens at its current prices.

  The workspace spend limit (§2) is the only hard cap at the provider.

## 8. Evaluating the result

The harness prints a summary. `--report-file` adds the full JSON report. It contains:
- the model requested and the model reported;
- the input and output tokens and the actual cost;
- the number of known claims, of page-grounded quotations, of unverified or ungrounded claims, and of claims on the expected page;
- the validation errors, the duration, the audit events, and `human_review_required: true`.

Evaluate in this order:
1. **Technical:**
   - exit code 0;
   - `requests sent 1`, `blocked []`;
   - HTTP 201;
   - actual cost ≤ budget.

   On a 503, the `ai_error` code (refusal, truncated, invalid_output, ungrounded_output, timeout, authentication_failed…) is itself the finding.
2. **Grounding:**
   - the fixture states all seven fields, on pages 1, 2, 2, 3, 3, 4, 4;
   - record known claims (expected 7), page-grounded quotations, and claims on the expected page;
   - every unverified claim with its status (`not_found`, `found_on_other_page`, `quotation_too_short`).
3. **Values (a person, from `claim_text` in the report file):** for each field, is the value a correct and complete reading of the cited passage? Record correct, partially correct or wrong. A verified quotation does not make a value correct.
4. **Conclusion:** one synthetic document. Record the observations; do **not** mark real AI extraction as verified. A broader evaluation (more documents, repeated runs, other models) is a separate, approved stage.

## 9. Back to the safe state

1. Remove the key from the terminal:
   - PowerShell: `Remove-Item Env:ANTHROPIC_API_KEY`
   - Git Bash: `unset ANTHROPIC_API_KEY`

   Close the terminal.
2. `AI_PROVIDER` was set only inside the harness process; the server's `.env` was not touched. To confirm, `Select-String -Path .env -Pattern '^AI_PROVIDER'` must print nothing, or `AI_PROVIDER=disabled`. Server startup logs `ai_provider.disabled`.
3. Delete the test API key in the Console, or keep it only in the dedicated workspace with its spend limit.

## 10. Temporary data

- The harness deletes its temporary directory (database, storage, registry, uploaded copy) at the end, and reports `temporary environment destroyed: True`.
- If it reports `False` (for example a Windows file lock), it prints the path. Delete it by hand: `Remove-Item -Recurse -Force <path>`.
- Nothing is written to the development database, `storage/` or `access_control.json`, which the report confirms (`development database unchanged: True`).
- The optional report file (`scratch/ai-live-check/`) is the only remaining artifact. Delete it when the evaluation is recorded.

## 11. Exit codes

| Code | Meaning |
|---|---|
| 0 | Completed (offline: every scenario as expected; live: the one request was sent) |
| 1 | An unexpected outcome or error (the message names the type; the key is redacted) |
| 2 | Refused: a prerequisite is missing, the budget would be exceeded, or the summary did not match; nothing was sent |

Tests: `tests/test_ai_live_check.py`. It runs fully offline, and the live code path is exercised with a fake transport while the real network transport is made impossible to construct.
