import re

from stellar_sdk import StrKey

# Stellar public key: starts with G, exactly 56 chars, RFC 4648 base32 (A-Z and 2-7 only)
_PUBKEY_RE = re.compile(r"^G[A-Z2-7]{55}$")

# Stellar asset code: 1-12 uppercase alphanumeric chars
_ASSET_CODE_RE = re.compile(r"^[A-Z0-9]{1,12}$")


def is_valid_stellar_public_key(key: str) -> bool:
    """
    Strict check of a Stellar account ID (Ed25519 public key, StrKey "G..." form):
      - Exactly 56 characters, first character 'G', RFC 4648 base32 (A-Z and 2-7)
      - The StrKey version byte and CRC16 checksum are valid (official stellar-sdk decoder)
    A valid key is only a well-formed identifier: it proves neither that the account exists nor
    who controls it.
    """
    if not isinstance(key, str) or len(key) != 56 or not _PUBKEY_RE.match(key):
        return False
    return StrKey.is_valid_ed25519_public_key(key)


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


def stellar_asset_type(code: str) -> str:
    """The Stellar asset type of a valid non-native asset code (by its length)."""
    return "credit_alphanum4" if len(code) <= 4 else "credit_alphanum12"
