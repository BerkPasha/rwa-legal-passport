"""
Local grant demo of the RWA Legal Passport (Stage 10C-1, GRANT_DEMO.md).

Starts one disposable environment on this computer only (127.0.0.1), keeps it running until Ctrl+C
(or --minutes), then stops it and deletes it:

    python scripts/grant_demo.py                                   # read-only showcase, synthetic dataset (offline)
    python scripts/grant_demo.py --dataset stellar-demo            # read-only, the demo dataset with its real Testnet anchor (offline)
    python scripts/grant_demo.py --dataset stellar-demo --testnet-readonly   # the same, verified live on Stellar Testnet (read-only)
    python scripts/grant_demo.py --sandbox                         # writable evaluator sandbox, synthetic dataset, local only

It reuses the isolated environments of the browser tests (scripts/e2e_run.py): a new temporary
database, storage and access registry with fresh fictional credentials, the real backend and a
production build of the frontend on free ports, AI_PROVIDER=disabled, no Stellar signing key, and
the repository's .env never loaded. The showcase is served with READ_ONLY_MODE=true,
SYNTHETIC_DATA_NOTICE=true and API_DOCS_ENABLED=false. The development database and the demo
dataset are checked unchanged at the end.

The run's fictional credentials are written to <temporary directory>/demo-credentials.txt; only the
path is printed. Nothing is reachable from another computer, and nothing is submitted to Stellar.
"""

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_run  # noqa: E402  (the isolated environments of the browser tests)

# Who each fictional credential of the synthetic dataset is (scripts/e2e_seed.py)
_SYNTHETIC_ROLES = {
    "legal_a": "Independent legal reviewer, issuer e2e-meridian (suggested evaluator sign-in)",
    "issuer_a": "Issuer reviewer, issuer e2e-meridian",
    "issuer_b": "Issuer reviewer, issuer e2e-harbor (another tenant)",
    "admin": "Platform operator",
    "inst_a": "Institutional API key, issuer e2e-meridian (Legal Passport lookup)",
    "inst_b": "Institutional API key, issuer e2e-harbor",
}
_DEMO_ROLES = {
    "legal": "Independent legal reviewer (suggested evaluator sign-in)",
    "issuer": "Issuer reviewer",
    "admin": "Platform operator",
    "inst": "Institutional API key (Legal Passport lookup)",
}


def _credentials_file(root: Path, context: dict, sandbox: bool) -> Path:
    roles = _DEMO_ROLES if "asset" in context else _SYNTHETIC_ROLES
    lines = [
        "RWA Legal Passport — local grant demo: fictional credentials of this run only.",
        "They exist in this temporary environment and stop working when it is stopped.",
        "Sign in through the sidebar 'Credentials' panel: user token for reviewers, API key for institutions.",
        "This server is " + ("a writable sandbox (synthetic data)." if sandbox
                             else "a read-only showcase: every write is refused by the server."),
        "",
    ]
    for name, description in roles.items():
        if name in context["credentials"]:
            lines += [f"{description}:", f"    {context['credentials'][name]}", ""]
    path = root / "demo-credentials.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local grant demo (GRANT_DEMO.md)")
    parser.add_argument("--dataset", choices=("synthetic", "stellar-demo"), default="synthetic")
    parser.add_argument("--sandbox", action="store_true",
                        help="writable evaluator sandbox (synthetic dataset, local only)")
    parser.add_argument("--testnet-readonly", action="store_true",
                        help="stellar-demo only: verify the anchor live on Stellar Testnet (read-only)")
    parser.add_argument("--minutes", type=float, help="stop by itself after this many minutes")
    args = parser.parse_args(argv)

    if args.sandbox and args.dataset != "synthetic":
        print("REFUSED: the sandbox uses the synthetic dataset only", file=sys.stderr)
        return 2
    if args.testnet_readonly and args.dataset != "stellar-demo":
        print("REFUSED: --testnet-readonly applies to --dataset stellar-demo", file=sys.stderr)
        return 2
    if args.dataset == "stellar-demo" and not e2e_run.DEMO_DATASET.is_dir():
        print(f"REFUSED: the demo dataset {e2e_run.DEMO_DATASET} does not exist (DEMO.md)", file=sys.stderr)
        return 2
    if not shutil.which("node") or not (e2e_run.FRONTEND / "node_modules" / "vite").is_dir():
        print("REFUSED: node and the frontend dependencies are needed (cd frontend; npm install)", file=sys.stderr)
        return 2

    name = ("sandbox" if args.sandbox else "showcase") if args.dataset == "synthetic" else \
        ("demo-testnet" if args.testnet_readonly else "demo-offline")
    dev_before, demo_before = e2e_run._sha256_file(e2e_run.DEV_DATABASE), e2e_run._sha256_tree(e2e_run.DEMO_DATASET)
    root = Path(tempfile.mkdtemp(prefix=f"rwa-grant-demo-{name}-"))
    logs = root / "logs"
    logs.mkdir()
    stack = e2e_run.Stack(name, root, logs, offline=not args.testnet_readonly)
    try:
        env, context = (e2e_run._prepare_demo if args.dataset == "stellar-demo" else e2e_run._prepare_synthetic)(stack)
        if not args.sandbox:
            env = {**env, **e2e_run.SHOWCASE_POLICIES}
        stack.start(env)
        credentials = _credentials_file(root, context, args.sandbox)
        mode = "WRITABLE SANDBOX (synthetic data, this computer only)" if args.sandbox else "READ-ONLY SHOWCASE"
        print(f"\n{mode}: http://127.0.0.1:{stack.web_port}/")
        print(f"API: http://127.0.0.1:{stack.api_port}/health")
        print(f"Network: {'live Stellar Testnet, read-only' if args.testnet_readonly else 'offline (outbound HTTP blocked)'}")
        print(f"Fictional credentials of this run: {credentials}")
        print("Press Ctrl+C to stop and delete this environment.", flush=True)
        deadline = time.monotonic() + args.minutes * 60 if args.minutes else None
        while deadline is None or time.monotonic() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stack.stop()
        shutil.rmtree(root, ignore_errors=True)
        unchanged = (e2e_run._sha256_file(e2e_run.DEV_DATABASE) == dev_before
                     and e2e_run._sha256_tree(e2e_run.DEMO_DATASET) == demo_before)
        print(f"\nStopped. Temporary environment removed: {not root.exists()}. "
              f"Development database and demo dataset unchanged: {unchanged}.", flush=True)
    return 0 if unchanged and not root.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
