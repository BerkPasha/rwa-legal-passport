"""
Stellar TESTNET evidence anchoring client (STELLAR_ANCHORING.md).

Builds and signs one classic Stellar transaction per evidence manifest with the official
stellar-sdk, submits it through Horizon, and reads transactions back for verification.

The transaction, from the configured anchoring account:
- memo: MEMO_HASH = the 32-byte SHA-256 manifest digest;
- one operation: manage_data(name = "lp:<asset id>", value = the same 32 bytes);
- time bounds: valid for STELLAR_ANCHOR_TX_TIMEOUT_SECONDS after signing, so an unconfirmed
  transaction can be declared failed once it has expired (it can no longer be included).

Safety:
- Testnet only: the network passphrase is fixed to the Testnet passphrase, and the Horizon
  server's own passphrase is checked before every submission and verification.
- The signing secret is read from STELLAR_ANCHOR_SECRET_FILE on use; it is never logged,
  returned or stored elsewhere. Error messages never include it.
- A file with only "network" and "public_key" configures a verification-only server: it
  verifies anchors of that account and refuses to sign (e.g. a public demo).
- Nothing but the digest and the data entry name goes on-chain: no document content, no
  personal data.

Horizon is used rather than Stellar RPC for reading because RPC keeps only a bounded recent
history (about 7 days by default), while Horizon serves Testnet transactions since the last reset.
"""

import asyncio
import base64
import json
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from stellar_sdk import Account, Keypair, Network, StrKey, TransactionBuilder

from app.config import settings
from app.enums import AnchorStatus, BlockchainNetwork
from app.services.stellar import HORIZON_URLS

TESTNET = BlockchainNetwork.stellar_testnet
TESTNET_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE
DATA_NAME_PREFIX = "lp:"


class AnchorConfigError(Exception):
    """No usable Testnet signing account is configured (the message never contains the secret)."""


class AnchorNetworkError(Exception):
    """Horizon could not be reached or answered unexpectedly; nothing can be concluded."""


class WrongNetworkError(AnchorNetworkError):
    """The configured Horizon server is not on Stellar Testnet."""


class AnchorAccountNotFound(AnchorNetworkError):
    """The anchoring account does not exist on the network (unfunded, or Testnet was reset)."""


def data_name_for(asset_id: str) -> str:
    name = f"{DATA_NAME_PREFIX}{asset_id}"
    if len(name.encode("utf-8")) > 64:  # manage_data names are limited to 64 bytes
        raise ValueError("data entry name too long")
    return name


@dataclass(frozen=True)
class AnchorSigner:
    public_key: str
    keypair: Keypair = field(repr=False)


def _read_account_file(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        raise AnchorConfigError("Stellar anchoring is not configured: no Testnet anchoring account file.")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AnchorConfigError("The Stellar anchoring account file is unreadable.") from None
    if not isinstance(data, dict) or data.get("network") != TESTNET.value:
        raise AnchorConfigError("The Stellar anchoring account file must describe a stellar-testnet account.")
    return data


def load_anchor_signer(path: str) -> AnchorSigner:
    """Loads and checks the Testnet signing account file: {"network", "public_key", "secret_seed"}."""
    data = _read_account_file(path)
    if "secret_seed" not in data:
        raise AnchorConfigError(
            "This server is configured for verification only: the anchoring account file holds no "
            "secret seed, so no transaction can be signed."
        )
    try:
        keypair = Keypair.from_secret(data.get("secret_seed"))
    except Exception:  # never echo the value
        raise AnchorConfigError("The Stellar anchoring account file holds no valid secret seed.") from None
    if keypair.public_key != data.get("public_key"):
        raise AnchorConfigError("The public key of the Stellar anchoring account file does not match its seed.")
    return AnchorSigner(public_key=keypair.public_key, keypair=keypair)


def load_trusted_account(path: str) -> str:
    """
    The anchoring account that verification trusts. A file without "secret_seed" configures a
    verification-only server: it can verify anchors but never sign (e.g. a public demo).
    """
    data = _read_account_file(path)
    if "secret_seed" in data:
        return load_anchor_signer(path).public_key
    public_key = data.get("public_key")
    if not isinstance(public_key, str) or not StrKey.is_valid_ed25519_public_key(public_key):
        raise AnchorConfigError("The Stellar anchoring account file holds no valid public key.")
    return public_key


@dataclass
class SignedAnchor:
    transaction_hash: str
    envelope_xdr: str
    valid_until: datetime
    data_name: str
    anchor_account: str


@dataclass
class SubmissionOutcome:
    status: AnchorStatus
    ledger: Optional[int] = None
    failure_reason: Optional[str] = None


@dataclass
class OnchainTransaction:
    found: bool
    successful: Optional[bool] = None
    source_account: Optional[str] = None
    memo_type: Optional[str] = None
    memo: Optional[bytes] = None
    ledger: Optional[int] = None
    closed_at: Optional[datetime] = None
    operations: list[dict] = field(default_factory=list)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _result_codes(body: dict) -> str:
    codes = (body.get("extras") or {}).get("result_codes") or {}
    parts = [str(codes.get("transaction") or "transaction_rejected")]
    parts += [str(op) for op in codes.get("operations") or [] if op != "op_success"]
    return ":".join(parts)[:255]


# One lock per event loop serializes the account's sequence numbers within the process
_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


class StellarAnchorService:
    """Testnet-only anchoring through Horizon."""

    def __init__(
        self,
        signer_file: str,
        *,
        base_fee: int,
        tx_timeout_seconds: int,
        horizon_url: str = HORIZON_URLS[TESTNET],
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 30.0,
    ) -> None:
        self._signer_file = signer_file
        self._base_fee = base_fee
        self._tx_timeout = tx_timeout_seconds
        self.horizon_url = horizon_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    @property
    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = _LOCKS.get(loop)
        if lock is None:
            lock = _LOCKS[loop] = asyncio.Lock()
        return lock

    def signer(self) -> AnchorSigner:
        return load_anchor_signer(self._signer_file)

    def trusted_account(self) -> Optional[str]:
        """The configured anchoring account (also in verification-only mode), or None."""
        try:
            return load_trusted_account(self._signer_file)
        except AnchorConfigError:
            return None

    def transaction_url(self, transaction_hash: str) -> str:
        return f"{self.horizon_url}/transactions/{transaction_hash}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.horizon_url, transport=self._transport, timeout=self._timeout)

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> httpx.Response:
        try:
            return await client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise AnchorNetworkError(f"Horizon network error: {type(exc).__name__}.") from exc

    async def _check_network(self, client: httpx.AsyncClient) -> None:
        response = await self._get(client, "/")
        if response.status_code != 200:
            raise AnchorNetworkError(f"Horizon HTTP error: {response.status_code}.")
        if response.json().get("network_passphrase") != TESTNET_PASSPHRASE:
            raise WrongNetworkError("The configured Horizon server is not on Stellar Testnet.")

    async def network_matches(self) -> bool:
        """True if the configured Horizon server is on Testnet; raises AnchorNetworkError if unreachable."""
        async with self._client() as client:
            try:
                await self._check_network(client)
            except WrongNetworkError:
                return False
        return True

    async def sign(self, asset_id: str, digest_hex: str) -> SignedAnchor:
        """Builds and signs the anchoring transaction with the account's next sequence number."""
        signer = self.signer()
        digest = bytes.fromhex(digest_hex)
        if len(digest) != 32:
            raise ValueError("a manifest digest is 32 bytes")
        name = data_name_for(asset_id)
        async with self._client() as client:
            await self._check_network(client)
            response = await self._get(client, f"/accounts/{signer.public_key}")
            if response.status_code == 404:
                raise AnchorAccountNotFound(
                    "The anchoring account does not exist on Testnet (fund it with Friendbot; "
                    "Testnet may have been reset)."
                )
            if response.status_code != 200:
                raise AnchorNetworkError(f"Horizon HTTP error: {response.status_code}.")
            sequence = int(response.json()["sequence"])
        builder = TransactionBuilder(
            source_account=Account(signer.public_key, sequence),
            network_passphrase=TESTNET_PASSPHRASE,
            base_fee=self._base_fee,
        )
        builder.add_hash_memo(digest)
        builder.append_manage_data_op(data_name=name, data_value=digest)
        builder.set_timeout(self._tx_timeout)
        envelope = builder.build()
        envelope.sign(signer.keypair)
        max_time = envelope.transaction.preconditions.time_bounds.max_time
        return SignedAnchor(
            transaction_hash=envelope.hash_hex(),
            envelope_xdr=envelope.to_xdr(),
            valid_until=datetime.fromtimestamp(max_time, tz=timezone.utc),
            data_name=name,
            anchor_account=signer.public_key,
        )

    async def submit(self, envelope_xdr: str) -> SubmissionOutcome:
        """
        Submits a signed envelope. Only a definite answer changes the state: 200 → confirmed,
        400 → failed (the result codes are kept). A timeout or any other answer → pending: the
        transaction may still be included before its time bounds expire.
        """
        async with self._client() as client:
            try:
                response = await client.post("/transactions", data={"tx": envelope_xdr})
            except httpx.HTTPError:
                return SubmissionOutcome(AnchorStatus.pending, failure_reason="submission_outcome_unknown")
        if response.status_code == 200:
            body = response.json()
            if body.get("successful") is False:
                return SubmissionOutcome(AnchorStatus.failed, failure_reason="transaction_unsuccessful")
            return SubmissionOutcome(AnchorStatus.confirmed, ledger=body.get("ledger"))
        if response.status_code == 400:
            try:
                reason = _result_codes(response.json())
            except ValueError:
                reason = "transaction_rejected"
            return SubmissionOutcome(AnchorStatus.failed, failure_reason=reason)
        return SubmissionOutcome(AnchorStatus.pending, failure_reason=f"submission_http_{response.status_code}")

    async def get_transaction(self, transaction_hash: str) -> OnchainTransaction:
        """The transaction as the network reports it (found=False if Horizon does not know it)."""
        async with self._client() as client:
            response = await self._get(client, f"/transactions/{transaction_hash}")
            if response.status_code == 404:
                return OnchainTransaction(found=False)
            if response.status_code != 200:
                raise AnchorNetworkError(f"Horizon HTTP error: {response.status_code}.")
            tx = response.json()
            operations = await self._get(client, f"/transactions/{transaction_hash}/operations", limit=10)
            if operations.status_code != 200:
                raise AnchorNetworkError(f"Horizon HTTP error: {operations.status_code}.")
        memo = None
        if tx.get("memo_type") == "hash" and tx.get("memo"):
            try:
                memo = base64.b64decode(tx["memo"], validate=True)
            except ValueError:
                memo = None
        return OnchainTransaction(
            found=True,
            successful=bool(tx.get("successful")),
            source_account=tx.get("source_account"),
            memo_type=tx.get("memo_type"),
            memo=memo,
            ledger=tx.get("ledger"),
            closed_at=_parse_time(tx.get("created_at")),
            operations=operations.json().get("_embedded", {}).get("records", []),
        )


def get_anchor_service() -> StellarAnchorService:
    """FastAPI dependency — replaced in tests with a service talking to a fake Horizon."""
    return StellarAnchorService(
        settings.stellar_anchor_secret_file,
        base_fee=settings.stellar_anchor_base_fee,
        tx_timeout_seconds=settings.stellar_anchor_tx_timeout_seconds,
    )
