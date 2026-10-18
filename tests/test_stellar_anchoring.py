"""
Stellar Testnet evidence anchoring (STELLAR_ANCHORING.md) — against an in-memory Horizon.

The transaction building and signing use the real stellar-sdk; only the network is simulated
(tests/fake_horizon.py). No test reaches the real network or reads the real account file.

Coverage:
 1. Deterministic manifest and exactly what it commits
 2. Asset identity (strict StrKey, Testnet only)
 3. Document, claim and approval changes
 4. Evidence and record tampering
 5. Incorrect network configuration
 6. Transaction failures and retries
 7. Pending and confirmed transactions
 8. Idempotent submission (sequential and concurrent)
 9. Missing transactions (Testnet reset)
10. Cross-tenant access and permissions
11. Existing approval and verification protections
12. Secrets are never exposed
"""

import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from stellar_sdk import Keypair, Network, TransactionEnvelope

from app.approval_integrity import canonical_json
from app.evidence_manifest import ManifestUnavailable, build_manifest, manifest_digest
from app.main import app
from app.models.asset import Asset
from app.services.stellar_anchor import StellarAnchorService, get_anchor_service
from app.services.storage import StorageService
from tests.conftest import INST_KEY_ALPHA, ISSUER_TOKEN, LEGAL_TOKEN, MERIDIAN_ISSUER, _MERIDIAN_ASSET
from tests.fake_horizon import FakeHorizon
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (
    _approve,
    _extract,
    _new_version,
    _passport_url,
    _snapshot,
    _upload,
    file_db,  # noqa: F401 — pytest fixture
)

TESTNET_ASSET = {**_MERIDIAN_ASSET, "blockchain_network": "stellar-testnet", "stellar_asset_code": "MRPT",
                 "stellar_issuer_public_key": Keypair.random().public_key}
FORBIDDEN_IN_MANIFEST = ("alice-chen-01", "bob-martinez-01", "Alice Chen", "Bob Martinez", MERIDIAN_ISSUER,
                         ISSUER_TOKEN, LEGAL_TOKEN, INST_KEY_ALPHA)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def horizon() -> FakeHorizon:
    return FakeHorizon()


@pytest.fixture
def anchor_account(tmp_path: Path, horizon: FakeHorizon) -> SimpleNamespace:
    keypair = Keypair.random()
    file = tmp_path / "stellar_testnet_anchor.json"
    file.write_text(json.dumps({"network": "stellar-testnet", "public_key": keypair.public_key,
                                "secret_seed": keypair.secret}), encoding="utf-8")
    horizon.fund(keypair.public_key)
    return SimpleNamespace(file=str(file), public_key=keypair.public_key, secret=keypair.secret)


def _service(file: str, horizon: FakeHorizon) -> StellarAnchorService:
    return StellarAnchorService(file, base_fee=1000, tx_timeout_seconds=300, transport=horizon.transport)


@pytest.fixture
def anchors(client: AsyncClient, horizon: FakeHorizon, anchor_account) -> StellarAnchorService:
    service = _service(anchor_account.file, horizon)
    app.dependency_overrides[get_anchor_service] = lambda: service
    return service


@pytest.fixture
def storage(tmp_path: Path) -> StorageService:
    return StorageService(str(tmp_path / "storage"))  # the client fixture's storage


async def _passport_asset(client, admin_client, issuer_headers, legal_headers, source_pdf, asset=TESTNET_ASSET,
                          approvers=("issuer", "legal")) -> SimpleNamespace:
    created = await admin_client.post("/assets", json=asset)
    assert created.status_code == 201, created.text
    asset = created.json()
    document = await _upload(client, asset["id"], source_pdf, issuer_headers)
    extraction = await _extract(client, document["id"], issuer_headers)
    for name in approvers:
        headers = {"issuer": issuer_headers, "legal": legal_headers}[name]
        assert (await _approve(client, extraction["id"], headers)).status_code == 201
    return SimpleNamespace(asset=asset, document=document, extraction=extraction)


@pytest.fixture
async def passport(client, admin_client, issuer_headers, legal_headers, source_pdf) -> SimpleNamespace:
    return await _passport_asset(client, admin_client, issuer_headers, legal_headers, source_pdf)


async def _anchor(client: AsyncClient, asset_id: str, headers: dict):
    return await client.post(f"/assets/{asset_id}/stellar-anchors", headers=headers)


async def _verify(client: AsyncClient, asset: dict, inst_headers: dict) -> dict:
    r = await client.get(_passport_url(asset) + "/stellar-anchor", headers=inst_headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _asset_row(db: AsyncSession, asset_id: str) -> Asset:
    return (await db.execute(select(Asset).where(Asset.id == asset_id).execution_options(
        populate_existing=True))).scalar_one()


# ── 1. Deterministic manifest ────────────────────────────────────────────────


async def test_the_manifest_is_deterministic_and_commits_the_passport_evidence(
    passport, async_db: AsyncSession, storage: StorageService,
):
    asset = await _asset_row(async_db, passport.asset["id"])
    first = await build_manifest(async_db, asset, storage)
    second = await build_manifest(async_db, asset, storage)
    assert first == second and manifest_digest(first) == manifest_digest(second)
    assert manifest_digest(first) == hashlib.sha256(canonical_json(first).encode("utf-8")).hexdigest()
    # Canonical: sorted keys, no whitespace between tokens
    assert canonical_json(first) == json.dumps(first, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert (first["schema"], first["schema_version"]) == ("rwa-legal-passport/evidence-manifest", 1)
    assert first["asset"] == {
        "identifier_type": "stellar_classic_asset", "network": "stellar-testnet",
        "network_passphrase": Network.TESTNET_NETWORK_PASSPHRASE, "asset_code": "MRPT",
        "asset_issuer": TESTNET_ASSET["stellar_issuer_public_key"], "stellar_asset_type": "credit_alphanum4",
        "asset_id": passport.asset["id"], "issuer_organization": TESTNET_ASSET["issuer_organization_name"],
        "rwa_asset_type": "real_estate", "jurisdiction": "US-NY", "status": "active", "material_revision": 1,
    }
    (document,) = first["documents"]
    assert document["document_id"] == passport.document["id"]
    assert document["extraction_id"] == passport.extraction["id"]
    source = document["evidence"]["source"]
    assert (source["document_version_id"], source["version_number"], source["document_sha256"]) == (
        passport.document["current_version"]["id"], 1, passport.document["current_version"]["sha256_hash"])
    assert {c["claim_id"] for c in document["evidence"]["claims"]} == {c["id"] for c in passport.extraction["claims"]}
    for claim in document["evidence"]["claims"]:
        assert {"value", "evidence_status", "page_text_sha256", "quotation_sha256", "correction_id"} <= set(claim)
    assert sorted(a["approval_type"] for a in document["approvals"]) == ["issuer_attestation", "legal_verification"]
    assert all(len(a["binding_digest"]) == 64 for a in document["approvals"])
    assert first["verification"]["verification_level"] == "independently_verified"
    # No reviewer identity, internal tenant id or credential is committed
    text_form = canonical_json(first)
    for forbidden in FORBIDDEN_IN_MANIFEST:
        assert forbidden not in text_form, forbidden


async def test_no_manifest_without_a_current_approved_passport(
    client, admin_client, issuer_headers, async_db: AsyncSession, storage: StorageService, source_pdf: bytes,
):
    created = (await admin_client.post("/assets", json=TESTNET_ASSET)).json()
    document = await _upload(client, created["id"], source_pdf, issuer_headers)
    await _extract(client, document["id"], issuer_headers)  # a draft only
    with pytest.raises(ManifestUnavailable):
        await build_manifest(async_db, await _asset_row(async_db, created["id"]), storage)


# ── 2. Asset identity ────────────────────────────────────────────────────────


async def test_an_issuer_key_with_a_bad_checksum_is_refused_at_registration(admin_client: AsyncClient):
    valid = Keypair.random().public_key
    broken = valid[:-1] + ("A" if valid[-1] != "A" else "B")  # same alphabet, wrong CRC16
    r = await admin_client.post("/assets", json={**TESTNET_ASSET, "stellar_asset_code": "CHKS",
                                                 "stellar_issuer_public_key": broken})
    assert r.status_code == 422 and "checksum" in r.text
    r = await admin_client.post("/assets", json={**TESTNET_ASSET, "stellar_asset_code": "CHKS",
                                                 "stellar_issuer_public_key": valid})
    assert r.status_code == 201


async def test_mainnet_assets_are_never_anchored(
    client, admin_client, issuer_headers, legal_headers, source_pdf, anchors, horizon, async_db: AsyncSession,
):
    mainnet = await _passport_asset(client, admin_client, issuer_headers, legal_headers, source_pdf,
                                    asset=_MERIDIAN_ASSET)
    rows = await _snapshot(async_db)
    r = await _anchor(client, mainnet.asset["id"], issuer_headers)
    assert r.status_code == 409 and r.json()["anchor_refusal"] == "mainnet_not_supported"
    assert horizon.submissions == [] and await _snapshot(async_db) == rows


async def test_the_transaction_commits_the_digest_for_exactly_this_asset_on_testnet(
    client, passport, anchors, horizon, anchor_account, issuer_headers, async_db: AsyncSession, storage,
):
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    digest = manifest_digest(await build_manifest(async_db, await _asset_row(async_db, passport.asset["id"]), storage))
    assert body["manifest_digest"] == digest and body["status"] == "confirmed"
    assert horizon.submissions == [body["transaction_hash"]]
    tx = horizon.transactions[body["transaction_hash"]]
    assert tx["source_account"] == anchor_account.public_key and tx["memo_type"] == "hash"
    assert bytes.fromhex(digest) == base64.b64decode(tx["memo"])
    (operation,) = horizon.operations[body["transaction_hash"]]
    assert operation["name"] == f"lp:{passport.asset['id']}" == body["data_name"]
    # The stored envelope is a Testnet transaction: under another passphrase its hash differs
    row = (await async_db.execute(text("SELECT envelope_xdr FROM stellar_anchors"))).scalar_one()
    assert TransactionEnvelope.from_xdr(row, Network.TESTNET_NETWORK_PASSPHRASE).hash_hex() == body["transaction_hash"]
    assert TransactionEnvelope.from_xdr(row, Network.PUBLIC_NETWORK_PASSPHRASE).hash_hex() != body["transaction_hash"]
    assert body["horizon_transaction_url"] == f"https://horizon-testnet.stellar.org/transactions/{body['transaction_hash']}"


# ── End-to-end (simulated network) ───────────────────────────────────────────


async def test_anchor_then_verify_through_the_passport_api(
    client, passport, anchors, issuer_headers, inst_headers,
):
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "not_anchored"
    anchored = (await _anchor(client, passport.asset["id"], issuer_headers)).json()
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"], result["evidence_matches_anchor"]) == ("verified", True, True)
    assert result["current_manifest_digest"] == anchored["manifest_digest"] == result["anchor"]["manifest_digest"]
    assert result["onchain"]["checked"] is True
    for key in ("network_passphrase_matches", "transaction_found", "successful", "source_account_matches",
                "memo_matches", "operation_matches"):
        assert result["onchain"][key] is True, key
    committed = result["committed_manifest_canonical_json"]
    assert hashlib.sha256(committed.encode("utf-8")).hexdigest() == anchored["manifest_digest"]
    assert result["asset"] == {"identifier_type": "stellar_classic_asset", "network": "stellar-testnet",
                               "asset_code": "MRPT", "asset_issuer": TESTNET_ASSET["stellar_issuer_public_key"],
                               "stellar_asset_type": "credit_alphanum4"}
    assert "not establish legal validity" in result["disclaimer"]


# ── 3. Document, claim and approval changes ──────────────────────────────────


async def test_new_evidence_is_detected_until_it_is_anchored_again(
    client, passport, anchors, horizon, issuer_headers, legal_headers, inst_headers, source_pdf,
):
    first = (await _anchor(client, passport.asset["id"], issuer_headers)).json()
    # A second approved document changes what the passport serves
    other = await _upload(client, passport.asset["id"], source_pdf, issuer_headers, title="Opinion")
    await _approve(client, (await _extract(client, other["id"], issuer_headers))["id"], issuer_headers)
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"], result["evidence_matches_anchor"]) == ("evidence_changed", False, False)
    assert result["anchor"]["manifest_digest"] == first["manifest_digest"] != result["current_manifest_digest"]

    second = await _anchor(client, passport.asset["id"], issuer_headers)
    assert second.status_code == 201 and second.json()["manifest_digest"] == result["current_manifest_digest"]
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "verified"
    assert len(horizon.submissions) == 2


async def test_a_new_document_version_changes_the_commitment(
    client, passport, anchors, issuer_headers, legal_headers, inst_headers,
):
    await _anchor(client, passport.asset["id"], issuer_headers)
    await _new_version(client, passport.document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    result = await _verify(client, passport.asset, inst_headers)
    # v1's approvals are superseded: no current passport, so nothing current matches the anchor
    assert (result["result"], result["current_passport_available"], result["verified"]) == (
        "evidence_changed", False, False)
    ex2 = await _extract(client, passport.document["id"], issuer_headers)
    await _approve(client, ex2["id"], legal_headers)
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "evidence_changed"
    assert (await _anchor(client, passport.asset["id"], issuer_headers)).status_code == 201
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "verified"


# ── 4. Evidence and record tampering ─────────────────────────────────────────


async def test_tampered_live_evidence_is_never_verified(
    client, passport, anchors, issuer_headers, inst_headers, async_db: AsyncSession,
):
    await _anchor(client, passport.asset["id"], issuer_headers)
    await async_db.execute(text("UPDATE legal_claims SET value = 'Tampered.' WHERE extraction_id = :e "
                                "AND field_name = 'jurisdiction'"), {"e": passport.extraction["id"]})
    await async_db.commit()
    result = await _verify(client, passport.asset, inst_headers)
    assert result["result"] == "evidence_changed" and result["verified"] is False


@pytest.mark.parametrize("column,value", [
    ("manifest_json", None),          # the stored manifest no longer hashes to its digest
    ("manifest_digest", "0" * 64),    # the digest no longer matches the manifest
    ("data_name", "lp:another-asset"),
])
async def test_a_tampered_anchor_record_is_never_verified(
    client, passport, anchors, issuer_headers, inst_headers, async_db: AsyncSession, column, value,
):
    await _anchor(client, passport.asset["id"], issuer_headers)
    if column == "manifest_json":
        await async_db.execute(text("UPDATE stellar_anchors SET manifest_json = replace(manifest_json, 'US-NY', 'KY')"))
    else:
        await async_db.execute(text(f"UPDATE stellar_anchors SET {column} = :v"), {"v": value})
    await async_db.commit()
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"]) == ("anchor_record_invalid", False)


@pytest.mark.parametrize("field", ["memo", "source_account", "operation_value"])
async def test_an_onchain_transaction_that_does_not_match_is_never_verified(
    client, passport, anchors, horizon, issuer_headers, inst_headers, field,
):
    tx_hash = (await _anchor(client, passport.asset["id"], issuer_headers)).json()["transaction_hash"]
    if field == "memo":
        horizon.transactions[tx_hash]["memo"] = base64.b64encode(b"\x00" * 32).decode()
    elif field == "source_account":
        horizon.transactions[tx_hash]["source_account"] = Keypair.random().public_key
    else:
        horizon.operations[tx_hash][0]["value"] = base64.b64encode(b"\x01" * 32).decode()
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"]) == ("onchain_mismatch", False)


async def test_a_transaction_of_another_account_with_the_same_memo_is_not_trusted(
    client, passport, horizon, tmp_path, issuer_headers, inst_headers,
):
    """An anchor signed by account A is never verified once the trusted account is B."""
    a = Keypair.random()
    file_a = tmp_path / "a.json"
    file_a.write_text(json.dumps({"network": "stellar-testnet", "public_key": a.public_key, "secret_seed": a.secret}))
    horizon.fund(a.public_key)
    app.dependency_overrides[get_anchor_service] = lambda: _service(str(file_a), horizon)
    assert (await _anchor(client, passport.asset["id"], issuer_headers)).status_code == 201
    b = Keypair.random()
    file_b = tmp_path / "b.json"
    file_b.write_text(json.dumps({"network": "stellar-testnet", "public_key": b.public_key, "secret_seed": b.secret}))
    app.dependency_overrides[get_anchor_service] = lambda: _service(str(file_b), horizon)
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["onchain"]["source_account_matches"]) == ("onchain_mismatch", False)


# ── 5. Incorrect network configuration ───────────────────────────────────────


async def test_a_horizon_server_on_another_network_is_refused(
    client, passport, anchors, horizon, issuer_headers, inst_headers, async_db: AsyncSession,
):
    horizon.passphrase = Network.PUBLIC_NETWORK_PASSPHRASE
    rows = await _snapshot(async_db)
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 503 and r.json()["anchor_refusal"] == "wrong_network"
    assert horizon.submissions == [] and await _snapshot(async_db) == rows
    assert (await async_db.execute(text("SELECT COUNT(*) FROM stellar_anchors"))).scalar_one() == 0
    # An existing anchor is not verified against the wrong network either
    horizon.passphrase = Network.TESTNET_NETWORK_PASSPHRASE
    await _anchor(client, passport.asset["id"], issuer_headers)
    horizon.passphrase = Network.PUBLIC_NETWORK_PASSPHRASE
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["onchain"]["error"]) == ("network_unavailable", "network_passphrase_mismatch")


@pytest.mark.parametrize("content", [
    None,                                                                   # no file
    "not json",
    {"network": "stellar-mainnet"},                                         # mainnet account file
    {"network": "stellar-testnet", "public_key": "G", "secret_seed": "SNOTASEED"},
    "mismatch",                                                             # seed of another key
])
async def test_a_missing_or_invalid_signing_account_disables_anchoring(
    client, passport, horizon, tmp_path, issuer_headers, inst_headers, async_db: AsyncSession, content,
):
    file = tmp_path / "signing.json"
    if content == "mismatch":
        kp = Keypair.random()
        file.write_text(json.dumps({"network": "stellar-testnet", "public_key": Keypair.random().public_key,
                                    "secret_seed": kp.secret}))
    elif content == "not json":
        file.write_text("not json")
    elif content is not None:
        file.write_text(json.dumps(content))
    app.dependency_overrides[get_anchor_service] = lambda: _service(str(file), horizon)
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 503 and r.json()["anchor_refusal"] == "anchoring_not_configured"
    assert "SNOTASEED" not in r.text and "S" + "NOTASEED" not in r.text
    assert horizon.submissions == []
    assert (await async_db.execute(text("SELECT COUNT(*) FROM stellar_anchors"))).scalar_one() == 0


# ── 6. Transaction failures ──────────────────────────────────────────────────


@pytest.mark.parametrize("mode,reason", [
    ("reject", "tx_insufficient_fee"),
    ("include_failed", "tx_failed:op_low_reserve"),
])
async def test_a_failed_transaction_is_never_verified_and_can_be_retried(
    client, passport, anchors, horizon, issuer_headers, inst_headers, mode, reason,
):
    horizon.submit_mode = mode
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 502 and (r.json()["status"], r.json()["failure_reason"]) == ("failed", reason)
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"]) == ("failed", False)

    horizon.submit_mode = "include"
    retry = await _anchor(client, passport.asset["id"], issuer_headers)
    assert retry.status_code == 201 and retry.json()["attempts"] == 2 and retry.json()["id"] == r.json()["id"]
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "verified"


async def test_an_unreachable_network_before_signing_stores_nothing(
    client, passport, anchors, horizon, issuer_headers, async_db: AsyncSession,
):
    horizon.down = True
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 503 and r.json()["anchor_refusal"] == "network_unavailable"
    assert (await async_db.execute(text("SELECT COUNT(*) FROM stellar_anchors"))).scalar_one() == 0


# ── 7. Pending and confirmed ─────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["timeout_included", "connect_error"])
async def test_a_lost_submission_response_stays_pending_and_is_resolved_by_refresh(
    client, passport, anchors, horizon, issuer_headers, inst_headers, mode,
):
    horizon.submit_mode = mode
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 202 and r.json()["status"] == "pending"
    anchor = r.json()
    if mode == "timeout_included":
        # The network has it: the read-only verification reports what the network says
        assert (await _verify(client, passport.asset, inst_headers))["result"] == "verified"
        refreshed = await client.post(f"/assets/{passport.asset['id']}/stellar-anchors/{anchor['id']}/refresh",
                                      headers=issuer_headers)
        assert refreshed.json()["status"] == "confirmed" and refreshed.json()["ledger"] is not None
    else:
        result = await _verify(client, passport.asset, inst_headers)
        assert (result["result"], result["verified"]) == ("pending", False)
        # Submitting again never creates a second transaction while it may still be included
        again = await _anchor(client, passport.asset["id"], issuer_headers)
        assert again.status_code == 200 and again.json()["transaction_hash"] == anchor["transaction_hash"]
        assert len(horizon.submissions) == 0  # the POST never reached Horizon


async def test_an_expired_pending_transaction_becomes_failed_and_is_retried(
    client, passport, anchors, horizon, issuer_headers, inst_headers, async_db: AsyncSession,
):
    horizon.submit_mode = "timeout_lost"
    anchor = (await _anchor(client, passport.asset["id"], issuer_headers)).json()
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "pending"
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S.%f")
    await async_db.execute(text("UPDATE stellar_anchors SET valid_until = :t"), {"t": past})
    await async_db.commit()
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "failed"
    refreshed = await client.post(f"/assets/{passport.asset['id']}/stellar-anchors/{anchor['id']}/refresh",
                                  headers=issuer_headers)
    assert (refreshed.json()["status"], refreshed.json()["failure_reason"]) == ("failed", "expired_not_included")
    horizon.submit_mode = "include"
    retry = await _anchor(client, passport.asset["id"], issuer_headers)
    assert retry.status_code == 201 and (retry.json()["attempts"], retry.json()["status"]) == (2, "confirmed")


# ── 8. Idempotent submission ─────────────────────────────────────────────────


async def test_anchoring_the_same_evidence_twice_submits_one_transaction(
    client, passport, anchors, horizon, issuer_headers, admin_headers,
):
    first = await _anchor(client, passport.asset["id"], issuer_headers)
    second = await _anchor(client, passport.asset["id"], admin_headers)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"] and len(horizon.submissions) == 1
    history = (await client.get(f"/assets/{passport.asset['id']}/stellar-anchors", headers=issuer_headers)).json()
    assert [a["id"] for a in history] == [first.json()["id"]]


async def test_concurrent_requests_anchor_the_same_evidence_once(
    file_db, client, admin_client, issuer_headers, legal_headers, horizon, anchor_account, source_pdf,
):
    app.dependency_overrides[get_anchor_service] = lambda: _service(anchor_account.file, horizon)
    passport = await _passport_asset(client, admin_client, issuer_headers, legal_headers, source_pdf)
    results = await asyncio.wait_for(asyncio.gather(
        *(_anchor(client, passport.asset["id"], issuer_headers) for _ in range(4))), 60)
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and set(codes) <= {200, 201}, codes
    assert len({r.json()["id"] for r in results}) == 1 and len(horizon.submissions) == 1


# ── 9. Missing transactions ──────────────────────────────────────────────────


async def test_a_confirmed_anchor_missing_after_a_testnet_reset_is_unavailable_not_verified(
    client, passport, anchors, horizon, issuer_headers, inst_headers,
):
    await _anchor(client, passport.asset["id"], issuer_headers)
    horizon.reset()
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"], result["onchain"]["transaction_found"]) == (
        "transaction_unavailable", False, False)
    assert result["anchor"]["status"] == "confirmed"  # the record keeps what was observed
    again = await _anchor(client, passport.asset["id"], issuer_headers)
    assert again.status_code == 200  # same evidence: already confirmed, nothing resubmitted


async def test_an_unfunded_account_is_reported_and_nothing_is_stored(
    client, passport, anchors, horizon, anchor_account, issuer_headers, async_db: AsyncSession,
):
    horizon.accounts.pop(anchor_account.public_key)
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 503 and r.json()["anchor_refusal"] == "anchor_account_not_found"
    assert (await async_db.execute(text("SELECT COUNT(*) FROM stellar_anchors"))).scalar_one() == 0


async def test_verification_on_an_unreachable_network_is_never_verified(
    client, passport, anchors, horizon, issuer_headers, inst_headers,
):
    await _anchor(client, passport.asset["id"], issuer_headers)
    horizon.down = True
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"]) == ("network_unavailable", False)


# ── 10. Cross-tenant access and permissions ──────────────────────────────────


async def test_another_tenant_can_neither_anchor_nor_see_anchors(
    client, admin_client, passport, anchors, horizon, issuer_headers, other_issuer_headers, other_legal_headers,
    inst_beta_headers,
):
    anchor = (await _anchor(client, passport.asset["id"], issuer_headers)).json()
    asset_id = passport.asset["id"]
    missing = await client.get(f"/assets/00000000-0000-0000-0000-000000000000/stellar-anchors",
                               headers=other_issuer_headers)
    assert missing.status_code == 404
    for method, path in (("POST", f"/assets/{asset_id}/stellar-anchors"),
                         ("GET", f"/assets/{asset_id}/stellar-anchors"),
                         ("POST", f"/assets/{asset_id}/stellar-anchors/{anchor['id']}/refresh"),
                         ("GET", f"/assets/{asset_id}/stellar-anchors/verification")):
        for headers in (other_issuer_headers, other_legal_headers):
            r = await client.request(method, path, headers=headers)
            if r.status_code != 403:  # the legal reviewer lacks anchor:create (403 before the scope)
                assert r.status_code == 404 and r.json() == missing.json(), (method, path, r.text)
    r = await client.get(_passport_url(passport.asset) + "/stellar-anchor", headers=inst_beta_headers)
    unknown = await client.get(_passport_url({**passport.asset, "stellar_asset_code": "NOPE"}) + "/stellar-anchor",
                               headers=inst_beta_headers)
    assert r.status_code == unknown.status_code == 404 and r.json() == unknown.json()
    assert len(horizon.submissions) == 1


async def test_only_issuer_reviewers_and_operators_can_anchor(
    client, passport, anchors, horizon, legal_headers, admin_headers, issuer_headers,
):
    assert (await _anchor(client, passport.asset["id"], legal_headers)).status_code == 403
    assert (await _anchor(client, passport.asset["id"], admin_headers)).status_code == 201
    internal = await client.get(f"/assets/{passport.asset['id']}/stellar-anchors/verification", headers=legal_headers)
    assert internal.status_code == 200 and internal.json()["result"] == "verified"


# ── 11. Existing approval and verification protections ───────────────────────


async def test_anchoring_changes_no_approval_evidence_or_passport(
    client, passport, anchors, issuer_headers, inst_headers, async_db: AsyncSession,
):
    before = await _snapshot(async_db)
    passport_before = (await client.get(_passport_url(passport.asset), headers=inst_headers)).json()
    assert (await _anchor(client, passport.asset["id"], issuer_headers)).status_code == 201
    assert await _snapshot(async_db) == before
    passport_after = (await client.get(_passport_url(passport.asset), headers=inst_headers)).json()
    passport_before.pop("retrieved_at"), passport_after.pop("retrieved_at")
    assert passport_after == passport_before


async def test_nothing_is_anchored_without_a_current_approved_passport(
    client, admin_client, anchors, horizon, issuer_headers, source_pdf, inst_headers,
):
    created = (await admin_client.post("/assets", json=TESTNET_ASSET)).json()
    document = await _upload(client, created["id"], source_pdf, issuer_headers)
    await _extract(client, document["id"], issuer_headers)
    r = await _anchor(client, created["id"], issuer_headers)
    assert r.status_code == 409 and r.json()["anchor_refusal"] == "no_current_passport"
    result = await _verify(client, created, inst_headers)
    assert (result["result"], result["current_passport_available"]) == ("not_anchored", False)
    assert horizon.submissions == []


async def test_a_legal_reviewer_approval_is_committed_only_when_counted(
    client, admin_client, anchors, issuer_headers, legal_headers, source_pdf, async_db: AsyncSession, storage,
):
    """The manifest commits the passport's counted approvals: an issuer-only passport commits no legal approval."""
    issuer_only = await _passport_asset(client, admin_client, issuer_headers, legal_headers, source_pdf,
                                        approvers=("issuer",))
    manifest = await build_manifest(async_db, await _asset_row(async_db, issuer_only.asset["id"]), storage)
    assert [a["approval_type"] for a in manifest["documents"][0]["approvals"]] == ["issuer_attestation"]
    assert manifest["verification"]["legal_reviewer_approval_recorded"] is False
    assert manifest["verification"]["verification_level"] == "issuer_attested"


# ── 12. Secrets are never exposed ────────────────────────────────────────────


async def test_the_signing_secret_never_appears_in_responses_logs_or_records(
    client, passport, anchors, anchor_account, issuer_headers, inst_headers, async_db: AsyncSession, caplog,
):
    caplog.set_level(logging.DEBUG)
    responses = [await _anchor(client, passport.asset["id"], issuer_headers),
                 await client.get(f"/assets/{passport.asset['id']}/stellar-anchors", headers=issuer_headers),
                 await client.get(_passport_url(passport.asset) + "/stellar-anchor", headers=inst_headers)]
    for r in responses:
        assert anchor_account.secret not in r.text
        assert "envelope_xdr" not in r.text  # the signed envelope stays internal
    assert all(anchor_account.secret not in record.getMessage() for record in caplog.records)
    rows = (await async_db.execute(text("SELECT * FROM stellar_anchors"))).all()
    assert all(anchor_account.secret not in str(row) for row in rows)
    assert repr(anchors.signer()).count(anchor_account.secret) == 0


# ── Schema: the anchor table is additive ─────────────────────────────────────


async def test_startup_adds_only_the_empty_anchor_table_to_an_up_to_date_database(tmp_path: Path):
    """A database of the previous schema gets the new table at startup; nothing else changes."""
    import sqlite3

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.base import Base
    from app.db.schema_check import verify_schema_columns

    db = tmp_path / "previous.db"
    previous = [t for name, t in Base.metadata.tables.items() if name != "stellar_anchors"]
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=previous))
            await conn.execute(text(
                "INSERT INTO assets (id, blockchain_network, stellar_asset_code, stellar_issuer_public_key, issuer_id, "
                "issuer_organization_name, asset_type, jurisdiction, status, created_at, updated_at, material_revision) "
                "VALUES ('a1', 'stellar-testnet', 'MRPT', :k, 'meridian-property', 'Org', 'real_estate', 'US-NY', "
                "'active', '2026-10-01 00:00:00', '2026-10-01 00:00:00', 1)"), {"k": TESTNET_ASSET["stellar_issuer_public_key"]})
        con = sqlite3.connect(db)
        before_ddl = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())
        before_rows = con.execute("SELECT * FROM assets").fetchall()
        con.close()
        async with engine.begin() as conn:  # the application's startup sequence (app.main.lifespan)
            await conn.run_sync(verify_schema_columns)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    con = sqlite3.connect(db)
    after_ddl = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall())
    assert set(after_ddl) - set(before_ddl) == {"stellar_anchors"}
    assert {k: v for k, v in after_ddl.items() if k != "stellar_anchors"} == before_ddl
    assert con.execute("SELECT * FROM assets").fetchall() == before_rows
    assert con.execute("SELECT COUNT(*) FROM stellar_anchors").fetchone()[0] == 0
    con.close()


async def test_a_recorded_anchor_whose_network_transaction_is_unsuccessful_is_never_verified(
    client, passport, anchors, horizon, issuer_headers, inst_headers,
):
    """The record says confirmed; the network now reports the transaction as unsuccessful."""
    tx_hash = (await _anchor(client, passport.asset["id"], issuer_headers)).json()["transaction_hash"]
    horizon.transactions[tx_hash]["successful"] = False
    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"], result["onchain"]["successful"]) == ("failed", False, False)


async def test_a_verification_only_server_verifies_but_never_signs(
    client, passport, anchors, horizon, anchor_account, tmp_path, issuer_headers, inst_headers, async_db,
):
    """A public key without a seed: anchors of that account verify; no transaction can be signed."""
    assert (await _anchor(client, passport.asset["id"], issuer_headers)).status_code == 201
    public_only = tmp_path / "anchor_public.json"
    public_only.write_text(json.dumps({"network": "stellar-testnet", "public_key": anchor_account.public_key}))
    app.dependency_overrides[get_anchor_service] = lambda: _service(str(public_only), horizon)

    result = await _verify(client, passport.asset, inst_headers)
    assert (result["result"], result["verified"]) == ("verified", True)

    other = await _upload(client, passport.asset["id"], make_source_pdf("Opinion."), issuer_headers, title="Opinion")
    await _approve(client, (await _extract(client, other["id"], issuer_headers))["id"], issuer_headers)
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 503 and r.json()["anchor_refusal"] == "anchoring_not_configured"
    assert "verification only" in r.json()["detail"]
    assert len(horizon.submissions) == 1
    assert (await async_db.execute(text("SELECT COUNT(*) FROM stellar_anchors"))).scalar_one() == 1


@pytest.mark.parametrize("content", [{"network": "stellar-testnet"}, {"network": "stellar-testnet", "public_key": "GBAD"}])
async def test_a_file_without_a_valid_public_key_trusts_no_account(
    client, passport, horizon, tmp_path, issuer_headers, inst_headers, content,
):
    file = tmp_path / "anchor.json"
    file.write_text(json.dumps(content))
    app.dependency_overrides[get_anchor_service] = lambda: _service(str(file), horizon)
    assert (await _anchor(client, passport.asset["id"], issuer_headers)).status_code == 503
    assert (await _verify(client, passport.asset, inst_headers))["result"] == "not_anchored"


def test_the_interface_shows_the_backend_anchor_wording_and_never_handles_signing_secrets():
    from app.schemas.stellar_anchor import ANCHOR_DISCLAIMER, VERIFICATION_MEANING

    root = Path(__file__).resolve().parent.parent / "frontend" / "src"
    handlers = (root / "test" / "mocks" / "handlers.ts").read_text(encoding="utf-8")
    assert f"'{ANCHOR_DISCLAIMER}'" in handlers, "frontend mock differs from the backend anchor disclaimer"
    assert f'"{VERIFICATION_MEANING}"' in handlers, "frontend mock differs from the backend verification meaning"
    for path in root.rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or "test" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert ANCHOR_DISCLAIMER[:40] not in source, path  # the interface shows the text it receives
        for secret_field in ("secret_seed", "envelope_xdr", "STELLAR_ANCHOR_SECRET"):
            assert secret_field not in source, (path, secret_field)
