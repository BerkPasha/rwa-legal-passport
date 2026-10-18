"""
Create and fund the Stellar TESTNET anchoring account (STELLAR_ANCHORING.md).

    python scripts/stellar_testnet_setup.py [--file secrets/stellar_testnet_anchor.json]

- Generates a new random keypair ONLY if the file does not exist (an existing file is never
  overwritten) and writes {"network", "public_key", "secret_seed"} to it.
- Funds the account once through the official Testnet Friendbot if it does not exist yet
  (also after a Testnet reset).
- Prints the public key and the XLM balance. The secret seed is never printed or logged.
- Testnet only: the Horizon server's network passphrase is checked first.

Keep the file out of version control (secrets/ is gitignored).
"""

import argparse
import json
import sys
from pathlib import Path

import httpx
from stellar_sdk import Keypair, Network

HORIZON = "https://horizon-testnet.stellar.org"
FRIENDBOT = "https://friendbot.stellar.org"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--file", default="secrets/stellar_testnet_anchor.json")
    args = parser.parse_args()
    file = Path(args.file)

    root = httpx.get(HORIZON, timeout=30).json()
    if root.get("network_passphrase") != Network.TESTNET_NETWORK_PASSPHRASE:
        print("Refusing: the Horizon server is not on Stellar Testnet.")
        return 2

    if file.exists():
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("network") != "stellar-testnet":
            print("Refusing: the existing file is not a stellar-testnet account file.")
            return 2
        keypair = Keypair.from_secret(data["secret_seed"])
        if keypair.public_key != data.get("public_key"):
            print("Refusing: the file's public key does not match its seed.")
            return 2
        print(f"Using the existing account file {file}")
    else:
        keypair = Keypair.random()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"network": "stellar-testnet", "public_key": keypair.public_key,
                                    "secret_seed": keypair.secret}, indent=2) + "\n", encoding="utf-8")
        print(f"Created a new Testnet account file {file}")

    account = httpx.get(f"{HORIZON}/accounts/{keypair.public_key}", timeout=30)
    if account.status_code == 404:
        funded = httpx.get(FRIENDBOT, params={"addr": keypair.public_key}, timeout=60)
        if funded.status_code != 200:
            print(f"Friendbot funding failed (HTTP {funded.status_code}).")
            return 1
        print(f"Funded by Friendbot: transaction {funded.json().get('hash')}")
        account = httpx.get(f"{HORIZON}/accounts/{keypair.public_key}", timeout=30)
    account.raise_for_status()
    native = next(b["balance"] for b in account.json()["balances"] if b["asset_type"] == "native")
    print(f"Anchoring account: {keypair.public_key}")
    print(f"Balance: {native} XLM (Testnet)")
    print(f"Explorer: {HORIZON}/accounts/{keypair.public_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
