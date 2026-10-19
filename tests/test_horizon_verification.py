"""
Stage 10C-3B, T5 — Horizon reads of the evidence verification (app/services/stellar_anchor.py,
StellarAnchorService.verification_lookup; app/anchoring.py, _check_network; STELLAR_ANCHORING.md).

Every verification remains a live read of the network, never a cached result: the product
promises a live check (API descriptions, the "Live network checks" of the interface).

Coverage:
 1. One verification: the network check, the transaction and its operations through one connection;
    an unsuccessful transaction is decided without its operations
 2. Repeated verifications are each a live read: a change on the network shows at once
 3. Concurrent identical verifications share one read while Horizon answers it (and only then);
    internally marked as shared; different transactions, servers or networks never share
 4. Per-request checks still run on a shared read: tenant, anchor record, current evidence
 5. Pending, unsuccessful, mismatched, wrong-network, reset, unreachable, slow (bounded) and
    malformed answers: never verified, and a malformed answer is never taken for "failed"
    (a pending anchor stays pending, so it is never submitted twice)
 6. Cancellation and errors of a shared read; the number of shared reads is bounded and returns
    to zero
 7. No transaction is submitted by a verification; the response contract is unchanged; the
    development database and the real anchoring account file are never touched
"""

import asyncio
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient
from stellar_sdk import Keypair, Network

from app.config import settings
from app.main import app
from app.services import stellar_anchor
from app.services.stellar_anchor import StellarAnchorService, VerificationLookup, get_anchor_service, in_flight_lookups
from tests.conftest import (
    _HARBOR_ASSET,
    INST_KEY_ALPHA,
    INST_KEY_BETA,
    ISSUER_TOKEN,
    LEGAL_TOKEN,
    OTHER_ISSUER_TOKEN,
    OTHER_LEGAL_TOKEN,
    bearer,
)
from tests import network_guard
from tests.test_approval_evidence_gate import _new_version, _passport_url
from tests.test_sqlite_hardening import development_database_untouched  # noqa: F401 (module fixture)
from tests.test_stellar_anchoring import (  # noqa: F401 (fixtures)
    TESTNET_ASSET,
    _anchor,
    _passport_asset,
    anchor_account,
    anchors,
    horizon,
    passport,
)

_REAL_DISPATCH = httpx.AsyncHTTPTransport.handle_async_request          # before no_real_network replaces it

HARBOR_TESTNET_ASSET = {**_HARBOR_ASSET, "blockchain_network": "stellar-testnet", "stellar_asset_code": "HBPT",
                        "stellar_issuer_public_key": Keypair.random().public_key}
INST = {"X-API-Key": INST_KEY_ALPHA}
MALFORMED = "Horizon answered with a malformed response."


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Any request through a real transport fails at once: only the fake Horizon is ever asked."""
    async def refuse(self, request):
        raise AssertionError(f"tests must not reach the network: {request.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


@pytest.fixture(scope="module", autouse=True)
def real_anchoring_account_untouched():
    """Metadata only: the configured (real) anchoring account file is never read or written here."""
    path = Path(settings.stellar_anchor_secret_file)
    state = lambda: (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None  # noqa: E731
    before = state()
    yield
    assert state() == before


class CountingHorizon:
    """FakeHorizon behind a counter, an optional gate (Horizon holds its answer) and overrides."""

    def __init__(self, horizon, *, gated: bool = False, override=None, hang: bool = False) -> None:
        self.horizon, self.override, self.hang = horizon, override, hang
        self.requests: Counter = Counter()
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        parts = [p for p in request.url.path.split("/") if p]
        self.requests[f"{request.method} /" + "/".join("{hash}" if len(p) == 64 else p for p in parts)] += 1
        if self.hang:
            await asyncio.sleep(3600)
        await self.gate.wait()
        if self.override is not None and (response := self.override(request)) is not None:
            return response
        return self.horizon.handle(request)

    @property
    def total(self) -> int:
        return sum(self.requests.values())

    async def waiting(self, count: int) -> None:
        """Until `count` requests have reached the (gated) server."""
        for _ in range(500):
            if self.total >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"only {self.total} requests reached Horizon")


def use(counting: CountingHorizon, anchor_account, *, timeout: float = 30.0) -> StellarAnchorService:
    service = StellarAnchorService(anchor_account.file, base_fee=1000, tx_timeout_seconds=300,
                                   transport=counting.transport, timeout=timeout)
    service.clients = 0
    open_client = service._client

    def counted() -> httpx.AsyncClient:
        service.clients += 1
        return open_client()

    service._client = counted
    app.dependency_overrides[get_anchor_service] = lambda: service
    return service


async def verified_passport(client, passport, issuer_headers) -> dict:
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.status_code == 201, r.text
    return r.json()


async def verify(client: AsyncClient, asset: dict, headers: dict = INST) -> dict:
    r = await client.get(_passport_url(asset) + "/stellar-anchor", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


READ = {"GET /": 1, "GET /transactions/{hash}": 1, "GET /transactions/{hash}/operations": 1}


# ── 1. One verification ───────────────────────────────────────────────────────


async def test_one_verification_reads_the_network_once_through_one_connection(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon)
    service = use(counting, anchor_account)
    body = await verify(client, passport.asset)
    assert (body["result"], body["verified"]) == ("verified", True)
    assert dict(counting.requests) == READ and service.clients == 1
    assert body["onchain"]["network_passphrase_matches"] is True and body["onchain"]["operation_matches"] is True


async def test_an_unsuccessful_transaction_is_decided_without_its_operations(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    anchor = await verified_passport(client, passport, issuer_headers)
    horizon.transactions[anchor["transaction_hash"]]["successful"] = False
    counting = CountingHorizon(horizon)
    use(counting, anchor_account)
    assert (await verify(client, passport.asset))["result"] == "failed"
    assert dict(counting.requests) == {"GET /": 1, "GET /transactions/{hash}": 1}


# ── 2. Every verification is a live read ──────────────────────────────────────


async def test_repeated_verifications_are_each_a_live_read(client, passport, anchors, horizon, anchor_account, issuer_headers):
    anchor = await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon)
    service = use(counting, anchor_account)
    for _ in range(5):
        assert (await verify(client, passport.asset))["result"] == "verified"
    assert counting.total == 15 and service.clients == 5
    # A change on the network shows in the very next verification: nothing was kept
    horizon.operations[anchor["transaction_hash"]][0]["value"] = "AAAA"
    assert (await verify(client, passport.asset))["result"] == "onchain_mismatch"
    horizon.reset()                                            # a Testnet reset
    after = await verify(client, passport.asset)
    assert (after["result"], after["verified"], after["onchain"]["transaction_found"]) == \
        ("transaction_unavailable", False, False)
    assert in_flight_lookups() == 0


# ── 3. Concurrent identical verifications ────────────────────────────────────


async def test_concurrent_identical_verifications_share_one_read(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon, gated=True)
    service = use(counting, anchor_account)
    internal = f"/assets/{passport.asset['id']}/stellar-anchors/verification"
    requests = [asyncio.ensure_future(client.get(_passport_url(passport.asset) + "/stellar-anchor", headers=INST))
                for _ in range(6)]
    requests += [asyncio.ensure_future(client.get(internal, headers=bearer(ISSUER_TOKEN))) for _ in range(4)]
    await counting.waiting(1)
    for _ in range(20):                                        # let every request reach the shared read
        await asyncio.sleep(0.01)
    assert in_flight_lookups() == 1
    counting.gate.set()
    responses = await asyncio.gather(*requests)
    assert all(r.status_code == 200 and r.json()["result"] == "verified" for r in responses)
    assert dict(counting.requests) == READ and service.clients == 1
    assert in_flight_lookups() == 0


async def test_a_shared_read_is_marked_and_a_finished_one_is_never_reused(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    anchor = await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon, gated=True)
    service = use(counting, anchor_account)
    first = asyncio.ensure_future(service.verification_lookup(anchor["transaction_hash"]))
    await counting.waiting(1)
    second = asyncio.ensure_future(service.verification_lookup(anchor["transaction_hash"]))
    await asyncio.sleep(0.05)
    counting.gate.set()
    own, joined = await first, await second
    assert (own.shared, joined.shared) == (False, True) and own.transaction == joined.transaction
    assert isinstance(own, VerificationLookup) and counting.total == 3
    later = await service.verification_lookup(anchor["transaction_hash"])    # after completion: a new read
    assert later.shared is False and counting.total == 6


async def test_different_transactions_servers_and_networks_never_share(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    anchor = await verified_passport(client, passport, issuer_headers)
    tx = anchor["transaction_hash"]
    testnet = CountingHorizon(horizon, gated=True)
    from tests.fake_horizon import FakeHorizon
    public = FakeHorizon(passphrase=Network.PUBLIC_NETWORK_PASSPHRASE)
    other = CountingHorizon(public, gated=True)
    a, b = use(testnet, anchor_account), use(other, anchor_account)          # the same URL, another network
    assert a.horizon_url == b.horizon_url
    lookups = [asyncio.ensure_future(a.verification_lookup(tx)), asyncio.ensure_future(b.verification_lookup(tx)),
               asyncio.ensure_future(a.verification_lookup("0" * 64))]
    await testnet.waiting(2)
    await other.waiting(1)
    assert in_flight_lookups() == 3
    testnet.gate.set()
    other.gate.set()
    on_testnet, on_public, unknown = await asyncio.gather(*lookups)
    assert (on_testnet.network_matches, on_testnet.transaction.found) == (True, True)
    assert (on_public.network_matches, on_public.transaction) == (False, None)
    assert (unknown.network_matches, unknown.transaction.found) == (True, False)
    assert not any(lookup.shared for lookup in (on_testnet, on_public, unknown))


# ── 4. Per-request checks on a shared read ────────────────────────────────────


async def test_tenants_are_verified_concurrently_without_sharing(
    client, admin_client, passport, anchors, horizon, anchor_account, issuer_headers, source_pdf,
):
    await verified_passport(client, passport, issuer_headers)
    harbor = await _passport_asset(client, admin_client, bearer(OTHER_ISSUER_TOKEN), bearer(OTHER_LEGAL_TOKEN),
                                   source_pdf, asset=HARBOR_TESTNET_ASSET)
    assert (await _anchor(client, harbor.asset["id"], bearer(OTHER_ISSUER_TOKEN))).status_code == 201
    counting = CountingHorizon(horizon, gated=True)
    use(counting, anchor_account)
    meridian_view = asyncio.ensure_future(verify(client, passport.asset))
    harbor_view = asyncio.ensure_future(verify(client, harbor.asset, {"X-API-Key": INST_KEY_BETA}))
    await counting.waiting(2)
    counting.gate.set()
    mine, theirs = await meridian_view, await harbor_view
    assert mine["result"] == theirs["result"] == "verified" and counting.total == 6
    assert mine["anchor"]["transaction_hash"] != theirs["anchor"]["transaction_hash"]
    assert mine["asset"]["asset_code"] == "MRPT" and theirs["asset"]["asset_code"] == "HBPT"
    # Out of scope stays out of scope
    r = await client.get(_passport_url(harbor.asset) + "/stellar-anchor", headers=INST)
    assert r.status_code == 404


async def test_changed_evidence_fails_even_on_a_shared_read(
    client, passport, anchors, horizon, anchor_account, issuer_headers, source_pdf,
):
    await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon, gated=True)
    use(counting, anchor_account)
    before_change = asyncio.ensure_future(verify(client, passport.asset))
    await counting.waiting(1)                                  # its manifest is computed, its read waits
    await _new_version(client, passport.document["id"], source_pdf + b"%changed", issuer_headers)
    after_change = asyncio.ensure_future(verify(client, passport.asset))
    for _ in range(20):
        await asyncio.sleep(0.01)
    counting.gate.set()
    first, second = await before_change, await after_change
    assert counting.total == 3                                 # one read, shared
    assert first["result"] == "verified"
    assert (second["result"], second["verified"], second["evidence_matches_anchor"]) == ("evidence_changed", False, False)


# ── 5. Negative answers are never verified ───────────────────────────────────


async def test_a_pending_anchor_is_never_reported_as_confirmed(client, passport, anchors, horizon, anchor_account, issuer_headers):
    horizon.submit_mode = "timeout_lost"
    r = await _anchor(client, passport.asset["id"], issuer_headers)
    assert r.json()["status"] == "pending"
    counting = CountingHorizon(horizon, gated=True)
    use(counting, anchor_account)
    views = [asyncio.ensure_future(verify(client, passport.asset)) for _ in range(3)]
    await counting.waiting(1)
    counting.gate.set()
    assert [v["result"] for v in await asyncio.gather(*views)] == ["pending"] * 3


async def test_the_wrong_network_is_rejected_before_the_transaction_is_read(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    await verified_passport(client, passport, issuer_headers)
    horizon.passphrase = Network.PUBLIC_NETWORK_PASSPHRASE
    counting = CountingHorizon(horizon)
    use(counting, anchor_account)
    body = await verify(client, passport.asset)
    assert (body["result"], body["onchain"]["network_passphrase_matches"], body["onchain"]["error"]) == \
        ("network_unavailable", False, "network_passphrase_mismatch")
    assert dict(counting.requests) == {"GET /": 1}


@pytest.mark.parametrize("field, value", [("source_account", Keypair.random().public_key), ("memo", "AAAA")])
async def test_other_transaction_details_are_never_verified(
    client, passport, anchors, horizon, anchor_account, issuer_headers, field, value,
):
    anchor = await verified_passport(client, passport, issuer_headers)
    horizon.transactions[anchor["transaction_hash"]][field] = value
    use(CountingHorizon(horizon), anchor_account)
    assert (await verify(client, passport.asset))["result"] == "onchain_mismatch"


@pytest.mark.parametrize("path, status", [("GET /", 503), ("GET /", 429), ("GET /transactions/{hash}", 500),
                                          ("GET /transactions/{hash}/operations", 502)])
async def test_an_unexpected_status_is_unavailable_never_negative(
    client, passport, anchors, horizon, anchor_account, issuer_headers, path, status,
):
    await verified_passport(client, passport, issuer_headers)

    def failing(request: httpx.Request):
        parts = [p for p in request.url.path.split("/") if p]
        key = f"{request.method} /" + "/".join("{hash}" if len(p) == 64 else p for p in parts)
        return httpx.Response(status, json={"status": status}) if key == path else None

    use(CountingHorizon(horizon, override=failing), anchor_account)
    body = await verify(client, passport.asset)
    assert (body["result"], body["verified"]) == ("network_unavailable", False)
    assert body["onchain"]["error"] == f"Horizon HTTP error: {status}."


async def test_an_outage_is_unavailable_and_nothing_negative_is_kept(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon, gated=True)
    use(counting, anchor_account)
    views = [asyncio.ensure_future(verify(client, passport.asset)) for _ in range(4)]
    await counting.waiting(1)
    horizon.down = True                                        # the shared read fails
    counting.gate.set()
    assert {v["result"] for v in await asyncio.gather(*views)} == {"network_unavailable"}
    assert in_flight_lookups() == 0
    horizon.down = False                                       # the next verification reads again
    assert (await verify(client, passport.asset))["result"] == "verified"


async def test_a_slow_horizon_is_bounded(client, passport, anchors, horizon, anchor_account, issuer_headers):
    await verified_passport(client, passport, issuer_headers)
    use(CountingHorizon(horizon, hang=True), anchor_account, timeout=0.3)
    started = time.monotonic()
    body = await verify(client, passport.asset)
    assert time.monotonic() - started < 3
    assert (body["result"], body["onchain"]["error"]) == ("network_unavailable", "Horizon did not answer in time.")
    assert in_flight_lookups() == 0


def _malformed(path: str, content: bytes):
    def answer(request: httpx.Request):
        parts = [p for p in request.url.path.split("/") if p]
        key = f"{request.method} /" + "/".join("{hash}" if len(p) == 64 else p for p in parts)
        return httpx.Response(200, content=content, headers={"content-type": "application/json"}) if key == path else None
    return answer


def _tx_with(**changes):
    """The real transaction record of the anchor, with these fields changed."""
    def answer(horizon, tx_hash: str) -> bytes:
        import json
        return json.dumps({**horizon.transactions[tx_hash], **changes}).encode()
    return answer


MALFORMED_ANSWERS = {
    "root-not-json": ("GET /", b"<html>maintenance</html>"),
    "root-a-list": ("GET /", b"[]"),
    "root-without-passphrase": ("GET /", b"{}"),
    "tx-not-json": ("GET /transactions/{hash}", b"not json"),
    "tx-a-list": ("GET /transactions/{hash}", b"[1]"),
    "tx-without-successful": ("GET /transactions/{hash}", _tx_with(successful=None)),
    "tx-successful-a-string": ("GET /transactions/{hash}", _tx_with(successful="yes")),
    "tx-of-another-hash": ("GET /transactions/{hash}", _tx_with(hash="f" * 64)),
    "tx-ledger-a-string": ("GET /transactions/{hash}", _tx_with(ledger="12")),
    "tx-bad-time": ("GET /transactions/{hash}", _tx_with(created_at="yesterday")),
    "operations-not-json": ("GET /transactions/{hash}/operations", b"oops"),
    "operations-records-not-a-list": ("GET /transactions/{hash}/operations", b'{"_embedded": {"records": {}}}'),
    "operations-record-not-an-object": ("GET /transactions/{hash}/operations", b'{"_embedded": {"records": ["x"]}}'),
}


@pytest.mark.parametrize("case", MALFORMED_ANSWERS, ids=MALFORMED_ANSWERS.keys())
async def test_a_malformed_answer_is_unavailable_never_failed_or_verified(
    client, passport, anchors, horizon, anchor_account, issuer_headers, case,
):
    anchor = await verified_passport(client, passport, issuer_headers)
    path, content = MALFORMED_ANSWERS[case]
    if callable(content):
        content = content(horizon, anchor["transaction_hash"])
    use(CountingHorizon(horizon, override=_malformed(path, content)), anchor_account)
    body = await verify(client, passport.asset)
    assert (body["result"], body["verified"], body["onchain"]["error"]) == ("network_unavailable", False, MALFORMED)


async def test_a_malformed_answer_leaves_a_pending_anchor_pending_and_never_resubmits(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    horizon.submit_mode = "timeout_included"                    # included, but the answer was lost
    pending = (await _anchor(client, passport.asset["id"], issuer_headers)).json()
    assert pending["status"] == "pending" and len(horizon.submissions) == 1
    refresh = f"/assets/{passport.asset['id']}/stellar-anchors/{pending['id']}/refresh"
    malformed = _malformed("GET /transactions/{hash}", _tx_with(successful=None)(horizon, pending["transaction_hash"]))
    use(CountingHorizon(horizon, override=malformed), anchor_account)
    assert (await client.post(refresh, headers=issuer_headers)).json()["status"] == "pending"
    again = await _anchor(client, passport.asset["id"], issuer_headers)      # never a second transaction
    assert again.json()["status"] == "pending" and len(horizon.submissions) == 1
    use(CountingHorizon(horizon), anchor_account)
    assert (await client.post(refresh, headers=issuer_headers)).json()["status"] == "confirmed"
    assert (await verify(client, passport.asset))["result"] == "verified" and len(horizon.submissions) == 1


async def test_a_malformed_read_back_never_fails_a_confirmed_submission(
    client, passport, horizon, anchor_account, issuer_headers,
):
    """The submission itself is unchanged; only its read-back no longer mistakes a malformed answer."""
    def malformed_read_back(request: httpx.Request):
        if request.method == "GET" and request.url.path.startswith("/transactions/") \
                and not request.url.path.endswith("/operations"):
            return httpx.Response(200, json={"hash": request.url.path.split("/")[2], "successful": None})
        return None

    use(CountingHorizon(horizon, override=malformed_read_back), anchor_account)
    anchored = await _anchor(client, passport.asset["id"], issuer_headers)
    assert anchored.status_code == 201 and anchored.json()["status"] == "confirmed"
    assert len(horizon.submissions) == 1
    use(CountingHorizon(horizon), anchor_account)
    assert (await verify(client, passport.asset))["result"] == "verified"


# ── 6. Cancellation, errors and the bound ────────────────────────────────────


async def test_a_cancelled_requester_never_breaks_the_others(client, passport, anchors, horizon, anchor_account, issuer_headers):
    await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon, gated=True)
    use(counting, anchor_account)
    views = [asyncio.ensure_future(verify(client, passport.asset)) for _ in range(3)]
    await counting.waiting(1)
    for _ in range(20):
        await asyncio.sleep(0.01)
    views[0].cancel()                                          # e.g. the first visitor closed the page
    counting.gate.set()
    rest = await asyncio.gather(*views[1:])
    assert [v["result"] for v in rest] == ["verified", "verified"] and counting.total == 3
    with pytest.raises(asyncio.CancelledError):
        await views[0]
    assert in_flight_lookups() == 0


async def test_the_number_of_shared_reads_is_bounded(client, passport, anchors, horizon, anchor_account, issuer_headers, monkeypatch):
    await verified_passport(client, passport, issuer_headers)
    monkeypatch.setattr(stellar_anchor, "MAX_SHARED_LOOKUPS", 4)
    counting = CountingHorizon(horizon, gated=True)
    service = use(counting, anchor_account)
    lookups = [asyncio.ensure_future(service.verification_lookup(f"{n:064x}")) for n in range(10)]
    await counting.waiting(10)                                 # every lookup is waiting for Horizon
    assert in_flight_lookups() == 4                            # the rest run on their own
    counting.gate.set()
    results = await asyncio.gather(*lookups)
    assert all(r.network_matches and not r.transaction.found for r in results)
    assert in_flight_lookups() == 0


async def test_a_removed_fake_transport_never_reaches_the_network(
    client, passport, anchors, horizon, anchor_account, issuer_headers, monkeypatch,
):
    """
    Stage 10C-3B, T5-S: the mutation of the T5 mutation test (the service's transport set to None),
    stopped twice: by this module's httpx guard, and, without it, by the process guard before DNS.
    Inside tripwired(), nothing a guard let through could reach the operating system.
    """
    anchor = await verified_passport(client, passport, issuer_headers)
    counting = CountingHorizon(horizon)
    service = use(counting, anchor_account)
    service._transport = None                                  # httpx then builds its real AsyncHTTPTransport
    with network_guard.tripwired() as reached, network_guard.deliberate_attempts() as seen:
        with pytest.raises(AssertionError, match="tests must not reach the network: horizon-testnet.stellar.org"):
            await service.verification_lookup(anchor["transaction_hash"])
        assert seen == []                                      # stopped at dispatch: no socket was even tried
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _REAL_DISPATCH)
        with pytest.raises(stellar_anchor.AnchorNetworkError, match="ConnectError"):
            await service.verification_lookup(anchor["transaction_hash"])
    assert seen == ["dns horizon-testnet.stellar.org"] and reached == []
    assert counting.total == 0 and in_flight_lookups() == 0


# ── 7. Nothing submitted, contract unchanged ──────────────────────────────────


async def test_verification_submits_nothing_and_keeps_its_contract(
    client, passport, anchors, horizon, anchor_account, issuer_headers,
):
    await verified_passport(client, passport, issuer_headers)
    submissions = list(horizon.submissions)
    counting = CountingHorizon(horizon)
    use(counting, anchor_account)
    body = await verify(client, passport.asset)
    for _ in range(3):
        await client.get(f"/assets/{passport.asset['id']}/stellar-anchors/verification", headers=bearer(LEGAL_TOKEN))
    assert horizon.submissions == submissions
    assert not any(key.startswith("POST") for key in counting.requests)
    assert set(body) == {"asset", "manifest_schema", "manifest_version", "current_passport_available",
                         "current_manifest_digest", "anchor", "evidence_matches_anchor", "onchain", "result",
                         "verified", "committed_manifest_canonical_json", "checked_at", "meaning", "disclaimer"}
    assert set(body["onchain"]) == {"checked", "network_passphrase_matches", "transaction_found", "successful",
                                    "source_account_matches", "memo_matches", "operation_matches", "ledger",
                                    "ledger_closed_at", "error"}
