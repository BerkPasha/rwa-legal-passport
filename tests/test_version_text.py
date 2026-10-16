"""
Stage 9B-4 step 8 — version-specific source text: GET /documents/{id}/versions/{n}/text.

The endpoint serves exactly the page texts recorded for the requested version at upload. The
version is required (no fallback to the current one), a newer upload never changes what an older
version returns, an optional version_id must name the same version, missing or inconsistent page
records are never served, and nothing is written. The text is evidence material, not proof of
legal validity.

Coverage:
 1. Correct version and pages
 2. A newer upload never replaces an older version's text
 3. Stale (non-current) versions
 4. Version reference mismatch
 5. Missing versions and documents
 6. Invalid inputs
 7. Authentication and roles
 8. Cross-tenant isolation without revealing existence
 9. Unavailable or inconsistent source text
10. Read-only: nothing is written
11. Consistency with the approval evidence binding
12. The current-version endpoint names its version
13. Concurrent uploads and reads
14. No blockchain dependency, no internal paths, OpenAPI
"""

import asyncio
import hashlib
import json
import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.services.stellar import get_stellar_service
from tests.conftest import (
    _HARBOR_ASSET,
    _MERIDIAN_ASSET,
    ADMIN_TOKEN,
    AI_TOKEN,
    DISABLED_TOKEN,
    ISSUER_TOKEN,
    LEGAL_TOKEN,
    OTHER_ISSUER_TOKEN,
    OTHER_LEGAL_TOKEN,
    bearer,
)
from tests.mock_ai import MOCK_SOURCE_PAGES
from tests.pdf_fixtures import make_source_pdf, make_text_pdf
from tests.test_approval_evidence_gate import _approve, _extract, _snapshot, _upload, file_db  # noqa: F401

V1_PAGES = [["Version one, page one: the Trust holds the Property."], ["Version one, page two: quarterly payments."]]
V2_PAGES = [["Version two, page one: amended terms."], ["Version two, page two: amended payments."],
            ["Version two, page three: new schedule."]]
RESPONSE_KEYS = {"document_id", "document_status", "version_id", "version_number", "version_created_at",
                 "sha256_hash", "is_current_version", "current_version_number", "page_count",
                 "source_text_sha256", "text_origin", "notice", "pages"}
NOT_FOUND = {"detail": "Document not found."}
VERSION_NOT_FOUND = {"detail": "Document version not found."}


def _url(document_id: str, version_number, version_id: str | None = None) -> str:
    query = f"?version_id={version_id}" if version_id is not None else ""
    return f"/documents/{document_id}/versions/{version_number}/text{query}"


def _source_text_sha256(pages: list[tuple[int, str]]) -> str:
    """Independent re-computation of the canonical page-text digest."""
    return hashlib.sha256(json.dumps([[n, t] for n, t in sorted(pages)], ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


async def _db_pages(db: AsyncSession, version_id: str) -> list[tuple[int, str]]:
    return [tuple(r) for r in (await db.execute(
        text("SELECT page_number, text_content FROM document_pages WHERE version_id = :v ORDER BY page_number"),
        {"v": version_id})).all()]


async def _new_version(client: AsyncClient, document_id: str, pdf: bytes, headers: dict) -> dict:
    r = await client.post(f"/documents/{document_id}/versions", headers=headers,
                          files={"file": ("v.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def two_versions(client: AsyncClient, created_asset: dict, issuer_headers: dict) -> dict:
    v1_pdf, v2_pdf = make_text_pdf(V1_PAGES), make_text_pdf(V2_PAGES)
    doc = await _upload(client, created_asset["id"], v1_pdf, issuer_headers)
    v2 = await _new_version(client, doc["id"], v2_pdf, issuer_headers)
    return {"doc": doc, "v1": doc["current_version"], "v2": v2, "v1_pdf": v1_pdf, "v2_pdf": v2_pdf}


# ── 1, 2, 3. Correct version, history immutability, stale versions ──────────


async def test_each_version_returns_exactly_its_own_pages(
    client: AsyncClient, two_versions: dict, issuer_headers: dict, async_db: AsyncSession
):
    doc, v1, v2 = two_versions["doc"], two_versions["v1"], two_versions["v2"]
    for version, n, expected_pages, pdf in ((v1, 1, V1_PAGES, two_versions["v1_pdf"]), (v2, 2, V2_PAGES, two_versions["v2_pdf"])):
        r = await client.get(_url(doc["id"], n), headers=issuer_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == RESPONSE_KEYS
        assert (body["document_id"], body["version_id"], body["version_number"]) == (doc["id"], version["id"], n)
        assert body["sha256_hash"] == hashlib.sha256(pdf).hexdigest() == version["sha256_hash"]
        assert body["page_count"] == len(expected_pages)
        assert [p["page_number"] for p in body["pages"]] == list(range(1, len(expected_pages) + 1))
        assert [p["text_content"] for p in body["pages"]] == [lines[0] for lines in expected_pages]
        stored = await _db_pages(async_db, version["id"])
        assert [(p["page_number"], p["text_content"]) for p in body["pages"]] == stored
        assert body["source_text_sha256"] == _source_text_sha256(stored)
        assert body["text_origin"] == "pdf_text_layer_extracted_at_upload"
        assert "not establish legal validity" in body["notice"] and "no OCR" in body["notice"]
        assert body["current_version_number"] == 2 and body["is_current_version"] is (n == 2)


async def test_a_newer_upload_never_replaces_an_older_versions_text(
    client: AsyncClient, two_versions: dict, issuer_headers: dict
):
    doc = two_versions["doc"]
    before = (await client.get(_url(doc["id"], 1), headers=issuer_headers)).json()
    await _new_version(client, doc["id"], make_text_pdf([["Version three replaces nothing."]]), issuer_headers)
    after = (await client.get(_url(doc["id"], 1), headers=issuer_headers)).json()
    changing = {"current_version_number"}
    assert {k: v for k, v in after.items() if k not in changing} == {k: v for k, v in before.items() if k not in changing}
    assert (before["current_version_number"], after["current_version_number"]) == (2, 3)
    v3 = (await client.get(_url(doc["id"], 3), headers=issuer_headers)).json()
    assert v3["pages"] == [{"page_number": 1, "text_content": "Version three replaces nothing."}]
    assert v3["version_id"] not in (two_versions["v1"]["id"], two_versions["v2"]["id"])


async def test_stale_versions_are_served_as_themselves_and_marked_not_current(
    client: AsyncClient, two_versions: dict, issuer_headers: dict
):
    body = (await client.get(_url(two_versions["doc"]["id"], 1), headers=issuer_headers)).json()
    assert (body["is_current_version"], body["current_version_number"], body["version_id"]) == (
        False, 2, two_versions["v1"]["id"])
    assert "Version one" in body["pages"][0]["text_content"]


# ── 4. Version reference mismatch ────────────────────────────────────────────


async def test_matching_version_id_is_accepted(client: AsyncClient, two_versions: dict, issuer_headers: dict):
    r = await client.get(_url(two_versions["doc"]["id"], 1, two_versions["v1"]["id"]), headers=issuer_headers)
    assert r.status_code == 200 and r.json()["version_id"] == two_versions["v1"]["id"]


async def test_mismatched_version_references_are_refused_without_serving_text(
    client: AsyncClient, admin_client: AsyncClient, two_versions: dict, created_asset: dict, source_pdf: bytes,
    issuer_headers: dict, other_issuer_headers: dict,
):
    doc = two_versions["doc"]
    other_doc = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Other")
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    harbor_doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Harbor")
    expected = {"detail": "Version reference mismatch: version_id does not identify version 1 of this document; "
                          "no text is served for a different version."}
    for version_id in (two_versions["v2"]["id"],                   # another version of the same document
                       other_doc["current_version"]["id"],        # a version of another document
                       harbor_doc["current_version"]["id"],       # another tenant's version: not revealed
                       "00000000-0000-0000-0000-000000000000", "not-a-version"):
        r = await client.get(_url(doc["id"], 1, version_id), headers=issuer_headers)
        assert r.status_code == 409 and r.json() == expected, (version_id, r.text)
        assert "Version one" not in r.text


# ── 5, 6. Missing versions, invalid inputs ───────────────────────────────────


async def test_missing_versions_and_documents(client: AsyncClient, two_versions: dict, issuer_headers: dict):
    doc_id = two_versions["doc"]["id"]
    r = await client.get(_url(doc_id, 3), headers=issuer_headers)
    assert r.status_code == 404 and r.json() == VERSION_NOT_FOUND
    r = await client.get(_url("00000000-0000-0000-0000-000000000000", 1), headers=issuer_headers)
    assert r.status_code == 404 and r.json() == NOT_FOUND


@pytest.mark.parametrize("version", ["0", "-1", "abc", "1.5", "1e3", "2147483648", "true", "%20"])
async def test_invalid_version_numbers_are_rejected(
    client: AsyncClient, two_versions: dict, issuer_headers: dict, version: str
):
    r = await client.get(f"/documents/{two_versions['doc']['id']}/versions/{version}/text", headers=issuer_headers)
    assert r.status_code == 422, (version, r.status_code, r.text)
    assert isinstance(r.json()["detail"], list)


@pytest.mark.parametrize("document_id", ["..%2F..%2Fetc", "%2E%2E", "x" * 300, "' OR '1'='1", "%00"])
async def test_hostile_document_ids_find_nothing(client: AsyncClient, two_versions: dict, issuer_headers: dict,
                                                 document_id: str):
    r = await client.get(f"/documents/{document_id}/versions/1/text", headers=issuer_headers)
    assert r.status_code == 404, (document_id, r.status_code, r.text)


async def test_empty_version_id_is_rejected(client: AsyncClient, two_versions: dict, issuer_headers: dict):
    r = await client.get(f"/documents/{two_versions['doc']['id']}/versions/1/text?version_id=", headers=issuer_headers)
    assert r.status_code == 422


# ── 7. Authentication and roles ──────────────────────────────────────────────


async def test_credentials_and_roles(client: AsyncClient, two_versions: dict, inst_headers: dict):
    url = _url(two_versions["doc"]["id"], 1)
    assert (await client.get(url)).status_code == 403
    assert (await client.get(url, headers=bearer("not-a-token"))).status_code == 401
    assert (await client.get(url, headers=bearer(DISABLED_TOKEN))).status_code == 401
    assert (await client.get(url, headers=inst_headers)).status_code == 403  # institutional keys: passport only
    for token in (ISSUER_TOKEN, LEGAL_TOKEN, ADMIN_TOKEN, AI_TOKEN):  # every role with document:read in scope
        r = await client.get(url, headers=bearer(token))
        assert r.status_code == 200 and r.json()["version_id"] == two_versions["v1"]["id"], token


# ── 8. Cross-tenant isolation ────────────────────────────────────────────────


async def test_other_tenants_learn_nothing_not_even_which_versions_exist(
    client: AsyncClient, admin_client: AsyncClient, two_versions: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, other_issuer_headers: dict,
):
    doc_id = two_versions["doc"]["id"]
    for token in (OTHER_ISSUER_TOKEN, OTHER_LEGAL_TOKEN):
        for version in (1, 2, 3, 999):  # existing and non-existing versions answer identically
            r = await client.get(_url(doc_id, version, two_versions["v1"]["id"]), headers=bearer(token))
            assert r.status_code == 404 and r.json() == NOT_FOUND
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    harbor_doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Harbor terms")
    for headers in (issuer_headers, legal_headers):  # Meridian reviewers cannot read Harbor text
        r = await client.get(_url(harbor_doc["id"], 1), headers=headers)
        assert r.status_code == 404 and r.json() == NOT_FOUND
    assert (await client.get(_url(harbor_doc["id"], 1), headers=other_issuer_headers)).status_code == 200


# ── 9. Unavailable or inconsistent source text ───────────────────────────────


@pytest.mark.parametrize("sql,reason", [
    ("DELETE FROM document_pages WHERE version_id = :v", "no_page_records"),
    ("UPDATE document_pages SET page_number = 1 WHERE version_id = :v", "invalid_page_records"),
    ("UPDATE document_pages SET page_number = 5 WHERE version_id = :v AND page_number = 2", "invalid_page_records"),
], ids=["no_pages", "duplicate_numbers", "gap"])
async def test_unavailable_or_inconsistent_text_is_never_served(
    client: AsyncClient, two_versions: dict, issuer_headers: dict, async_db: AsyncSession, caplog, sql: str, reason: str
):
    await async_db.execute(text(sql), {"v": two_versions["v1"]["id"]})
    await async_db.commit()
    with caplog.at_level(logging.ERROR, logger="rwa_security"):
        r = await client.get(_url(two_versions["doc"]["id"], 1), headers=issuer_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "The stored source text of this document version is unavailable or inconsistent; "
                                  "it is not served.", "source_text_unavailable_reason": reason}
    assert "Version one" not in r.text
    assert any(f"reason={reason}" in rec.getMessage() for rec in caplog.records)
    # The other version is unaffected; nothing is substituted
    v2 = (await client.get(_url(two_versions["doc"]["id"], 2), headers=issuer_headers)).json()
    assert "Version two" in v2["pages"][0]["text_content"]


async def test_pages_without_a_text_layer_are_served_empty_never_invented(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers)
    body = (await client.get(_url(doc["id"], 1), headers=issuer_headers)).json()
    assert body["pages"] == [{"page_number": 1, "text_content": ""}] and body["page_count"] == 1


# ── 10. Read-only ────────────────────────────────────────────────────────────


async def test_the_endpoint_writes_nothing(
    client: AsyncClient, two_versions: dict, issuer_headers: dict, async_db: AsyncSession
):
    before = await _snapshot(async_db)
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    engine = async_db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for url in (_url(two_versions["doc"]["id"], 1), _url(two_versions["doc"]["id"], 2),
                    _url(two_versions["doc"]["id"], 9), _url(two_versions["doc"]["id"], 1, "mismatch")):
            await client.get(url, headers=issuer_headers)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements and set(statements) <= {"SELECT"}, set(statements)
    assert await _snapshot(async_db) == before


# ── 11. Consistency with the approval evidence binding ───────────────────────


async def test_text_matches_what_an_approval_of_that_version_bound(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict
):
    doc_id = created_document["id"]
    approval = (await _approve(client, created_extraction["id"], legal_headers)).json()
    source = approval["evidence_record"]["source"]
    # The reviewer can fetch exactly the extraction's version, with the version id as a cross-check
    body = (await client.get(_url(doc_id, source["version_number"], created_extraction["version_id"]),
                             headers=issuer_headers)).json()
    assert (body["version_id"], body["sha256_hash"], body["source_text_sha256"]) == (
        source["document_version_id"], source["document_sha256"], source["source_text_sha256"])
    claim = next(c for c in created_extraction["claims"] if c["field_name"] == "jurisdiction")
    assert claim["supporting_passage"] in body["pages"][claim["source_page"] - 1]["text_content"]
    # A newer version supersedes the approval, but the approved version's text stays what was bound
    await _new_version(client, doc_id, make_source_pdf("Amendment."), issuer_headers)
    again = (await client.get(_url(doc_id, 1), headers=issuer_headers)).json()
    assert again["source_text_sha256"] == source["source_text_sha256"] and again["is_current_version"] is False


async def test_an_extraction_of_an_old_version_can_be_checked_against_its_own_text(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict
):
    """The audit gap P-D: the current-version text endpoint cannot show an older extraction's text."""
    doc_id = created_document["id"]
    await _new_version(client, doc_id, make_text_pdf([["Completely different second version."]]), issuer_headers)
    current = (await client.get(f"/documents/{doc_id}/text", headers=issuer_headers)).json()
    assert current["version_number"] == 2 and current["version_id"] != created_extraction["version_id"]
    own = (await client.get(_url(doc_id, 1, created_extraction["version_id"]), headers=issuer_headers)).json()
    passage = next(c for c in created_extraction["claims"] if c["field_name"] == "ownership_nature")["supporting_passage"]
    assert passage in own["pages"][0]["text_content"]
    assert passage not in json.dumps(current)


# ── 12. Current-version endpoint ─────────────────────────────────────────────


async def test_current_version_endpoint_names_its_version(client: AsyncClient, two_versions: dict, issuer_headers: dict):
    body = (await client.get(f"/documents/{two_versions['doc']['id']}/text", headers=issuer_headers)).json()
    assert set(body) == {"document_id", "version_number", "version_id", "pages"}
    assert (body["version_number"], body["version_id"]) == (2, two_versions["v2"]["id"])
    specific = (await client.get(_url(two_versions["doc"]["id"], 2), headers=issuer_headers)).json()
    assert body["pages"] == specific["pages"]


# ── 13. Concurrency ──────────────────────────────────────────────────────────


async def test_reads_racing_with_uploads_never_mix_versions(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_text_pdf(V1_PAGES), issuer_headers)
    v1_id = doc["current_version"]["id"]
    expected_v1 = [{"page_number": i + 1, "text_content": lines[0]} for i, lines in enumerate(V1_PAGES)]

    async def upload(i: int):
        return await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                                 files={"file": ("v.pdf", make_text_pdf([[f"Concurrent version {i}."]]), "application/pdf")})

    async def read_v1():
        return await client.get(_url(doc["id"], 1, v1_id), headers=issuer_headers)

    results = await asyncio.wait_for(asyncio.gather(
        *(read_v1() for _ in range(4)), upload(1), *(read_v1() for _ in range(4)), upload(2), read_v1()), 60)
    reads = [r for r in results if r.request.method == "GET"]
    uploads = [r for r in results if r.request.method == "POST"]
    assert all(r.status_code == 200 and r.json()["pages"] == expected_v1 and r.json()["version_id"] == v1_id
               for r in reads), [r.text for r in reads]
    created = [u.json() for u in uploads if u.status_code == 201]
    assert all(u.status_code in (201, 409, 503) for u in uploads)
    for version in created:  # every new version serves its own text, never version 1's
        body = (await client.get(_url(doc["id"], version["version_number"], version["id"]), headers=issuer_headers)).json()
        assert body["pages"][0]["text_content"].startswith("Concurrent version")


# ── 14. No blockchain dependency, no internal paths, OpenAPI ─────────────────


async def test_no_blockchain_connection_is_needed(client: AsyncClient, two_versions: dict, issuer_headers: dict):
    def unavailable():
        raise RuntimeError("no Stellar node")

    app.dependency_overrides[get_stellar_service] = unavailable
    r = await client.get(_url(two_versions["doc"]["id"], 1), headers=issuer_headers)
    assert r.status_code == 200


async def test_no_internal_storage_path_is_exposed(
    client: AsyncClient, two_versions: dict, issuer_headers: dict, async_db: AsyncSession
):
    stored = (await async_db.execute(text("SELECT stored_path FROM document_versions WHERE id = :v"),
                                     {"v": two_versions["v1"]["id"]})).scalar_one()
    r = await client.get(_url(two_versions["doc"]["id"], 1), headers=issuer_headers)
    assert stored not in r.text and stored.rsplit("/", 1)[-1] not in r.text


def test_openapi_documents_the_endpoint():
    schema = app.openapi()
    operation = schema["paths"]["/documents/{document_id}/versions/{version_number}/text"]["get"]
    assert "no fallback" in operation["description"] and "does not establish legal validity" in operation["description"]
    params = {p["name"]: p for p in operation["parameters"]}
    assert params["version_number"]["required"] is True and params["version_id"]["required"] is False
    assert set(operation["responses"]) >= {"200", "404", "409", "422"}
    properties = schema["components"]["schemas"]["DocumentVersionTextResponse"]["properties"]
    assert set(properties) == RESPONSE_KEYS
    assert "MOCK" not in json.dumps(properties) and MOCK_SOURCE_PAGES  # schema only, no content
