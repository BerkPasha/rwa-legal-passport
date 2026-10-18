"""
Run the backend on the prepared local demo dataset (DEMO.md).

    python scripts/run_stellar_demo.py [--demo scratch/stellar-demo/demo] [--port 8000]

For this process only (.env is not changed):
- DATABASE_URL, STORAGE_PATH and ACCESS_CONTROL_FILE point to the demo dataset (fictional
  credentials; never the development database);
- STELLAR_ANCHOR_SECRET_FILE points to the verification-only file (public key, no secret seed):
  verification queries the real Stellar Testnet, and anchoring is refused, so no transaction can
  be submitted from the demo;
- AI_PROVIDER is disabled and ANTHROPIC_API_KEY is emptied, so that no document is sent to an AI
  service and no paid AI call can be made.

Then start the frontend (cd frontend; npm run dev) and open http://localhost:5173.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the backend on the local Stellar demo dataset")
    parser.add_argument("--demo", default=str(ROOT / "scratch" / "stellar-demo" / "demo"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    demo = Path(args.demo).resolve()
    required = [demo / "demo.db", demo / "storage", demo / "access_control.json", demo / "anchor_public.json"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("Demo dataset incomplete; run scripts/prepare_stellar_demo.py first. Missing: " + ", ".join(missing))
        return 2
    if "secret_seed" in (demo / "anchor_public.json").read_text(encoding="utf-8"):
        print("Refusing: the demo anchoring file must not contain a secret seed.")
        return 2

    os.environ.update({
        "APP_ENV": "demo",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(demo / 'demo.db').as_posix()}",
        "STORAGE_PATH": str(demo / "storage"),
        "ACCESS_CONTROL_FILE": str(demo / "access_control.json"),
        "STELLAR_ANCHOR_SECRET_FILE": str(demo / "anchor_public.json"),
        "AI_PROVIDER": "disabled",
        "ANTHROPIC_API_KEY": "",
    })
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import uvicorn

    print(f"Demo backend on http://127.0.0.1:{args.port} (dataset {demo}; verification-only, no paid AI)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
