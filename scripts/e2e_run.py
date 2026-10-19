"""
Real-browser end-to-end tests of the RWA Legal Passport interface (Stage 10B-1, E2E_TESTING.md).

    python scripts/e2e_run.py                     # synthetic and showcase, plus demo-offline if the local demo dataset exists
    python scripts/e2e_run.py --testnet-readonly  # also the demo dataset against the live Stellar Testnet (read-only)
    python scripts/e2e_run.py --grep "passport" --headed

For every environment:
1. A new temporary directory holds the database, storage and access registry.
   - synthetic: seeded by scripts/e2e_seed.py through the real API, with random fictional credentials;
   - showcase (Stage 10C-1): the same seed, then served with READ_ONLY_MODE=true,
     SYNTHETIC_DATA_NOTICE=true and API_DOCS_ENABLED=false, as a public read-only showcase would be;
   - rehearsal (Stage 10C-2, DEPLOYMENT.md): the deployable showcase bundle of scripts/build_showcase.py
     (with the demo dataset's genuine anchor when it exists), run as on a server: uvicorn with the
     production flags and the bundle's showcase.env, behind deploy/rehearsal_proxy.py (the static
     build, single-page routing and /api on one origin). Live Testnet reads with --testnet-readonly;
   - demo-*: a copy of scratch/stellar-demo/demo, which is never written.
2. The backend (uvicorn, the real application) and the frontend (a production build served by
   `vite preview`) start on free ports chosen by the operating system. The ports in use (for
   example 8000 and 5173) are never touched, and only the processes started here are stopped.
3. Playwright drives Playwright's Chromium against them (frontend/playwright.config.ts).

Isolation and safety:
- The working directory of every Python process is the temporary directory, so the repository's
  .env is never loaded; every setting comes from the environment set here.
- AI_PROVIDER=disabled with an empty ANTHROPIC_API_KEY: no AI provider is contacted.
- No Stellar signing key is reachable: both datasets use a verification-only account file (a
  public key without a seed), so nothing can be signed or submitted.
- Offline environments route every outbound HTTP(S) request of the backend to a closed local port
  (HTTP(S)_PROXY): Horizon is unreachable and is reported as unavailable, never as a success.
- After the run: the development database is checked byte for byte, the demo dataset is checked
  unchanged, every report file is scanned for the run's credentials, and the temporary
  directories are deleted.

Reports: scratch/e2e/<run>/<environment>/ (gitignored): Playwright's JSON report, screenshots of
failures only, and the server logs. Credentials are kept out of them at the source (Stage 10B-2):
- traces, videos and the HTML report are off (they record typed values);
- PLAYWRIGHT_NO_COPY_PROMPT=1 stops the page snapshot of error-context.md (it contains input values);
- credential fields are password inputs and no page renders a credential, so a screenshot (taken
  when the test function ends) never shows one;
- credential values in a failure message are redacted by the test fixture before Playwright writes
  error-context.md or the report, and a failing test's inputs and session storage are cleared
  before its browser context is closed;
- failed-request diagnostics hold method, path and status only.
A deliberately failing self-test (synthetic/report-hygiene.e2e.ts), whose failure message names a
typed credential, produces those artifacts on every run: its error-context.md must carry the
redaction marker. Afterwards every retained file is scanned for the run's credential values. A
value found fails the run (and the file is deleted): the scan verifies the prevention, it does not
replace it.
Exit codes: 0 every environment passed; 1 a test or check failed; 2 refused (a prerequisite is missing).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DEV_DATABASE = ROOT / "rwa_legal_passport.db"
DEMO_DATASET = ROOT / "scratch" / "stellar-demo" / "demo"
REPORTS = ROOT / "scratch" / "e2e"
RESERVED_PORTS = {8000, 5173}           # the development servers' default ports
# Every variable the application reads (app/config.py), and proxies: none is inherited by the servers
APP_VARIABLES = ("APP_", "DATABASE_URL", "SQLITE_", "MAX_UPLOAD", "STORAGE_PATH", "MAX_PDF", "PDF_PARSE", "AI_",
                 "ANTHROPIC", "ACCESS_CONTROL", "CORS_", "EXTRACTION_RATE", "STELLAR_", "REVIEWER_",
                 "INSTITUTIONAL_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "READ_ONLY_MODE",
                 "API_DOCS_ENABLED", "SYNTHETIC_DATA_NOTICE")
# A public read-only showcase of synthetic data (Stage 10C-1, GRANT_DEMO.md)
SHOWCASE_POLICIES = {"READ_ONLY_MODE": "true", "SYNTHETIC_DATA_NOTICE": "true", "API_DOCS_ENABLED": "false"}


class Refused(Exception):
    pass


def _sha256_file(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _sha256_tree(path: Path) -> Optional[str]:
    if not path.is_dir():
        return None
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def _free_port() -> int:
    """A port nothing listens on, chosen by the operating system (never a reserved one)."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port


def _base_env(**values: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(APP_VARIABLES)}
    env.update(values)
    return env


def _wait_http(url: str, process: subprocess.Popen, check, seconds: float = 60.0) -> None:
    """Polls `url` until `check(body)` holds; fails if the process exits or the deadline passes."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url}: the server exited (code {process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200 and check(response.read()):
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{url}: not ready within {seconds:.0f} s")


class Stack:
    """The servers of one environment: started here, stopped here, nothing else."""

    api_suffix = ""

    def __init__(self, name: str, root: Path, out: Path, offline: bool) -> None:
        self.name, self.root, self.out, self.offline = name, root, out, offline
        # APP_ENV: the test profile, labelled with this run; /health names it, so a stale server
        # on the same port is never mistaken for this one
        self.app_env_value = f"test-e2e-{name}"
        self.api_port, self.web_port = _free_port(), _free_port()
        self.processes: list[subprocess.Popen] = []
        self._logs = []

    def app_env(self, database: Path, storage: Path, registry: Path, anchor_file: Path) -> dict:
        values = {
            "APP_ENV": self.app_env_value,
            "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
            "STORAGE_PATH": str(storage),
            "ACCESS_CONTROL_FILE": str(registry),
            "STELLAR_ANCHOR_SECRET_FILE": str(anchor_file),
            "AI_PROVIDER": "disabled",
            "ANTHROPIC_API_KEY": "",
            "CORS_ALLOWED_ORIGINS": f"http://127.0.0.1:{self.web_port}",
        }
        if self.offline:
            closed = f"http://127.0.0.1:{_free_port()}"   # nothing listens there: every outbound request fails
            values.update(HTTP_PROXY=closed, HTTPS_PROXY=closed, ALL_PROXY=closed, NO_PROXY="127.0.0.1,localhost")
        return _base_env(**values)

    def _spawn(self, name: str, cmd: list[str], env: dict, cwd: Path) -> subprocess.Popen:
        log = open(self.out / f"{name}.log", "w", encoding="utf-8")
        self._logs.append(log)
        process = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    def start(self, env: dict) -> None:
        backend = self._spawn("backend", [
            sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT),
            "--host", "127.0.0.1", "--port", str(self.api_port)], env, self.root)
        _wait_http(f"http://127.0.0.1:{self.api_port}/health", backend,
                   lambda body: json.loads(body).get("environment") == self.app_env_value)
        node = shutil.which("node")
        dist = self.root / "dist"
        build = subprocess.run([node, "node_modules/vite/bin/vite.js", "build", "--outDir", str(dist), "--emptyOutDir",
                                "--logLevel", "warn"],
                               cwd=FRONTEND, env={**os.environ, "VITE_API_URL": f"http://127.0.0.1:{self.api_port}"},
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        if build.returncode != 0:
            raise RuntimeError(f"frontend build failed: {build.stdout[-500:]}{build.stderr[-500:]}")
        frontend = self._spawn("frontend", [
            node, "node_modules/vite/bin/vite.js", "preview", "--outDir", str(dist),
            "--host", "127.0.0.1", "--port", str(self.web_port), "--strictPort"], dict(os.environ), FRONTEND)
        _wait_http(f"http://127.0.0.1:{self.web_port}/", frontend, lambda body: b'<div id="root">' in body)

    def stop(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
        for log in self._logs:
            log.close()


class RehearsalStack(Stack):
    """The deployment rehearsal: the bundle served as on a server, behind the stand-in proxy."""

    api_suffix = "/api"
    bundle: Path

    def start(self, env: dict) -> None:
        deploy = self.bundle / "deploy"
        backend = self._spawn("backend", [
            sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT),
            "--host", "127.0.0.1", "--port", str(self.api_port), "--workers", "1",
            "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1", "--no-server-header", "--no-access-log"],
            env, deploy)
        _wait_http(f"http://127.0.0.1:{self.api_port}/health", backend,
                   lambda body: json.loads(body).get("environment") == self.app_env_value)
        proxy = self._spawn("proxy", [
            sys.executable, str(ROOT / "deploy" / "rehearsal_proxy.py"), "--www", str(deploy / "www"),
            "--backend", f"http://127.0.0.1:{self.api_port}", "--port", str(self.web_port)], dict(os.environ), self.root)
        _wait_http(f"http://127.0.0.1:{self.web_port}/", proxy, lambda body: b'<div id="root">' in body)


def _prepare_rehearsal(stack: Stack) -> tuple[dict, dict]:
    import build_showcase   # scripts/build_showcase.py
    stack.bundle = stack.root / "bundle"
    stack.bundle.mkdir()
    build_showcase.build(stack.bundle, with_stellar_demo=DEMO_DATASET.is_dir())
    settings = dict(line.split("=", 1) for line in
                    (stack.bundle / "deploy" / "showcase.env").read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#"))
    # Identifies this run's server, keeping the bundle's profile (production): its startup checks
    # run on the bundle's settings. Every other setting is the bundle's.
    stack.app_env_value = f"{settings['APP_ENV'].partition('-')[0]}-e2e-{stack.name}"
    settings["APP_ENV"] = stack.app_env_value
    if stack.offline:
        closed = f"http://127.0.0.1:{_free_port()}"
        settings.update(HTTP_PROXY=closed, HTTPS_PROXY=closed, ALL_PROXY=closed, NO_PROXY="127.0.0.1,localhost")
    context = json.loads((stack.bundle / "rehearsal-context.json").read_text(encoding="utf-8"))
    context["network"] = "offline" if stack.offline else "testnet"
    (stack.root / "context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    return _base_env(**settings), context


def _prepare_synthetic(stack: Stack) -> tuple[dict, dict]:
    root = stack.root
    # anchor_public.json is written by the seeder: a verification-only account (public key, no seed)
    env = stack.app_env(root / "e2e.db", root / "storage", root / "access_control.json", root / "anchor_public.json")
    seed = subprocess.run([sys.executable, str(ROOT / "scripts" / "e2e_seed.py"), str(root)], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    (stack.out / "seed.log").write_text(seed.stdout + seed.stderr, encoding="utf-8")
    if seed.returncode != 0:
        raise RuntimeError(f"seeding failed (see {stack.out / 'seed.log'})")
    return env, json.loads((root / "context.json").read_text(encoding="utf-8"))


def _prepare_demo(stack: Stack) -> tuple[dict, dict]:
    copy = stack.root / "demo"
    shutil.copytree(DEMO_DATASET, copy)
    demo = json.loads((copy / "demo.json").read_text(encoding="utf-8"))
    env = stack.app_env(copy / "demo.db", copy / "storage", copy / "access_control.json", copy / "anchor_public.json")
    creds = demo["fictional_demo_credentials"]
    context = {"environment": stack.name, "network": "offline" if stack.offline else "testnet",
               "credentials": {"admin": creds["platform_admin_token"], "issuer": creds["issuer_reviewer_token"],
                               "legal": creds["legal_reviewer_token"], "inst": creds["institutional_api_key"]},
               "asset": demo["asset"], "anchor": demo["anchor"]}
    (stack.root / "context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    return env, context


# Terminal colour codes, raw and JSON-escaped: a matcher's diff highlighting can split a value
_ANSI_CODES = re.compile(rb"(?:\x1b|\\u001b|\\x1b)\[[0-9;]*m")


def _secrets_in(directory: Path, context: dict) -> list[Path]:
    """Files holding a credential value, as written or once colour codes are removed."""
    values = [v.encode() for v in context.get("credentials", {}).values() if v]

    def holds_secret(data: bytes) -> bool:
        plain = _ANSI_CODES.sub(b"", data)
        return any(v in data or v in plain for v in values)

    return [file for file in directory.rglob("*") if file.is_file() and holds_secret(file.read_bytes())]


REDACTION_MARKER = b"[credential redacted]"   # frontend/e2e/support.ts


def _selftest_artifacts(out: Path) -> dict:
    """What the report-hygiene self-test left behind (synthetic/report-hygiene.e2e.ts)."""
    files = [f for d in (out / "artifacts").glob("*report-hygiene*") for f in d.iterdir() if f.is_file()] \
        if (out / "artifacts").is_dir() else []
    contexts = [f for f in files if f.name == "error-context.md"]
    return {"files": sorted({f.name for f in files}),
            "screenshot_written": any(f.suffix == ".png" for f in files),
            "error_context_written": bool(contexts),
            # The failure message named the typed credential: the marker shows it was replaced
            "error_context_redacted": any(REDACTION_MARKER in f.read_bytes() for f in contexts)}


def run_environment(name: str, out: Path, args: argparse.Namespace) -> dict:
    offline = name != "demo-testnet" and not (name == "rehearsal" and args.testnet_readonly)
    root = Path(tempfile.mkdtemp(prefix=f"rwa-e2e-{name}-"))
    out.mkdir(parents=True, exist_ok=True)
    stack = (RehearsalStack if name == "rehearsal" else Stack)(name, root, out, offline)
    result = {"environment": name, "network": "offline (outbound HTTP blocked)" if offline else "live Stellar Testnet (read-only)",
              "ports": {"api": stack.api_port, "web": stack.web_port}}
    context: dict = {}
    try:
        prepare = _prepare_rehearsal if name == "rehearsal" else _prepare_demo if name.startswith("demo") else _prepare_synthetic
        env, context = prepare(stack)
        if name == "showcase":
            env = {**env, **SHOWCASE_POLICIES}   # seeded writable above; served read-only
        stack.start(env)
        command = [shutil.which("node"), "node_modules/@playwright/test/cli.js", "test", f"--project={name}"]
        if args.grep:
            command += ["--grep", args.grep]
        if args.headed:
            command.append("--headed")
        test_env = {**os.environ, "E2E_CONTEXT": str(root / "context.json"), "E2E_OUTPUT": str(out),
                    "E2E_BASE_URL": f"http://127.0.0.1:{stack.web_port}",
                    "E2E_API_URL": (f"http://127.0.0.1:{stack.web_port}/api" if stack.api_suffix
                                    else f"http://127.0.0.1:{stack.api_port}"),
                    "PLAYWRIGHT_NO_COPY_PROMPT": "1"}
        print(f"\n=== {name}: backend :{stack.api_port}, frontend :{stack.web_port}, {result['network']} ===", flush=True)
        playwright = subprocess.run(command, cwd=FRONTEND, env=test_env)
        result["playwright_exit_code"] = playwright.returncode
    except Exception as exc:  # noqa: BLE001 — reported, then the environment is torn down
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stack.stop()
        # Detected before anything is deleted: a hit means the prevention failed, and fails the run
        leaks = _secrets_in(out, context)
        result["credential_leaks_detected"] = [str(f.relative_to(out)) for f in leaks]
        if name == "synthetic":
            result["security_selftest_artifacts"] = _selftest_artifacts(out)
        for leaked in leaks:
            leaked.unlink()
        if not args.keep_temp:
            shutil.rmtree(root, ignore_errors=True)
        result["temporary_directory_removed"] = not root.exists()
    # A temporary directory that could not be removed (e.g. a file still open) fails the run too
    result["passed"] = result.get("playwright_exit_code") == 0 and not result["credential_leaks_detected"] \
        and "error" not in result and (args.keep_temp or result["temporary_directory_removed"])
    if name == "synthetic" and not args.grep:
        # The failing self-test must have produced its artifacts, for the scan to have checked them,
        # and its credential must have been redacted from the failure message before it was written
        selftest = result.get("security_selftest_artifacts", {})
        result["passed"] = result["passed"] and all(
            selftest.get(k) for k in ("screenshot_written", "error_context_written", "error_context_redacted"))
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Real-browser end-to-end tests (E2E_TESTING.md)")
    parser.add_argument("--testnet-readonly", action="store_true",
                        help="also verify the demo anchor against the live Stellar Testnet (read-only, no transaction)")
    parser.add_argument("--no-demo", action="store_true", help="skip the demo dataset environments")
    parser.add_argument("--grep", help="run only the tests whose title matches")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--keep-temp", action="store_true", help="keep the temporary directories")
    args = parser.parse_args(argv)

    try:
        if not shutil.which("node"):
            raise Refused("node was not found")
        if not (FRONTEND / "node_modules" / "@playwright" / "test").is_dir():
            raise Refused("@playwright/test is not installed (cd frontend; npm install)")
        if args.testnet_readonly and not DEMO_DATASET.is_dir():
            raise Refused(f"--testnet-readonly needs the local demo dataset {DEMO_DATASET} (DEMO.md)")
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    environments = ["synthetic", "showcase", "rehearsal"]
    if DEMO_DATASET.is_dir() and not args.no_demo:
        environments.append("demo-offline")
        if args.testnet_readonly:
            environments.append("demo-testnet")
    elif not args.no_demo:
        print(f"demo dataset not found ({DEMO_DATASET}); the demo environments are skipped")

    run = REPORTS / datetime.now().strftime("%Y%m%d-%H%M%S")
    dev_before, demo_before = _sha256_file(DEV_DATABASE), _sha256_tree(DEMO_DATASET)
    results = [run_environment(name, run / name, args) for name in environments]
    summary = {
        "run": str(run.relative_to(ROOT)), "environments": results,
        "development_database_unchanged": _sha256_file(DEV_DATABASE) == dev_before,
        "demo_dataset_unchanged": _sha256_tree(DEMO_DATASET) == demo_before,
    }
    (run / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=2))
    ok = all(r["passed"] for r in results) and summary["development_database_unchanged"] and summary["demo_dataset_unchanged"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
