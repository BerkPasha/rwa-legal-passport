"""
In-memory stand-in for the Stellar Testnet Horizon API, served through httpx.MockTransport.

No network is used. Submitted envelopes are decoded with the real stellar-sdk, their signature is
checked against the source account and the sequence number is enforced, so the anchoring code's
transaction building and signing are exercised for real; only the ledger is simulated.

Submission modes:
- "include"           → included, 200
- "include_failed"    → included but unsuccessful (op failure), 400 with result codes
- "reject"            → rejected before inclusion, 400 tx_insufficient_fee
- "timeout_included"  → included, but Horizon answers 504 (the response is lost)
- "timeout_lost"      → not included, Horizon answers 504
- "connect_error"     → the POST never reaches Horizon
"""

import base64
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
from stellar_sdk import Keypair, Network, TransactionEnvelope
from stellar_sdk.memo import HashMemo
from stellar_sdk.operation import ManageData


class FakeHorizon:
    def __init__(self, passphrase: str = Network.TESTNET_NETWORK_PASSPHRASE) -> None:
        self.passphrase = passphrase
        self.accounts: dict[str, int] = {}
        self.transactions: dict[str, dict] = {}
        self.operations: dict[str, list[dict]] = {}
        self.submissions: list[str] = []
        self.submit_mode = "include"
        self.down = False
        self.ledger = 5_000_000
        self.transport = httpx.MockTransport(self.handle)

    def fund(self, account_id: str, sequence: int = 4_000_000_000) -> None:
        self.accounts[account_id] = sequence

    def reset(self) -> None:
        """A Testnet reset: every account and transaction disappears."""
        self.accounts.clear()
        self.transactions.clear()
        self.operations.clear()

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Horizon unreachable", request=request)
        parts = [p for p in request.url.path.split("/") if p]
        if request.method == "GET" and not parts:
            return httpx.Response(200, json={"network_passphrase": self.passphrase})
        if request.method == "GET" and parts[0] == "accounts" and len(parts) == 2:
            if parts[1] not in self.accounts:
                return httpx.Response(404, json={"status": 404, "title": "Resource Missing"})
            return httpx.Response(200, json={"id": parts[1], "sequence": str(self.accounts[parts[1]])})
        if request.method == "POST" and parts == ["transactions"]:
            if self.submit_mode == "connect_error":
                raise httpx.ConnectError("connection reset", request=request)
            return self._submit(parse_qs(request.content.decode())["tx"][0])
        if request.method == "GET" and parts[0] == "transactions" and len(parts) in (2, 3):
            tx_hash = parts[1]
            if tx_hash not in self.transactions:
                return httpx.Response(404, json={"status": 404, "title": "Resource Missing"})
            if len(parts) == 3 and parts[2] == "operations":
                return httpx.Response(200, json={"_embedded": {"records": self.operations[tx_hash]}})
            return httpx.Response(200, json=self.transactions[tx_hash])
        return httpx.Response(404, json={"status": 404})

    # ── Ledger simulation ────────────────────────────────────────────────────

    def _include(self, envelope: TransactionEnvelope, successful: bool = True) -> str:
        tx = envelope.transaction
        tx_hash = envelope.hash_hex()
        self.ledger += 1
        memo = tx.memo
        self.transactions[tx_hash] = {
            "hash": tx_hash,
            "successful": successful,
            "source_account": tx.source.account_id,
            "memo_type": "hash" if isinstance(memo, HashMemo) else "none",
            "memo": base64.b64encode(memo.memo_hash).decode() if isinstance(memo, HashMemo) else None,
            "ledger": self.ledger,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.operations[tx_hash] = [
            {"type": "manage_data", "name": op.data_name,
             "value": base64.b64encode(op.data_value).decode() if op.data_value is not None else None}
            if isinstance(op, ManageData) else {"type": type(op).__name__}
            for op in tx.operations
        ]
        self.accounts[tx.source.account_id] = tx.sequence
        return tx_hash

    def _submit(self, envelope_xdr: str) -> httpx.Response:
        envelope = TransactionEnvelope.from_xdr(envelope_xdr, self.passphrase)
        tx = envelope.transaction
        source = tx.source.account_id
        self.submissions.append(envelope.hash_hex())
        try:  # signed by the source account, for exactly this network passphrase
            Keypair.from_public_key(source).verify(envelope.hash(), envelope.signatures[0].signature)
        except Exception:
            return self._rejected("tx_bad_auth")
        if source not in self.accounts:
            return self._rejected("tx_no_source_account")
        if tx.sequence != self.accounts[source] + 1:
            return self._rejected("tx_bad_seq")
        mode = self.submit_mode
        if mode == "reject":
            return self._rejected("tx_insufficient_fee")
        if mode == "timeout_lost":
            return httpx.Response(504, json={"status": 504, "title": "Timeout"})
        if mode == "include_failed":
            self._include(envelope, successful=False)
            return self._rejected("tx_failed", ["op_low_reserve"])
        tx_hash = self._include(envelope)
        if mode == "timeout_included":
            return httpx.Response(504, json={"status": 504, "title": "Timeout"})
        return httpx.Response(200, json={"hash": tx_hash, "ledger": self.ledger, "successful": True})

    @staticmethod
    def _rejected(code: str, operations: list[str] | None = None) -> httpx.Response:
        return httpx.Response(400, json={
            "status": 400, "title": "Transaction Failed",
            "extras": {"result_codes": {"transaction": code, "operations": operations or []}},
        })
