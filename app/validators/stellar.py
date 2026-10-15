import re

# Stellar public key: starts with G, exactly 56 chars, RFC 4648 base32 (A-Z and 2-7 only)
_PUBKEY_RE = re.compile(r"^G[A-Z2-7]{55}$")

# Stellar asset code: 1-12 uppercase alphanumeric chars
_ASSET_CODE_RE = re.compile(r"^[A-Z0-9]{1,12}$")


def is_valid_stellar_public_key(key: str) -> bool:
    """
    Minimal format check for a Stellar account ID (Ed25519 public key).
    Rules:
      - Exactly 56 characters
      - First character must be 'G' (public key version byte in base32)
      - Remaining 55 characters must be valid RFC 4648 base32: A-Z and 2-7
    Does NOT verify the checksum — that requires the Stellar SDK (out of scope).
    """
    if not isinstance(key, str) or len(key) != 56:
        return False
    return bool(_PUBKEY_RE.match(key))


def is_valid_stellar_asset_code(code: str) -> bool:
    """
    Stellar non-native asset code rules:
      - 1 to 12 uppercase alphanumeric characters
      - Cannot be 'XLM' (the native asset; it has no issuer)
    """
    if not isinstance(code, str):
        return False
    if code == "XLM":
        return False
    return bool(_ASSET_CODE_RE.match(code))
