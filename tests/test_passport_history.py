"""
Passport history for API consumers: GET /passport/{network}/{asset_code}/{issuer_key}/history.

The MVP workflow's last two steps: an API consumer retrieves the historical versions behind the
passport, and a material document change makes the earlier approvals stale until re-approval.
Anchoring runs against the in-memory Horizon of tests/fake_horizon.py (simulated, no network).

Coverage:
1. An approved passport: its version, approvals, approved claims and current manifest digest
2. A new document version: the passport answers 409, the history shows why
3. Re-approval of the new version: v1 stays in the history as superseded, v2 is served
4. Anchors: stored state and whether they match the current evidence
5. Drafts and untrusted records show nothing they should not
6. The key's scope and permission, as for the passport
"""

import json

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.evidence_manifest import build_manifest, manifest_digest
from app.models.asset import Asset
from tests.conftest import ISSUER_TOKEN, LEGAL_TOKEN
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import _approve, _extract, _new_version, _passport_url, _upload
from tests.test_stellar_anchoring import (  # noqa: F401 — pytest fixtures
    _anchor,
    anchor_account,
    anchors,
    horizon,
    passport,
    storage,
)


async def _history(client: AsyncClient, asset: dict, headers: dict) -> dict:
    r = await client.get(_passport_url(asset) + "/history", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _only_document(history: dict) -> dict:
    (document,) = history["documents"]
    return document


# ── 1. An approved passport ──────────────────────────────────────────────────


async def test_the_history_of_an_approved_passport(
    client, passport, inst_headers, storage, async_db: AsyncSession,
):
    history = await _history(client, passport.asset, inst_headers)
    document = _only_document(history)
    assert history["current_passport_available"] is True
    asset = await async_db.get(Asset, passport.asset["id"])
    assert history["current_manifest_digest"] == manifest_digest(await build_manifest(async_db, asset, storage))
    assert [(v["version_number"], v["is_current"]) for v in document["versions"]] == [(1, True)]
    assert document["versions"][0]["sha256_hash"] == passport.document["current_version"]["sha256_hash"]

    (version,) = document["approved_versions"]
    assert (version["extraction_id"], version["document_version_number"], version["approval_state"],
            version["served_in_current_passport"]) == (passport.extraction["id"], 1, "current", True)
    assert [a["approval_type"] for a in version["approvals"]] == ["issuer_attestation", "legal_verification"]
    assert all(a["is_current"] and a["counted_in_current_passport"] and not a["stale_reasons"]
               for a in version["approvals"])

    # What was approved, exactly as the extraction's claims were
    claims = {c["field_name"]: c for c in passport.extraction["claims"]}
    for approval in version["approvals"]:
        assert {c["field_name"] for c in approval["approved_claims"]} == set(claims)
        for c in approval["approved_claims"]:
            if not c["is_unknown"]:
                assert (c["value"], c["source_page"], c["supporting_passage"]) == (
                    claims[c["field_name"]]["value"], claims[c["field_name"]]["source_page"],
                    claims[c["field_name"]]["supporting_passage"])
            else:
                assert (c["value"], c["source_page"], c["supporting_passage"]) == ("unknown", None, None)

    # The passport's own approvals are the history's current ones
    served = (await client.get(_passport_url(passport.asset), headers=inst_headers)).json()
    assert sorted(a["binding_digest"] for a in served["approvals"]) == sorted(
        a["binding_digest"] for a in version["approvals"])
    assert history["disclaimer"] == served["disclaimer"]


async def test_the_history_names_no_reviewer_and_no_credential(client, passport, inst_headers):
    r = await client.get(_passport_url(passport.asset) + "/history", headers=inst_headers)
    for secret in ("alice-chen-01", "bob-martinez-01", "Alice Chen", "Bob Martinez", ISSUER_TOKEN, LEGAL_TOKEN,
                   inst_headers["X-API-Key"], "stored_path", "download"):
        assert secret not in r.text


# ── 2–3. A material document change, and the re-approval ─────────────────────


async def test_a_new_version_makes_the_approvals_stale_until_it_is_approved(
    client, passport, issuer_headers, legal_headers, inst_headers,
):
    await _new_version(client, passport.document["id"], make_source_pdf("Amendment 1."), issuer_headers)

    # The passport serves nothing; the history says what happened
    assert (await client.get(_passport_url(passport.asset), headers=inst_headers)).status_code == 409
    history = await _history(client, passport.asset, inst_headers)
    document = _only_document(history)
    assert (history["current_passport_available"], history["current_manifest_digest"]) == (False, None)
    assert [(v["version_number"], v["is_current"]) for v in document["versions"]] == [(1, False), (2, True)]
    (v1,) = document["approved_versions"]
    assert (v1["document_version_number"], v1["approval_state"], v1["served_in_current_passport"]) == (
        1, "superseded", False)
    assert "document_version_superseded" in v1["stale_reasons"]
    for approval in v1["approvals"]:
        assert (approval["is_current"], approval["counted_in_current_passport"]) == (False, False)
        assert "document_version_superseded" in approval["stale_reasons"]
        assert approval["approved_claims"]  # still shown, as a historical record

    # Re-approval of version 2 restores a current passport; version 1 stays in the history
    ex2 = await _extract(client, passport.document["id"], issuer_headers)
    for headers in (issuer_headers, legal_headers):
        assert (await _approve(client, ex2["id"], headers)).status_code == 201
    served = (await client.get(_passport_url(passport.asset), headers=inst_headers)).json()
    assert served["source_document"]["version_number"] == 2

    history = await _history(client, passport.asset, inst_headers)
    v1, v2 = _only_document(history)["approved_versions"]
    assert history["current_passport_available"] is True
    assert (v1["document_version_number"], v1["approval_state"], v1["served_in_current_passport"]) == (
        1, "superseded", False)
    assert (v2["extraction_id"], v2["document_version_number"], v2["approval_state"],
            v2["served_in_current_passport"]) == (ex2["id"], 2, "current", True)
    assert all(a["counted_in_current_passport"] for a in v2["approvals"])


async def test_an_inactive_asset_still_has_a_history(client, passport, admin_client, inst_headers):
    r = await admin_client.patch(f"/assets/{passport.asset['id']}", json={"status": "suspended"})
    assert r.status_code == 200, r.text
    assert (await client.get(_passport_url(passport.asset), headers=inst_headers)).status_code == 409
    history = await _history(client, passport.asset, inst_headers)
    (v1,) = _only_document(history)["approved_versions"]
    assert (history["asset"]["status"], history["current_passport_available"], v1["approval_state"]) == (
        "suspended", False, "stale")
    assert {"asset_not_active", "asset_information_changed"} <= set(v1["stale_reasons"])


# ── 4. Anchors ───────────────────────────────────────────────────────────────


async def test_anchors_are_listed_with_whether_they_match_the_current_evidence(
    client, passport, anchors, horizon, issuer_headers, inst_headers,
):
    anchored = await _anchor(client, passport.asset["id"], issuer_headers)
    assert anchored.status_code == 201, anchored.text
    history = await _history(client, passport.asset, inst_headers)
    (anchor,) = history["anchors"]
    assert (anchor["manifest_digest"], anchor["status"], anchor["matches_current_evidence"]) == (
        history["current_manifest_digest"], "confirmed", True)
    assert anchor["transaction_hash"] == anchored.json()["transaction_hash"]

    # A material change: the stored anchor no longer matches anything current; nothing is re-checked
    submissions = len(horizon.submissions)
    await _new_version(client, passport.document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    (anchor,) = (await _history(client, passport.asset, inst_headers))["anchors"]
    assert (anchor["status"], anchor["matches_current_evidence"]) == ("confirmed", False)
    assert len(horizon.submissions) == submissions


# ── 5. What is never shown ───────────────────────────────────────────────────


async def test_drafts_are_never_shown(client, passport, issuer_headers, inst_headers):
    other = await _upload(client, passport.asset["id"], make_source_pdf("Opinion."), issuer_headers, title="Opinion")
    await _extract(client, other["id"], issuer_headers)                    # a draft, never approved
    await _extract(client, passport.document["id"], issuer_headers)        # a second, unapproved extraction
    history = await _history(client, passport.asset, inst_headers)
    first, second = history["documents"]
    assert [v["extraction_id"] for v in first["approved_versions"]] == [passport.extraction["id"]]
    assert (second["title"], second["approved_versions"]) == ("Opinion", [])
    assert [v["version_number"] for v in second["versions"]] == [1]


async def test_an_approval_whose_binding_was_altered_shows_no_claims(
    client, passport, inst_headers, async_db: AsyncSession,
):
    await async_db.execute(
        text("UPDATE review_approvals SET evidence_snapshot = :s WHERE extraction_id = :e AND approval_type = "
             "'legal_verification'"),
        {"s": json.dumps({"format": 2, "forged": True}), "e": passport.extraction["id"]},
    )
    await async_db.commit()
    (version,) = _only_document(await _history(client, passport.asset, inst_headers))["approved_versions"]
    issuer, legal = version["approvals"]
    assert (legal["is_current"], legal["approved_claims"]) == (False, [])
    assert "binding_integrity_failed" in legal["stale_reasons"]
    assert issuer["is_current"] and issuer["approved_claims"]


# ── 6. Scope and permission ──────────────────────────────────────────────────


async def test_the_history_has_the_passport_scope(client, passport, inst_headers, inst_beta_headers):
    url = _passport_url(passport.asset) + "/history"
    assert (await client.get(url)).status_code == 403                                        # no key
    assert (await client.get(url, headers={"X-API-Key": "not-a-key"})).status_code == 401
    out_of_scope = await client.get(url, headers=inst_beta_headers)                         # Harbor only
    unknown = await client.get(_passport_url({**passport.asset, "stellar_asset_code": "NOPE"}) + "/history",
                               headers=inst_headers)
    assert (out_of_scope.status_code, unknown.status_code) == (404, 404)
    assert out_of_scope.json() == unknown.json()
    assert (await client.get(url, headers={"Authorization": f"Bearer {ISSUER_TOKEN}"})).status_code == 403
