"""
Offline isolation of the real-browser end-to-end tests (Stage 10C-3B; E2E_TESTING.md, "Offline isolation").

Chromium is started only on a computer whose external networking the operator has switched off. Browser
flags alone are not enough: a scratch experiment (2026-10-19) showed Chromium's DNS error page querying
DNS for google.com, through the system resolver and 8.8.8.8, outside --host-resolver-rules, and
Playwright's Chromium contacting Google services in the background. The isolation boundary is
therefore the operating system's lack of an external route. Everything here either verifies that
boundary or records what the browser attempted against it.

scripts/e2e_run.py uses:
1. collect_network_state / assess_network_state: the offline preflight. A snapshot of the Windows IP
   interfaces, addresses, routes and adapters, read with local PowerShell cmdlets (nothing is sent
   anywhere), and its fail-closed assessment.
2. NetworkMonitor: the same snapshot about once a second while the run is in progress.
3. Sinkhole: Chromium's only proxy. It records the method and host:port of each request and refuses
   it; it never opens a connection itself.
4. load_netlog / classify_netlog: Chromium's own record of what it attempted.
5. evaluate_evidence / compare_counters / counter_evidence: what fails the run, and what is only reported.

The functions are tested on synthetic data, without a browser, in tests/test_e2e_isolation.py.
"""

import base64
import ipaddress
import json
import os
import queue
import re
import socket
import socketserver
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.parse import urlsplit

# ── 1. Offline preflight ──────────────────────────────────────────────────────

# Read-only cmdlets of the NetTCPIP and NetAdapter modules, one compact JSON line per snapshot. The JSON
# is escaped to ASCII, so that the console code page cannot alter adapter names (Turkish names arrive
# garbled otherwise)
_SNAPSHOT_FUNCTION = r"""
$ErrorActionPreference = 'Stop'
function Get-RwaNetworkState {
  $state = [ordered]@{
    interfaces = @(Get-NetIPInterface | ForEach-Object { [ordered]@{
      ifIndex = [int]$_.ifIndex; alias = [string]$_.InterfaceAlias; family = [string]$_.AddressFamily
      state = [string]$_.ConnectionState } })
    addresses = @(Get-NetIPAddress | ForEach-Object { [ordered]@{
      ifIndex = [int]$_.InterfaceIndex; address = [string]$_.IPAddress; state = [string]$_.AddressState } })
    routes = @(Get-NetRoute | ForEach-Object { [ordered]@{
      ifIndex = [int]$_.ifIndex; prefix = [string]$_.DestinationPrefix; nextHop = [string]$_.NextHop } })
    adapters = @(Get-NetAdapter -IncludeHidden | ForEach-Object { [ordered]@{
      ifIndex = [int]$_.ifIndex; name = [string]$_.Name; status = [string]$_.Status
      hardware = [bool]$_.HardwareInterface } })
    counters = @(Get-NetAdapterStatistics -IncludeHidden -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{
      name = [string]$_.Name; sentBytes = [uint64]$_.SentBytes
      sentPackets = [uint64]$_.SentUnicastPackets + [uint64]$_.SentMulticastPackets + [uint64]$_.SentBroadcastPackets } })
  }
  $json = $state | ConvertTo-Json -Depth 4 -Compress
  [regex]::Replace($json, '[^\x20-\x7E]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
}
"""
SNAPSHOT_ONCE = _SNAPSHOT_FUNCTION + "[Console]::Out.WriteLine((Get-RwaNetworkState)); [Console]::Out.Flush()\n"
SNAPSHOT_WATCH = _SNAPSHOT_FUNCTION + (
    "while ($true) { [Console]::Out.WriteLine((Get-RwaNetworkState)); [Console]::Out.Flush(); "
    "Start-Sleep -Milliseconds 500 }\n")

_FIELDS = {
    "interfaces": {"ifIndex": int, "alias": str, "family": str, "state": str},
    "addresses": {"ifIndex": int, "address": str, "state": str},
    "routes": {"ifIndex": int, "prefix": str, "nextHop": str},
    "adapters": {"ifIndex": int, "name": str, "status": str, "hardware": bool},
    "counters": {"name": str, "sentBytes": int, "sentPackets": int},
}
# States under which an interface or adapter cannot send anything; any other value, including one
# this list does not know, counts as a possible path out
_DOWN_INTERFACE_STATES = {"Disconnected"}
_DOWN_ADAPTER_STATUSES = {"Disconnected", "Disabled", "Not Present"}
_ON_LINK = {"0.0.0.0", "::", ""}
_DEFAULT_ROUTES = {"0.0.0.0/0", "::/0"}


class NetworkStateUnavailable(Exception):
    """The network state could not be read reliably: the run is refused (fail-closed)."""


def powershell_command(script: str) -> list[str]:
    """Windows PowerShell from System32 (not from PATH), with the script passed encoded."""
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if os.name != "nt" or not executable.is_file():
        raise NetworkStateUnavailable("the network state can be read on Windows only (PowerShell NetTCPIP cmdlets)")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def parse_network_state(raw) -> dict:
    """One snapshot line, checked field by field: anything unexpected makes the state unreadable."""
    try:
        text = raw.decode("ascii") if isinstance(raw, (bytes, bytearray)) else str(raw)
        state = json.loads(text.strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NetworkStateUnavailable(f"the network state is not valid JSON ({type(exc).__name__})") from exc
    if not isinstance(state, dict) or set(state) != set(_FIELDS):
        raise NetworkStateUnavailable("the network state does not have the expected sections")
    for section, fields in _FIELDS.items():
        items = state[section]
        if not isinstance(items, list) or not all(isinstance(item, dict) and set(item) == set(fields) for item in items):
            raise NetworkStateUnavailable(f"the network state's {section} do not have the expected fields")
        for item in items:
            for name, kind in fields.items():
                if type(item[name]) is not kind:      # not isinstance: a bool is no interface index
                    raise NetworkStateUnavailable(f"the network state's {section} have a malformed {name}")
    if not state["interfaces"] or not state["addresses"]:
        raise NetworkStateUnavailable("the network state lists no interface or no address")
    return state


def collect_network_state(timeout: float = 30.0, run=subprocess.run) -> dict:
    """A snapshot of this computer's network state. Read-only and local: nothing is sent anywhere."""
    try:
        done = run(powershell_command(SNAPSHOT_ONCE), capture_output=True, timeout=timeout,
                   stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkStateUnavailable(f"PowerShell did not answer ({type(exc).__name__})") from exc
    if done.returncode != 0:
        raise NetworkStateUnavailable(f"PowerShell exited with code {done.returncode}")
    return parse_network_state(done.stdout)


def _address(text: str):
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None


def _label(name: str) -> str:
    """An interface or adapter name for a message: ASCII, since Windows can deliver it garbled (U+FFFD)."""
    return name.encode("ascii", "replace").decode("ascii")


@dataclass(frozen=True)
class NetworkAssessment:
    offline: bool
    problems: tuple[str, ...]


def assess_network_state(state: dict) -> NetworkAssessment:
    """
    Offline means: loopback is the only connected interface, no route leads to a gateway (in
    particular no default route), no other interface holds a usable address, and no hardware
    adapter is up. Whatever cannot be read, or is not known to be down, is a problem.
    """
    problems: list[str] = []
    loopback = set()
    for entry in state["addresses"]:
        address = _address(entry["address"])
        if address is None:
            problems.append(f"interface {entry['ifIndex']} holds an unreadable address")
        elif address.is_loopback:
            loopback.add(entry["ifIndex"])
    if not loopback:
        problems.append("no loopback interface was found")
    for entry in state["addresses"]:
        address = _address(entry["address"])
        if address is None or address.is_loopback:
            continue
        if entry["ifIndex"] in loopback:
            problems.append(f"the loopback interface {entry['ifIndex']} also holds a non-loopback address")
        elif entry["state"] == "Preferred":
            problems.append(f"interface {entry['ifIndex']} holds a usable (Preferred) IPv{address.version} address")
    if loopback and not any(i["ifIndex"] in loopback and i["family"] == "IPv4" and i["state"] == "Connected"
                            for i in state["interfaces"]):
        problems.append("the loopback interface is not connected (IPv4): the run's servers would be unreachable")
    for interface in state["interfaces"]:
        if interface["ifIndex"] not in loopback and interface["state"] not in _DOWN_INTERFACE_STATES:
            problems.append(f"interface {interface['ifIndex']} ({_label(interface['alias'])}, {interface['family']}) "
                            f"is {interface['state']}")
    for route in state["routes"]:
        if route["prefix"] in _DEFAULT_ROUTES:
            problems.append(f"default route {route['prefix']} on interface {route['ifIndex']}")
        elif route["nextHop"] not in _ON_LINK:
            problems.append(f"route {route['prefix']} through a gateway on interface {route['ifIndex']}")
    for adapter in state["adapters"]:
        if adapter["hardware"] and adapter["status"] not in _DOWN_ADAPTER_STATUSES:
            problems.append(f"hardware adapter {adapter['ifIndex']} ({_label(adapter['name'])}) is {adapter['status']}")
    unique = tuple(dict.fromkeys(problems))
    return NetworkAssessment(offline=not unique, problems=unique)


# ── 2. Network-state monitor ──────────────────────────────────────────────────


class NetworkMonitor:
    """
    Re-reads the network state about once a second while the browser tests run: one PowerShell child
    prints a snapshot per line. The first unacceptable or unreadable state, a silence longer than
    `stale_after` seconds, or the end of the reader calls `on_violation` once, and e2e_run.py stops
    every process of the run.

    The check is periodic. A connection that appears and disappears between two snapshots, and
    whatever is sent in the second or two before a violation is acted on, is not prevented by it.
    """

    def __init__(self, on_violation: Callable[[str], None], command: Optional[Sequence[str]] = None,
                 stale_after: float = 10.0) -> None:
        self._on_violation = on_violation
        self._command = list(command) if command else None
        self._stale_after = stale_after
        self._lines: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._threads: list[threading.Thread] = []
        self.snapshots = 0
        self.violation: Optional[str] = None

    def start(self) -> None:
        command = self._command or powershell_command(SNAPSHOT_WATCH)
        self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL)
        self._threads = [threading.Thread(target=self._read, daemon=True),
                         threading.Thread(target=self._check, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _read(self) -> None:
        for line in self._process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _check(self) -> None:
        while not self._stopping.is_set():
            try:
                line = self._lines.get(timeout=self._stale_after)
            except queue.Empty:
                return self._violate(f"no network state was read for {self._stale_after:.0f} s")
            if self._stopping.is_set():
                return
            if line is None:
                return self._violate("the network-state reader stopped")
            try:
                assessment = assess_network_state(parse_network_state(line))
            except NetworkStateUnavailable as exc:
                return self._violate(str(exc))
            self.snapshots += 1
            if not assessment.offline:
                return self._violate("; ".join(assessment.problems))

    def _violate(self, reason: str) -> None:
        with self._lock:
            if self.violation is not None or self._stopping.is_set():
                return
            self.violation = reason
        self._on_violation(reason)

    def stop(self) -> None:
        self._stopping.set()
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
            self._process.wait(timeout=15)
        for thread in self._threads:
            thread.join(timeout=15)


# ── 3. Sinkhole proxy ─────────────────────────────────────────────────────────

# The positive controls of frontend/e2e/isolation/: names under .localhost, which Chromium answers
# itself as loopback, and a loopback address outside the exact proxy bypass. Even with every other
# protection gone, they could reach only this computer.
PROBE_SUFFIX = ".isolation-probe.localhost"
PROBE_ADDRESSES = frozenset({"127.0.0.2"})
# What the isolation project's requests must have left in the sinkhole: (host, port), None = any port
REQUIRED_PROBES = (("egress.isolation-probe.localhost", 80), ("egress-tls.isolation-probe.localhost", 443),
                   ("ws.isolation-probe.localhost", 80), ("127.0.0.2", None))

_REFUSAL_BODY = b"refused by the E2E sinkhole\n"
_REFUSAL = (b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n"
            b"X-E2E-Sinkhole: refused\r\nConnection: close\r\n\r\n" % len(_REFUSAL_BODY)) + _REFUSAL_BODY
_DEFAULT_PORTS = {"http": 80, "ws": 80, "https": 443, "wss": 443}
_HOST = re.compile(r"^[a-z0-9._~-]+$|^[0-9a-f:.]+$")


def is_probe(host: str) -> bool:
    return host in PROBE_ADDRESSES or host.endswith(PROBE_SUFFIX)


def _host_port(text: str) -> Optional[tuple[str, int]]:
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    host = host.lower()
    return (host, int(port)) if host and _HOST.match(host) else None


def parse_proxy_request_line(line: bytes) -> dict:
    """
    The destination of one proxy request line, as {"method", "host", "port"}. The path, the query and
    the headers are never kept: a request can carry credentials there.
    """
    parts = line.decode("latin-1").split(" ")
    method = parts[0].upper() if parts and parts[0].isalpha() and len(parts[0]) <= 16 else ""
    if len(parts) != 3 or not method or not parts[2].startswith("HTTP/"):
        return {"method": method, "malformed": True}
    if method == "CONNECT":
        destination = _host_port(parts[1])
    else:
        target = urlsplit(parts[1])               # absolute form, as a client sends it to a proxy
        try:
            port = target.port or _DEFAULT_PORTS.get(target.scheme)
        except ValueError:
            port = None
        host = (target.hostname or "").lower()
        destination = (host, port) if target.scheme in _DEFAULT_PORTS and port and host and _HOST.match(host) else None
    if destination is None:
        return {"method": method, "malformed": True}
    return {"method": method, "host": destination[0], "port": destination[1]}


class Sinkhole:
    """
    Chromium's only proxy (playwright.config.ts). Every request that reaches it is recorded as its
    method and destination host:port, appended to `log_path` as one JSON line, and refused with 403.
    It opens no connection of its own. A connection that sends no request line within `read_timeout`
    seconds is counted, not recorded (it names no destination).
    """

    def __init__(self, log_path: Optional[Path] = None, read_timeout: float = 5.0) -> None:
        self.log_path = log_path
        self.read_timeout = read_timeout
        self.records: list[dict] = []
        self.idle_connections = 0
        self._lock = threading.Lock()
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> str:
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                owner._handle(self.request)

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return self.address

    def _handle(self, connection: socket.socket) -> None:
        connection.settimeout(self.read_timeout)
        data = b""
        try:
            while b"\r\n" not in data and len(data) < 8192:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data += chunk
        except OSError:
            pass
        if b"\r\n" not in data:
            with self._lock:
                self.idle_connections += 1
            return
        self._record(parse_proxy_request_line(data.split(b"\r\n", 1)[0]))
        try:
            connection.sendall(_REFUSAL)
        except OSError:
            pass

    def _record(self, record: dict) -> None:
        with self._lock:
            self.records.append(record)
            if self.log_path is not None:
                with open(self.log_path, "a", encoding="utf-8") as log:
                    log.write(json.dumps(record) + "\n")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=15)


# ── 4. Chromium net-log ───────────────────────────────────────────────────────

# Connection errors raised before anything is sent: the operating system had no route
NO_ROUTE_ERRORS = {-106: "ERR_INTERNET_DISCONNECTED", -109: "ERR_ADDRESS_UNREACHABLE", -138: "ERR_NETWORK_ACCESS_DENIED"}
# Chromium's IPv6 reachability check: a UDP "connect" (a route lookup, no packet) to Google Public DNS
IPV6_PROBE_HOST = "2001:4860:4860::8888"
NOT_AN_ORIGIN = {"", "not an origin"}


class NetLogUnreadable(Exception):
    pass


@dataclass
class NetLog:
    path: str
    constants: dict
    events: list
    truncated: bool


def load_netlog(path: Path) -> NetLog:
    """
    A Chromium net-log. Chromium writes its constants on the first line and one event per line, and a
    browser that did not close cleanly leaves the file without its end: every complete event before
    the cut is kept, and the log is marked truncated.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
        return NetLog(str(path), data["constants"], data.get("events", []), truncated=False)
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    lines = text.split("\n")
    try:
        constants = json.loads(lines[0].rstrip().rstrip(",") + "}")["constants"]
        if lines[1].strip() != '"events": [':
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError) as exc:
        raise NetLogUnreadable(f"{Path(path).name}: not a Chromium net-log") from exc
    events, complete = [], False
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("]"):
            complete = True
            break
        event = line.rstrip(",")
        closing = event.endswith("}]")          # the last event closes the list on its own line
        try:
            events.append(json.loads(event[:-1] if closing else event))
        except json.JSONDecodeError:
            break
        if closing:
            complete = True
            break
    return NetLog(str(path), constants, events, truncated=not complete)


def _split_endpoint(text: str) -> tuple[str, Optional[int]]:
    """'1.2.3.4:80', '[::1]:443', 'https://host:443', 'host:443' or 'host' → (host, port)."""
    text = text.split("://", 1)[1] if "://" in text else text
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        return host.lower(), int(rest[1:]) if rest[1:].isdigit() else None
    host, sep, port = text.rpartition(":")
    if sep and port.isdigit() and ":" not in host:
        return host.lower(), int(port)
    return text.lower(), None


def _is_loopback(host: str) -> bool:
    address = _address(host)
    return address is not None and address.is_loopback


@dataclass
class NetLogFindings:
    files: int = 0
    truncated_files: int = 0
    events: int = 0
    loopback_connections: int = 0
    loopback_requests: int = 0
    transmissions: list = field(default_factory=list)         # a packet left, or may have left, for a non-loopback address
    direct_attempts_blocked: list = field(default_factory=list)  # TCP outside the sinkhole, refused locally (no route)
    routes_available: list = field(default_factory=list)      # a UDP route lookup to a non-loopback address succeeded
    route_lookups_blocked: dict = field(default_factory=dict) # Chromium's IPv6 check, refused locally: address → count
    unexpected_udp: list = field(default_factory=list)        # other UDP sockets to non-loopback addresses (no packet)
    dns_queries: list = field(default_factory=list)           # Chromium's own DNS client: {"hostname", "transmitted"}
    resolutions_blocked: int = 0                              # names the resolver rules turned into ~NOTFOUND
    resolutions_unmapped: list = field(default_factory=list)  # names resolved outside the resolver rules
    requests: list = field(default_factory=list)              # URL requests to non-loopback hosts
    proxy_configs: list = field(default_factory=list)         # every effective proxy configuration


def classify_netlog(logs: Sequence[NetLog]) -> NetLogFindings:
    findings = NetLogFindings(files=len(logs))
    for log in logs:
        findings.truncated_files += log.truncated
        findings.events += len(log.events)
        names = {number: name for name, number in log.constants.get("logEventTypes", {}).items()}
        phases = log.constants.get("logEventPhase", {})
        begin, end = phases.get("PHASE_BEGIN", 1), phases.get("PHASE_END", 2)
        tcp_pending, udp_pending, udp_failed, udp_connected = {}, {}, {}, {}
        dns_hostnames, dns_sockets, udp_sent = {}, {}, set()
        for event in log.events:
            name = names.get(event.get("type"))
            params = event.get("params") or {}
            phase = event.get("phase")
            source = (event.get("source") or {}).get("id")
            if name == "TCP_CONNECT_ATTEMPT" and phase == begin:
                tcp_pending[source] = params.get("address", "")
            elif name == "TCP_CONNECT_ATTEMPT" and phase == end and source in tcp_pending:
                address, error = tcp_pending.pop(source), params.get("net_error")
                if _is_loopback(_split_endpoint(address)[0]):
                    findings.loopback_connections += error is None
                elif error is None:
                    findings.transmissions.append(f"TCP connection established to {address}")
                elif error in NO_ROUTE_ERRORS:
                    findings.direct_attempts_blocked.append(f"{address} ({NO_ROUTE_ERRORS[error]})")
                else:
                    findings.transmissions.append(f"TCP connection attempt to {address} failed with net error {error}: "
                                                  "packets may have left")
            elif name == "UDP_CONNECT" and phase == begin:
                udp_pending[source] = params.get("address", "")
            elif name == "UDP_CONNECT" and phase == end and source in udp_pending:
                address = udp_pending.pop(source)
                if not _is_loopback(_split_endpoint(address)[0]):
                    (udp_failed if params.get("net_error") is not None else udp_connected)[source] = address
            elif name == "UDP_BYTES_SENT":
                address = params.get("address", "")
                if not _is_loopback(_split_endpoint(address)[0]):
                    udp_sent.add(source)
                    findings.transmissions.append(f"{params.get('byte_count', '?')} bytes sent over UDP to {address}")
            elif name == "DNS_TRANSACTION" and phase == begin and "hostname" in params:
                dns_hostnames[source] = params["hostname"]
            elif name == "DNS_TRANSACTION_ATTEMPT":
                dependency = (params.get("source_dependency") or {}).get("id")
                if dependency is not None:
                    dns_sockets[dependency] = source
            elif name == "HOST_RESOLVER_MANAGER_REQUEST" and phase == begin and "host" in params:
                host = _split_endpoint(params["host"])[0]
                if host == "~notfound":
                    findings.resolutions_blocked += 1
                elif not _is_loopback(host):
                    findings.resolutions_unmapped.append(host)
            elif name == "URL_REQUEST_START_JOB" and phase == begin and "url" in params:
                target = urlsplit(params["url"])
                host = (target.hostname or "").lower()
                if _is_loopback(host):
                    findings.loopback_requests += 1
                elif host:
                    try:
                        port = target.port or _DEFAULT_PORTS.get(target.scheme)
                    except ValueError:
                        port = None
                    findings.requests.append({"host": host, "port": port, "method": params.get("method", ""),
                                              "page": str(params.get("initiator", "")) not in NOT_AN_ORIGIN})
            elif name == "PROXY_CONFIG_CHANGED":
                findings.proxy_configs.append(json.dumps(params.get("new_config"), sort_keys=True))
        for address in tcp_pending.values():          # the log ends before the attempt's outcome
            if not _is_loopback(_split_endpoint(address)[0]):
                findings.transmissions.append(f"TCP connection attempt to {address} with no logged outcome")
        for transaction, hostname in dns_hostnames.items():
            sockets = [s for s, t in dns_sockets.items() if t == transaction]
            findings.dns_queries.append({"hostname": hostname, "transmitted": any(s in udp_sent for s in sockets)})
        for socket_id, address in udp_failed.items():
            if socket_id in dns_sockets:
                continue                              # reported with its DNS query
            if _split_endpoint(address)[0] == IPV6_PROBE_HOST:
                findings.route_lookups_blocked[address] = findings.route_lookups_blocked.get(address, 0) + 1
            else:
                findings.unexpected_udp.append(address)
        for address in udp_connected.values():
            findings.routes_available.append(address)
    return findings


# ── 5. Verdict ────────────────────────────────────────────────────────────────

# Connection attempts that Playwright's Chromium (Chrome for Testing 153) makes on its own, observed
# behind a local sinkhole on 2026-10-19: network time, component updates, sign-in, device services,
# the search engine; and, in the first offline run (20261019-204043), Autofill's field-type
# predictions for the forms it sees. They are reported, not failed, only when the sinkhole refused
# them, the offline preflight succeeded, and no page of the run requested that host. Any other host,
# or one of these on another port, fails the run.
KNOWN_BROWSER_BACKGROUND = frozenset({
    ("clients2.google.com", 80),
    ("update.googleapis.com", 443),
    ("accounts.google.com", 443),
    ("www.google.com", 443),
    ("android.clients.google.com", 443),
    ("content-autofill.googleapis.com", 443),
})

# Background services observed with one method only. Autofill's predictions are GET requests; an
# upload of a submitted form's structure would be a POST, and fails the run like any other method.
BACKGROUND_METHODS = {
    ("content-autofill.googleapis.com", 443): frozenset({"GET"}),
}


def expected_proxy_config(sinkhole_address: str) -> str:
    """The proxy configuration playwright.config.ts gives Chromium, as Chromium's net-log states it."""
    return json.dumps({"bypass_list": ["<-loopback>", "127.0.0.1"], "single_proxy": [f"[{sinkhole_address}]"]},
                      sort_keys=True)


@dataclass
class EvidenceVerdict:
    failures: list
    report: dict

    @property
    def passed(self) -> bool:
        return not self.failures


def _count(counter: dict, key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def evaluate_evidence(*, sinkhole_address: str, sinkhole_records: Sequence[dict], idle_connections: int,
                      netlogs: Sequence[NetLog], unreadable_netlogs: Sequence[str], offline_verified: bool,
                      probes_allowed: bool, required_probes: Sequence[tuple] = ()) -> EvidenceVerdict:
    """
    What one Playwright invocation's evidence means. Three kinds of event are kept apart: an attempt
    refused inside this computer (the sinkhole, the resolver rules, no route), a DNS query outside
    the resolver rules, and a packet that left. Only refused attempts of known Chromium background
    services and the declared positive controls pass; everything else fails, with its own reason.
    """
    failures: list[str] = []
    if not offline_verified:
        failures.append("the offline preflight did not succeed: no browser attempt can be counted as blocked")
    if not netlogs:
        failures.append("no Chromium net-log was written: what the browser attempted cannot be assessed")
    failures += [f"unreadable net-log {name}" for name in unreadable_netlogs]
    findings = classify_netlog(netlogs)
    failures += [f"outbound packet: {item}" for item in findings.transmissions]
    failures += [f"a route to {item} was available to Chromium" for item in findings.routes_available]
    failures += [f"direct connection attempt outside the sinkhole, refused locally (no route): {item}"
                 for item in findings.direct_attempts_blocked]
    for query in findings.dns_queries:
        failures.append(f"DNS query for {query['hostname']} outside the resolver rules: "
                        + ("transmitted" if query["transmitted"] else "attempted, no packet sent (no route)"))
    failures += [f"name resolved outside the resolver rules: {host}" for host in findings.resolutions_unmapped]
    failures += [f"unexpected UDP socket to {item} (no packet sent)" for item in findings.unexpected_udp]
    expected = expected_proxy_config(sinkhole_address)
    if netlogs and not findings.proxy_configs:
        failures.append("the net-log states no proxy configuration")
    failures += [f"unexpected proxy configuration {config}" for config in dict.fromkeys(findings.proxy_configs)
                 if config != expected]

    background: dict = {}
    deliberate: dict = {}
    page_hosts = {request["host"] for request in findings.requests if request["page"]}
    for request in findings.requests:
        key = f"{request['host']}:{request['port']}"
        if is_probe(request["host"]):
            if probes_allowed:
                _count(deliberate, key)
            else:
                failures.append(f"positive-control address requested outside the isolation preflight: {key}")
        elif request["page"]:
            failures.append(f"application-origin external request: {request['method']} {key}")
        elif (request["host"], request["port"]) not in KNOWN_BROWSER_BACKGROUND:
            failures.append(f"unknown browser-initiated external request: {request['method']} {key}")
        elif request["method"] not in BACKGROUND_METHODS.get((request["host"], request["port"]), {request["method"]}):
            observed = "/".join(sorted(BACKGROUND_METHODS[request["host"], request["port"]]))
            failures.append(f"browser-initiated {request['method']} to a known Chromium background host, "
                            f"observed with {observed} only: {key}")
    for record in sinkhole_records:
        if record.get("malformed"):
            failures.append(f"malformed request to the sinkhole (method {record.get('method') or '?'})")
            continue
        key = f"{record['host']}:{record['port']}"
        if is_probe(record["host"]):
            if probes_allowed:
                _count(deliberate, key)
            else:
                failures.append(f"positive-control address refused outside the isolation preflight: {key}")
        elif (record["host"], record["port"]) not in KNOWN_BROWSER_BACKGROUND:
            failures.append(f"external attempt refused by the sinkhole, not a known Chromium background service: "
                            f"{record['method']} {key}")
        elif record["host"] in page_hosts:
            failures.append(f"a page of the run requested a Chromium background host: {key}")
        elif offline_verified:
            _count(background, key)
    for host, port in required_probes:
        if not any(r.get("host") == host and (port is None or r.get("port") == port) for r in sinkhole_records):
            failures.append(f"positive control missing: the sinkhole has no record for {host}"
                            + (f":{port}" if port else ""))
    report = {
        "sinkhole": sinkhole_address,
        "browser_background_blocked": background,
        "deliberate_probes": deliberate,
        "idle_sinkhole_connections": idle_connections,
        "resolutions_blocked_by_rules": findings.resolutions_blocked,
        "route_lookups_refused_locally": findings.route_lookups_blocked,
        "loopback": {"connections": findings.loopback_connections, "requests": findings.loopback_requests},
        "netlogs": {"files": findings.files, "truncated": findings.truncated_files, "events": findings.events},
    }
    return EvidenceVerdict(failures=list(dict.fromkeys(failures)), report=report)


def compare_counters(before: Sequence[dict], after: Sequence[dict]) -> tuple[list[str], dict]:
    """Windows' sent counters of every adapter, before and after the run: any increase fails it."""
    first = {c["name"]: c for c in before}
    last = {c["name"]: c for c in after}
    failures, report = [], {}
    for name in sorted(first.keys() | last.keys()):
        if name not in first or name not in last:
            report[name] = f"not compared: available only at the {'start' if name in first else 'end'}"
            continue
        sent = last[name]["sentBytes"] - first[name]["sentBytes"]
        packets = last[name]["sentPackets"] - first[name]["sentPackets"]
        report[name] = {"sent_bytes": sent, "sent_packets": packets}
        if sent > 0 or packets > 0:
            failures.append(f"adapter {name} sent {sent} bytes in {packets} packets during the run (Windows counters)")
    return failures, report


def counter_evidence(report: dict) -> str:
    """
    What the counter comparison shows, said as it is. Windows keeps no statistics for a disabled or
    absent adapter, so on a disconnected computer there may be nothing to compare: that is no
    evidence that nothing was sent, and is reported as such (it does not fail the run).
    """
    compared = sorted(name for name, value in report.items() if isinstance(value, dict))
    if not compared:
        return ("no evidence: no adapter had sent counters at both the start and the end (Windows keeps none "
                "for a disabled or absent adapter); only the network-state snapshots show that nothing left")
    return f"compared at the start and the end for {len(compared)} adapter(s): {', '.join(_label(n) for n in compared)}"


# ── Processes ─────────────────────────────────────────────────────────────────


def kill_process_tree(pid: int) -> None:
    """Stops a process and every process it started (taskkill /T on Windows)."""
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run([str(taskkill), "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def browser_processes_command() -> list[str]:
    """Lists the running processes of Playwright's browsers (their executables under ms-playwright)."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$root = '{root.replace(chr(39), chr(39) * 2)}'\n"
        "$ids = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
        "$_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { [int]$_.ProcessId })\n"
        "[Console]::Out.WriteLine((ConvertTo-Json -InputObject $ids -Compress)); [Console]::Out.Flush()\n")
    return powershell_command(script)


def browser_processes(run=subprocess.run) -> list[int]:
    try:
        done = run(browser_processes_command(), capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        ids = json.loads(done.stdout.decode("ascii").strip() or "[]") if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NetworkStateUnavailable(f"the browser processes could not be listed ({type(exc).__name__})") from exc
    if not isinstance(ids, list) or not all(type(i) is int for i in ids):
        raise NetworkStateUnavailable("the browser processes could not be listed")
    return ids
