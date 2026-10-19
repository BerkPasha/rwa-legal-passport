"""
Builds the deployable bundle of the read-only grant showcase (Stage 10C-2, DEPLOYMENT.md).

    python scripts/build_showcase.py                       # synthetic data only
    python scripts/build_showcase.py --with-stellar-demo   # plus the genuine Stellar Testnet anchor of the
                                                           # local demo dataset (DEMO.md)
    python scripts/build_showcase.py --with-stellar-demo --demo-dataset scratch/stellar-demo/demo-v2
                                                           # the same, from the version 2 dataset
    python scripts/build_showcase.py --out <directory>     # default: scratch/showcase-bundle/<timestamp>

Layout of the result:

    <out>/deploy/                    what goes on the server (nothing in it is secret)
        data/showcase.db             SQLite database of synthetic records (+ the copied demo records)
        data/storage/                the synthetic PDFs, as uploaded
        data/access_control.json     showcase-only evaluator credentials and institutional keys (digests only)
        data/anchor_public.json      verification-only anchoring account (a public key; no seed exists)
        www/                         the production frontend build, talking to the API under /api
        showcase.env                 the backend settings (READ_ONLY_MODE=true, API docs off, rate limits)
        BUNDLE_MANIFEST.json         SHA-256 of every file above
    <out>/evaluator-credentials.txt  the fictional showcase credentials to hand to evaluators; never deployed
    <out>/rehearsal-context.json     identifiers and those credentials, for scripts/e2e_run.py's rehearsal

How it is made: the synthetic dataset is seeded through the real API (scripts/e2e_seed.py,
--public-showcase: no synthetic anchor records) with build-time credentials that are then discarded:
the bundle's registry holds only new showcase credentials of read-only roles (independent legal
reviewers) and institutional keys, so the operator and issuer credentials used to build it can do
nothing on the server. With --with-stellar-demo the synthetic data is added to a copy of the demo
dataset, whose genuine anchor stays verifiable: its evidence manifest covers only its own asset.

Checks before it finishes: the frontend build holds no credential and no signing material, the
anchoring account file holds no seed, the registry holds digests only, and the development
database and the demo dataset are unchanged.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_run  # noqa: E402  (paths, isolated environments and integrity helpers)

ROOT, FRONTEND = e2e_run.ROOT, e2e_run.FRONTEND
SHOWCASE_ISSUERS = {"e2e-meridian": "E2E Meridian Harbour Issuer (synthetic)", "e2e-harbor": "E2E Harbor Bonds Issuer (synthetic)"}
SHOWCASE_SETTINGS = {
    # A publicly reachable server: the production profile, whose startup checks these settings pass
    "APP_ENV": "production-showcase",
    "DATABASE_URL": "sqlite+aiosqlite:///./data/showcase.db",
    "STORAGE_PATH": "./data/storage",
    "ACCESS_CONTROL_FILE": "./data/access_control.json",
    "STELLAR_ANCHOR_SECRET_FILE": "./data/anchor_public.json",
    "AI_PROVIDER": "disabled",
    "ANTHROPIC_API_KEY": "",
    "READ_ONLY_MODE": "true",
    "API_DOCS_ENABLED": "false",
    "SYNTHETIC_DATA_NOTICE": "true",
    # Same origin through the reverse proxy (/api): no cross-origin browser access is needed
    "CORS_ALLOWED_ORIGINS": "",
    "CLIENT_RATE_LIMIT_PER_MINUTE": "300",
    "AUTH_FAILURE_LIMIT_PER_MINUTE": "10",
    # Nothing is uploaded to a read-only server: request bodies are capped at the 64 KiB envelope
    "MAX_UPLOAD_SIZE_MB": "0",
}
_FORBIDDEN_IN_WWW = (b"secret_seed", b"sk-ant-", b"BEGIN PRIVATE KEY", b"token_sha256")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry(issuers: dict[str, str], scopes: dict[str, list[str]]) -> tuple[dict, dict]:
    """The showcase registry (digests only) and its plaintext credentials (for the evaluators)."""
    sys.path.insert(0, str(ROOT))
    from app.access_registry import hash_token

    creds = {name: f"showcase-{name.replace('_', '-')}-{secrets.token_urlsafe(24)}" for name in
             ("evaluator_a", "evaluator_b", "institution_a", "institution_b")}
    config = {
        "issuers": [{"id": i, "name": n} for i, n in issuers.items()],
        "users": [
            {"user_id": "showcase-evaluator-a", "display_name": "Showcase Evaluator A",
             "role": "independent_legal_reviewer", "token_sha256": hash_token(creds["evaluator_a"]),
             "issuer_ids": scopes["a"]},
            {"user_id": "showcase-evaluator-b", "display_name": "Showcase Evaluator B",
             "role": "independent_legal_reviewer", "token_sha256": hash_token(creds["evaluator_b"]),
             "issuer_ids": scopes["b"]},
        ],
        "institutional_clients": [
            {"client_id": "showcase-institution-a", "organization": "Showcase Institution A",
             "key_sha256": hash_token(creds["institution_a"]), "issuer_ids": scopes["a"], "operations": ["passport:read"]},
            {"client_id": "showcase-institution-b", "organization": "Showcase Institution B",
             "key_sha256": hash_token(creds["institution_b"]), "issuer_ids": scopes["b"], "operations": ["passport:read"]},
        ],
    }
    return config, creds


def build(out: Path, with_stellar_demo: bool, demo_dataset: Path = e2e_run.DEMO_DATASET) -> dict:
    dev_before, demo_before = e2e_run._sha256_file(e2e_run.DEV_DATABASE), e2e_run._sha256_tree(e2e_run.DEMO_DATASET)
    source_before = e2e_run._sha256_tree(demo_dataset)
    deploy, data = out / "deploy", out / "deploy" / "data"
    data.mkdir(parents=True)
    issuers = dict(SHOWCASE_ISSUERS)
    demo: dict = {}

    # 1. The genuine Testnet anchor's dataset, copied (the original is never written)
    if with_stellar_demo:
        source = demo_dataset
        if not source.is_dir():
            raise SystemExit(f"REFUSED: the demo dataset {source} does not exist (DEMO.md)")
        shutil.copy2(source / "demo.db", data / "showcase.db")
        shutil.copytree(source / "storage", data / "storage")
        shutil.copy2(source / "anchor_public.json", data / "anchor_public.json")
        manifest = json.loads((source / "demo.json").read_text(encoding="utf-8"))
        demo = {"asset": manifest["asset"], "anchor": manifest["anchor"]}
        # closing(): sqlite3's own context manager commits but does not close, and an open file
        # cannot be deleted on Windows (the rehearsal removes its temporary directory)
        with closing(sqlite3.connect(f"file:{data / 'showcase.db'}?mode=ro", uri=True)) as db:
            for issuer_id, organization in db.execute("SELECT DISTINCT issuer_id, issuer_organization_name FROM assets"):
                issuers[issuer_id] = f"{organization} (demo issuer)"

    # 2. The synthetic dataset, through the real API, with build-time credentials discarded afterwards
    env = e2e_run._base_env(
        APP_ENV="test-showcase-build", DATABASE_URL=f"sqlite+aiosqlite:///{(data / 'showcase.db').as_posix()}",
        STORAGE_PATH=str(data / "storage"), ACCESS_CONTROL_FILE=str(data / "access_control.json"),
        STELLAR_ANCHOR_SECRET_FILE=str(data / "anchor_public.json"), AI_PROVIDER="disabled", ANTHROPIC_API_KEY="")
    seed = subprocess.run([sys.executable, str(ROOT / "scripts" / "e2e_seed.py"), str(data), "--public-showcase"],
                          cwd=data, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if seed.returncode != 0:
        raise SystemExit(f"seeding failed:\n{seed.stdout[-2000:]}{seed.stderr[-2000:]}")
    seeded = json.loads((data / "context.json").read_text(encoding="utf-8"))
    build_credentials = list(seeded["credentials"].values())
    for leftover in ("context.json", "access_control.json"):
        (data / leftover).unlink()

    # 3. The showcase registry: read-only roles and institutional keys only
    demo_issuers = [i for i in issuers if i not in SHOWCASE_ISSUERS]
    config, creds = _registry(issuers, {"a": ["e2e-meridian", *demo_issuers], "b": ["e2e-harbor"]})
    (data / "access_control.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from app.access_registry import load_access_registry
    load_access_registry(data / "access_control.json")       # refuses an invalid registry

    # 4. The production frontend build: same origin, API under /api
    www = deploy / "www"
    vite = subprocess.run([shutil.which("node"), "node_modules/vite/bin/vite.js", "build", "--outDir", str(www),
                           "--emptyOutDir", "--logLevel", "warn"], cwd=FRONTEND,
                          env={**os.environ, "VITE_API_URL": "/api"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if vite.returncode != 0:
        raise SystemExit(f"frontend build failed: {vite.stdout[-800:]}{vite.stderr[-800:]}")

    # 5. Settings, evaluator credentials (outside deploy/), rehearsal context
    (deploy / "showcase.env").write_text(
        "# RWA Legal Passport - read-only grant showcase (DEPLOYMENT.md). No secret is set here.\n"
        + "".join(f"{k}={v}\n" for k, v in SHOWCASE_SETTINGS.items()), encoding="utf-8")
    lines = ["RWA Legal Passport — read-only grant showcase: fictional credentials of this bundle.",
             "They only read synthetic data on a server that refuses every write.", ""]
    for name, label in (("evaluator_a", "Evaluator A (independent legal reviewer; issuers: e2e-meridian"
                         + "".join(f", {i}" for i in demo_issuers) + ")"),
                        ("institution_a", "Institution A (Legal Passport lookup, same issuers)"),
                        ("evaluator_b", "Evaluator B (issuer e2e-harbor only: shows tenant isolation)"),
                        ("institution_b", "Institution B (issuer e2e-harbor only)")):
        lines += [f"{label}:", f"    {creds[name]}", ""]
    (out / "evaluator-credentials.txt").write_text("\n".join(lines), encoding="utf-8")
    context = {"environment": "rehearsal", "credentials": creds,
               "assets": {k: v for k, v in seeded["assets"].items()}, "seed_model": seeded["seed_model"], **demo}
    (out / "rehearsal-context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")

    # 6. Checks: nothing private in what is served or deployed
    secrets_anywhere = [c.encode() for c in [*build_credentials, *creds.values()]]
    for path in www.rglob("*"):
        if path.is_file():
            body = path.read_bytes()
            leaked = [m for m in (*_FORBIDDEN_IN_WWW, *secrets_anywhere) if m in body]
            if leaked:
                raise SystemExit(f"REFUSED: {path.relative_to(out)} contains private material")
    for path in deploy.rglob("*"):
        if path.is_file() and any(c in path.read_bytes() for c in secrets_anywhere):
            raise SystemExit(f"REFUSED: {path.relative_to(out)} contains a plaintext credential")
    anchor_file = json.loads((data / "anchor_public.json").read_text(encoding="utf-8"))
    if set(anchor_file) - {"network", "public_key"} or not re.fullmatch(r"G[A-Z2-7]{55}", anchor_file["public_key"]):
        raise SystemExit("REFUSED: the anchoring account file must be verification-only (a public key, no seed)")
    files = sorted(p for p in deploy.rglob("*") if p.is_file())
    (deploy / "BUNDLE_MANIFEST.json").write_text(json.dumps(
        {"built_at": datetime.now().isoformat(timespec="seconds"), "with_stellar_demo": with_stellar_demo,
         "files": {p.relative_to(deploy).as_posix(): _sha256(p) for p in files}}, indent=2), encoding="utf-8")
    if (e2e_run._sha256_file(e2e_run.DEV_DATABASE) != dev_before or e2e_run._sha256_tree(e2e_run.DEMO_DATASET) != demo_before
            or e2e_run._sha256_tree(demo_dataset) != source_before):
        raise SystemExit("REFUSED: the development database or the demo dataset changed")
    return {"bundle": str(out), "files": len(files), "with_stellar_demo": with_stellar_demo,
            "assets": sorted(a["stellar_asset_code"] for a in seeded["assets"].values())
            + ([demo["asset"]["asset_code"]] if demo else [])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the read-only grant showcase bundle (DEPLOYMENT.md)")
    parser.add_argument("--with-stellar-demo", action="store_true",
                        help="include the local demo dataset with its genuine Stellar Testnet anchor")
    parser.add_argument("--demo-dataset", type=Path, default=e2e_run.DEMO_DATASET,
                        help="the demo dataset to include (default: scratch/stellar-demo/demo; e.g. "
                             "scratch/stellar-demo/demo-v2, the version 2 state anchored on 2026-10-20, DEMO.md)")
    parser.add_argument("--out", type=Path, help="an empty or new directory (default: scratch/showcase-bundle/<timestamp>)")
    args = parser.parse_args(argv)
    out = (args.out or ROOT / "scratch" / "showcase-bundle" / datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    if out.exists() and any(out.iterdir()):
        print(f"REFUSED: {out} is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    summary = build(out, args.with_stellar_demo, args.demo_dataset.resolve())
    print(json.dumps(summary, indent=2))
    print(f"Hand {out / 'evaluator-credentials.txt'} to evaluators privately; deploy only {out / 'deploy'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
