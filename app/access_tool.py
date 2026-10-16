"""
Access-configuration helper tool.

Usage (from the project root):
  python -m app.access_tool new-token            Generates a new token; prints the token and its SHA-256 digest
  python -m app.access_tool hash                 Prints the digest of a token read from stdin (not echoed)
  python -m app.access_tool hash-env NAME        Prints the digest of the NAME variable from .env / the environment
  python -m app.access_tool check [--file PATH]  Validates the access file and prints a summary

The token is shown only ONCE, in the new-token output; only the digest
(token_sha256 / key_sha256) is written to the access file.
"""

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values

from app.access_registry import AccessConfigError, describe_registry, hash_token, load_access_registry
from app.config import settings

_MIN_TOKEN_LENGTH = 32


def _cmd_new_token(_: argparse.Namespace) -> int:
    token = secrets.token_urlsafe(32)
    print("token (shown only once; deliver it over a secure channel):")
    print(f"  {token}")
    print("sha256 (write this into the access file):")
    print(f"  {hash_token(token)}")
    return 0


def _cmd_hash(_: argparse.Namespace) -> int:
    token = getpass.getpass("token: ") if sys.stdin.isatty() else sys.stdin.readline()
    token = token.strip()
    if len(token) < _MIN_TOKEN_LENGTH:
        print(f"error: the token must be at least {_MIN_TOKEN_LENGTH} characters long", file=sys.stderr)
        return 2
    print(hash_token(token))
    return 0


def _cmd_hash_env(args: argparse.Namespace) -> int:
    values = dotenv_values(args.env_file) if Path(args.env_file).exists() else {}
    token = (os.environ.get(args.name) or values.get(args.name) or "").strip()
    if not token:
        print(f"error: {args.name} is not defined or is empty", file=sys.stderr)
        return 2
    if len(token) < _MIN_TOKEN_LENGTH:
        print(f"error: {args.name} must be at least {_MIN_TOKEN_LENGTH} characters long", file=sys.stderr)
        return 2
    print(hash_token(token))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    try:
        registry = load_access_registry(path)
    except AccessConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"valid: {path} — {describe_registry(registry)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.access_tool", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("new-token", help="generate a new token and its digest").set_defaults(func=_cmd_new_token)
    sub.add_parser("hash", help="print the digest of a token read from stdin").set_defaults(func=_cmd_hash)
    p_env = sub.add_parser("hash-env", help="print the digest of the token in a .env variable")
    p_env.add_argument("name")
    p_env.add_argument("--env-file", default=".env")
    p_env.set_defaults(func=_cmd_hash_env)
    p_check = sub.add_parser("check", help="validate the access file")
    p_check.add_argument("--file", default=settings.access_control_file)
    p_check.set_defaults(func=_cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
