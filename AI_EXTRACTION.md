# AI extraction (Stage 10A-2)

AI extraction proposes **draft** legal-rights claims from the extracted text of one document version. It never approves, attests or establishes legal validity. Every known claim is checked against its cited page (deterministic evidence check, [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md)), and only a human approval can make it part of a passport.

**Status:** the Anthropic integration (`app/services/ai.py`) is hardened and tested against an in-process fake of the Messages API, using the real SDK. It has **not been run against a real model**. Its accuracy is unmeasured.

---

## 1. Configuration (server-side only)

| Variable | Default | Allowed | Meaning |
|---|---|---|---|
| `AI_PROVIDER` | `disabled` | `disabled`, `anthropic` | `disabled`: no document text is ever sent to any AI service (extraction answers 503 `ai_provider_disabled`) |
| `ANTHROPIC_API_KEY` | empty | an Anthropic key (`sk-ant-…`) | Read from `.env` as a secret (`SecretStr`): never logged, never returned. Empty or malformed (e.g. the old placeholder): 503 `ai_provider_not_configured`, nothing sent |
| `AI_MODEL` | `claude-opus-5-5` | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-sonnet-4-6`, `claude-haiku-5-5`, `claude-haiku-4-5`, `claude-haiku-4-5-20251001` | Checked at startup only when `AI_PROVIDER=anthropic`; another ID refuses to start |
| `AI_MAX_OUTPUT_TOKENS` | 8192 | 1024 – 16000 | `max_tokens` of every request |
| `AI_MAX_INPUT_CHARS` | 200000 | 10000 – 1000000 | Characters of the complete model input (§4) |
| `AI_MAX_PAGES` | 200 | 1 – 10000 | Pages of the document version |
| `AI_REQUEST_TIMEOUT_SECONDS` | 300 | 10 – 600 | Timeout of the single attempt |

- An out-of-range value refuses startup.
- The SDK's automatic retries are disabled (`max_retries=0`): one request at most per extraction, and a failure is retried only by the user, within the rate limit.
- The frontend never receives the key. It receives only:
  - the processor and model named in a confirmation request (§3);
  - from `GET /ai/status` (`extraction:read`, Stage 10B-2): whether extraction is available, its status code, the external processor and model, the supported model IDs, and a message.

  The interface uses this to state that AI is disabled, to disable the action, and to label each extraction as AI (a supported model ID) or not AI (test or imported data).
- Manual legal claims (Stage 10B-3, [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md) §13) never involve an AI service. They are labelled `source: manual` and work with `AI_PROVIDER=disabled`.
- Startup logs the provider state, never the key.

## 2. When document text leaves the server

`POST /documents/{document_id}/extractions` (`extraction:run`). With an external provider, nothing is sent unless every gate passes, in this order:

| # | Gate | Refusal | Body |
|---|---|---|---|
| 1 | Document exists and is in the caller's issuer scope | 404 | `detail` (identical to a missing document) |
| 2 | Provider enabled and configured | 503 | `ai_error`: `ai_provider_disabled` / `ai_provider_not_configured` |
| 3 | The document's issuer authorized external AI processing (`access_control.json`, issuer `"external_ai_processing": true`; default `false`) | 403 | `extraction_refusal`: `external_processing_not_authorized` |
| 4 | The version has extractable text (Stage 10A-1) | 422 | `no_extractable_text` |
| 5 | Within `AI_MAX_PAGES` and `AI_MAX_INPUT_CHARS` | 422 | `too_many_pages` / `input_too_large`, with `external_processing` |
| 6 | The caller confirmed this request (§3) | 428 | `external_processing_confirmation_required`, with `external_processing` |
| 7 | The confirmed `version_id` (if given) is the current version | 409 | `confirmation_version_mismatch`, with `external_processing` |
| 8 | Per-user rate limit (`EXTRACTION_RATE_LIMIT_PER_HOUR`) | 429 | `detail` |

- Gates 1–7 make no AI request and store nothing.
- The rate limit counts only requests that pass every gate, so a confirmation round trip costs nothing.
- Permissions are unchanged: the independent legal reviewer has no `extraction:run`.
- The mock services used by the tests are not external processors and have no gates.

## 3. Confirmation protocol

A request without confirmation is answered with 428. The answer states exactly what would be sent, and contains no document text:

```json
{
  "detail": "Confirmation required: this request sends the extracted text of version 1 of this document (4 pages, 3562 characters including the instructions) to Anthropic, an external AI service (model claude-opus-5-5). Nothing has been sent. …",
  "extraction_refusal": "external_processing_confirmation_required",
  "external_processing": {
    "processor": "Anthropic", "model": "claude-opus-5-5",
    "document_id": "…", "version_id": "…", "version_number": 1,
    "page_count": 4, "pages_without_text": [], "input_chars": 3562,
    "max_input_chars": 200000, "max_pages": 200, "max_output_tokens": 8192
  }
}
```

The client repeats the request with `{"confirm_external_processing": true, "version_id": "<that version_id>"}`.
- **Strict body:** `true` must be a JSON boolean, and unknown fields are rejected (422).
- **Version binding:** if a newer version was uploaded in between, the confirmation does not cover it (409).

The interface shows the summary in a confirmation dialog (processor, model, version, pages, characters, pages without text, "unverified AI draft, never approved automatically"), and sends nothing more if the user declines.

The confirmation is logged (`extraction.external_processing_confirmed`, with user, version, processor, model and size). It is **not** stored in the database: a durable consent record needs a schema change (§9).

## 4. Input-size safeguard

`input_chars` = the characters of the complete model input: the instructions, the page markers and the page texts exactly as sent.
- **Measured before sending:** it is computed with the same code that builds the request (`prepare_request`), so the bound is exact (tested at the limit and one character below).
- **Why characters, not tokens:** counting tokens would need the provider's token-counting endpoint, which would send the document before any authorization. Characters are deterministic and need no network.
- **Planning assumption, not a guarantee:**
  - typical English legal text is about 3–4 characters per token (newer tokenizers count up to about 30% more tokens);
  - for cost limits we assume at most one token per character, so the default 200,000 characters stays below about 200,000 input tokens;
  - non-Latin scripts and unusual symbols can tokenize less favourably;
  - the API reports the real usage.
- **Cost ceiling per request** (prices per million input/output tokens, from the model catalog; to be confirmed before a live test):

| Model | Input ($/MTok) | Output ($/MTok) | Ceiling at the defaults (200,000 input tokens assumed + 8,192 output) |
|---|---|---|---|
| `claude-opus-5-5` | 4 | 20 | ≈ $0.80 + $0.16 = $0.96 |
| `claude-sonnet-5-5` | 2 | 10 | ≈ $0.40 + $0.08 = $0.48 |
| `claude-haiku-5-5` | 0.10 (0.50 above 100K prompt tokens) | 0.50 (2.50 above 100K) | ≈ $0.10 + $0.02 = $0.12 |

The upload limits of Stage 9B-2 (`MAX_PDF_PAGES`, `MAX_PDF_TEXT_CHARS`, file size) still apply first.

## 5. Output handling

- **Stop reasons:** only a completed response (`end_turn`) with text is used. The response is rejected (503, nothing stored) for:
  - `refusal` (`ai_error: refusal`, with the category if the API names one);
  - `max_tokens` or `model_context_window_exceeded` (`truncated`);
  - a response without text (thinking blocks are never read);
  - any other stop reason.
- **Parsing:** strict JSON and schema validation; duplicate fields reject the response (Stage 9B-4).
- **Error messages** name the location and type of a schema error, never its value. Values can quote the document.
- **Grounding:** a known claim must have a quotation and cite a page that exists in this version and has a text layer. Otherwise the whole response is rejected (`ungrounded_output`). A claim on a page outside the document is an invented reference, and a page without a text layer was shown to the model as `(empty page)`.
- **Quotation check:** whether a quotation really appears on its cited page is decided claim by claim by the evidence check. A mismatch (`not_found`, `found_on_other_page`, `quotation_too_short`) is stored as an unverified draft claim, visible to reviewers, and blocks approval until it is corrected with verified evidence. Extraction artifacts (spacing in tables, hyphenation) can cause such mismatches without any fabrication.
- **Missing fields** are stored as canonical `unknown` claims (Stage 9B-4 decision): no value, page or quotation is invented.
- **Status:** the result is always `draft`; nothing is approved automatically.

## 6. Errors (`ai_error`, HTTP 503, nothing stored)

| Code | Cause |
|---|---|
| `ai_provider_disabled`, `ai_provider_not_configured` | §1 |
| `authentication_failed`, `permission_denied` | 401 / 403 from the provider |
| `rate_limited` | 429 from the provider (no retry) |
| `timeout`, `connection_failed` | No response within `AI_REQUEST_TIMEOUT_SECONDS`, or no connection |
| `provider_error` | Any other provider HTTP error (e.g. 400, 500, 529), with the status code only |
| `client_error` | The SDK could not build the request; nothing was sent |
| `refusal`, `truncated`, `invalid_output`, `ungrounded_output`, `input_too_large`, `too_many_pages` | §4, §5 |

No message contains the API key, the document text or the model output.

## 7. Confidentiality and untrusted content

- **The document is data, not instructions:** the instructions say so, and text inside a page can never change them. A model that follows instructions planted in a document still cannot produce an approvable claim: its quotations must be found on their cited pages, and a human must approve.
- **Nothing is logged that contains document text:**
  - The SDK logs the complete request (the document text) at DEBUG level. Those records are dropped whatever the configured level.
  - The database driver's DEBUG statement log is silenced, and SQL errors never contain statement parameters (`hide_parameters`).
  - Application logs name documents, versions and users by identifier only.
- **Data processing by Anthropic:** enabling the provider sends document text to Anthropic under the organization's agreement with Anthropic (retention, zero-data-retention options). That is an organizational decision; the per-issuer `external_ai_processing` flag records it for each tenant.

## 8. Tests

- `tests/test_ai_provider_hardening.py` runs the real service and the real SDK against an in-process transport, with no network. It covers:
  - configuration and secret handling;
  - every authorization gate;
  - successful structured extraction;
  - refusal, truncation, malformed and invalid output;
  - HTTP errors, timeout and connection failure, with exactly one attempt each;
  - grounding;
  - input and output bounds and the rate limit;
  - prompt injection;
  - logging without text;
  - mock compatibility.
- Also: `tests/test_ai_output_integrity.py`, `tests/test_real_pdf_ingestion.py` and `frontend/src/test/ExternalProcessing.test.tsx`.

## 9. Not done, and limitations

- **No live call has been made:** model accuracy, refusal behaviour on legal documents and real token usage are unmeasured.
- **Consent is logged, not stored:** a durable record (who confirmed which version for which provider, and when) needs new columns on `legal_extractions`, i.e. a migration that requires approval.
- Requests are non-streaming, which is why output is capped at 16,000 tokens.
- The rate limit is per process and in memory.
- The input bound counts characters, not tokens (§4).

## 10. The first live test

Prepared as a harness, `scripts/ai_live_check.py` (Stage 10A-3). It never runs without explicit flags, and it is tested only offline so far. The operator guide, with the exact commands, the maximum cost, the evaluation and the clean-up, is **[AI_LIVE_TEST.md](AI_LIVE_TEST.md)**.

It drives this route and all its gates in a temporary environment:
- with only the verified synthetic fixture;
- with one guarded request at most;
- with a budget checked before confirming;
- never approving anything.
