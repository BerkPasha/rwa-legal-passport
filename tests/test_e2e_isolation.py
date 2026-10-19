"""
Offline isolation of the real-browser tests (Stage 10C-3B; scripts/e2e_isolation.py, scripts/e2e_run.py).

No browser is started and nothing leaves this computer. Every network state, net-log and proxy request
here is synthetic: documentation addresses (192.0.2.0/24, 2001:db8::/32) and reserved names (.invalid,
.localhost) stand for external destinations. The sinkhole, the fake monitors and the fake processes run
on this computer only (the conftest network guard fails any test that tries otherwise).

 A. Offline preflight: offline, online, ambiguous and malformed network states
 B. Collection: PowerShell is only asked to read, and any failure makes the state unreadable
 C. Monitor: a state that changes, stalls, ends or cannot be read stops the run
 D. Sinkhole: it records the destination only, refuses every request, and connects nowhere
 E. Net-log: loopback traffic, refused attempts, DNS queries and transmitted packets are told apart
 F. Verdict: what fails the run, and what is only reported
 G. Adapter counters: an increase fails the run; none to compare is reported as no evidence
 H. Runner gate: nothing starts without --offline-host and a verified offline state
 I. The browser side agrees with these rules (playwright.config.ts, support.ts, the isolation project),
    and the operator script states what its exit code means
"""

import base64
import copy
import ctypes
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))            # e2e_run.py imports its sibling module by name

import e2e_isolation as iso  # noqa: E402


def _load_runner():
    spec = importlib.util.spec_from_file_location("e2e_run_under_test", SCRIPTS / "e2e_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()

# ── Synthetic network states ──────────────────────────────────────────────────


def offline_state() -> dict:
    """A laptop in airplane mode, as the snapshot reports it (modelled on this project's Windows 11 laptop)."""
    return {
        "interfaces": [
            {"ifIndex": 1, "alias": "Loopback Pseudo-Interface 1", "family": "IPv4", "state": "Connected"},
            {"ifIndex": 1, "alias": "Loopback Pseudo-Interface 1", "family": "IPv6", "state": "Connected"},
            {"ifIndex": 8, "alias": "Wi-Fi", "family": "IPv4", "state": "Disconnected"},
            {"ifIndex": 8, "alias": "Wi-Fi", "family": "IPv6", "state": "Disconnected"},
            {"ifIndex": 9, "alias": "Bluetooth Network Connection", "family": "IPv4", "state": "Disconnected"},
        ],
        "addresses": [
            {"ifIndex": 1, "address": "127.0.0.1", "state": "Preferred"},
            {"ifIndex": 1, "address": "::1", "state": "Preferred"},
            {"ifIndex": 8, "address": "169.254.10.20", "state": "Tentative"},
            {"ifIndex": 8, "address": "fe80::1234%8", "state": "Deprecated"},
        ],
        "routes": [
            {"ifIndex": 1, "prefix": "127.0.0.0/8", "nextHop": "0.0.0.0"},
            {"ifIndex": 1, "prefix": "::1/128", "nextHop": "::"},
            {"ifIndex": 8, "prefix": "224.0.0.0/4", "nextHop": "0.0.0.0"},
            {"ifIndex": 8, "prefix": "fe80::/64", "nextHop": "::"},
        ],
        "adapters": [
            {"ifIndex": 8, "name": "Wi-Fi", "status": "Disconnected", "hardware": True},
            {"ifIndex": 9, "name": "Bluetooth Network Connection", "status": "Disconnected", "hardware": False},
            {"ifIndex": 11, "name": "WAN Miniport (IP)", "status": "Up", "hardware": False},
            {"ifIndex": 16, "name": "Wi-Fi 4", "status": "Not Present", "hardware": True},
        ],
        "counters": [{"name": "Wi-Fi", "sentBytes": 31519001, "sentPackets": 47165}],
    }


def online_state() -> dict:
    """The same laptop on a Wi-Fi network (documentation addresses)."""
    state = offline_state()
    for interface in state["interfaces"]:
        if interface["ifIndex"] == 8:
            interface["state"] = "Connected"
    state["addresses"][2:] = [{"ifIndex": 8, "address": "192.0.2.15", "state": "Preferred"},
                              {"ifIndex": 8, "address": "2001:db8::15", "state": "Preferred"}]
    state["routes"].append({"ifIndex": 8, "prefix": "0.0.0.0/0", "nextHop": "192.0.2.1"})
    state["adapters"][0]["status"] = "Up"
    return state


def _line(state: dict) -> str:
    return json.dumps(state)


# ── A. Offline preflight ──────────────────────────────────────────────────────


def test_a_computer_in_airplane_mode_is_offline():
    assessment = iso.assess_network_state(offline_state())
    assert assessment.offline and assessment.problems == ()


def test_a_connected_computer_is_refused_with_every_reason():
    problems = iso.assess_network_state(online_state()).problems
    assert "interface 8 (Wi-Fi, IPv4) is Connected" in problems
    assert "default route 0.0.0.0/0 on interface 8" in problems
    assert "interface 8 holds a usable (Preferred) IPv4 address" in problems
    assert "hardware adapter 8 (Wi-Fi) is Up" in problems


def _with(change):
    state = offline_state()
    change(state)
    return state


@pytest.mark.parametrize("change, problem", [
    (lambda s: s["interfaces"][3].update(state="Connected"), "interface 8 (Wi-Fi, IPv6) is Connected"),
    (lambda s: s["interfaces"][4].update(state="Dormant"), "interface 9 (Bluetooth Network Connection, IPv4) is Dormant"),
    (lambda s: s["routes"].append({"ifIndex": 8, "prefix": "::/0", "nextHop": "::"}), "default route ::/0 on interface 8"),
    (lambda s: s["routes"].append({"ifIndex": 8, "prefix": "198.51.100.0/24", "nextHop": "192.0.2.1"}),
     "route 198.51.100.0/24 through a gateway on interface 8"),
    (lambda s: s["addresses"][2].update(state="Preferred"), "interface 8 holds a usable (Preferred) IPv4 address"),
    (lambda s: s["adapters"][0].update(status="Up"), "hardware adapter 8 (Wi-Fi) is Up"),
    (lambda s: s["adapters"][3].update(status="Testing"), "hardware adapter 16 (Wi-Fi 4) is Testing"),
    (lambda s: s["addresses"].__setitem__(slice(0, 2), []), "no loopback interface was found"),
    (lambda s: s["addresses"].append({"ifIndex": 1, "address": "192.0.2.99", "state": "Preferred"}),
     "the loopback interface 1 also holds a non-loopback address"),
    (lambda s: s["interfaces"][0].update(state="Disconnected"),
     "the loopback interface is not connected (IPv4): the run's servers would be unreachable"),
    (lambda s: s["addresses"].append({"ifIndex": 8, "address": "not-an-address", "state": "Preferred"}),
     "interface 8 holds an unreadable address"),
], ids=["interface-connected", "unknown-interface-state", "ipv6-default-route", "gateway-route", "usable-address",
        "hardware-adapter-up", "unknown-adapter-status", "no-loopback", "loopback-with-external-address",
        "loopback-down", "unreadable-address"])
def test_every_possible_path_out_or_ambiguity_refuses_the_run(change, problem):
    assessment = iso.assess_network_state(_with(change))
    assert not assessment.offline
    assert problem in assessment.problems


def test_a_garbled_interface_name_is_reported_in_ascii():
    garbled = "Bluetooth A{0} Ba{0}lant{0}s{0}".format(chr(0xFFFD))   # as Windows can deliver an alias
    state = _with(lambda s: s["interfaces"][4].update(alias=garbled, state="Connected"))
    problem = next(p for p in iso.assess_network_state(state).problems if p.startswith("interface 9"))
    assert problem == "interface 9 (Bluetooth A? Ba?lant?s?, IPv4) is Connected" and problem.isascii()


def test_virtual_adapters_without_a_connected_interface_and_on_link_routes_are_not_paths_out():
    state = offline_state()
    assert any(a["status"] == "Up" and not a["hardware"] for a in state["adapters"])          # WAN miniport
    assert any(r["ifIndex"] == 8 and r["nextHop"] == "0.0.0.0" for r in state["routes"])      # on-link, link down
    assert iso.assess_network_state(state).offline


# ── B. Collection ─────────────────────────────────────────────────────────────


def test_a_snapshot_line_round_trips():
    assert iso.parse_network_state(_line(offline_state()).encode("ascii")) == offline_state()


@pytest.mark.parametrize("raw", [
    "not json",
    "[]",
    bytes([0xC3, 0xA7]),                      # a non-ASCII character, UTF-8 encoded
    json.dumps({k: v for k, v in offline_state().items() if k != "routes"}),
    json.dumps({**offline_state(), "extra": []}),
    json.dumps(_with(lambda s: s["interfaces"][0].update(ifIndex=True))),
    json.dumps(_with(lambda s: s["interfaces"][0].update(ifIndex="1"))),
    json.dumps(_with(lambda s: s["routes"][0].update(metric=1))),
    json.dumps(_with(lambda s: s["counters"][0].pop("sentBytes"))),
    json.dumps(_with(lambda s: s.update(interfaces=[]))),
], ids=["text", "list", "non-ascii", "missing-section", "extra-section", "bool-index", "string-index", "extra-field",
        "missing-field", "no-interface"])
def test_an_unexpected_snapshot_makes_the_state_unreadable(raw):
    with pytest.raises(iso.NetworkStateUnavailable):
        iso.parse_network_state(raw)


@pytest.mark.parametrize("outcome", ["exit-code", "timeout", "os-error"])
def test_powershell_failing_makes_the_state_unreadable(outcome, monkeypatch):
    monkeypatch.setattr(iso, "powershell_command", lambda script: ["powershell.exe"])

    def run(command, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "os-error":
            raise OSError("cannot start")
        return subprocess.CompletedProcess(command, 1, b"", b"error")

    with pytest.raises(iso.NetworkStateUnavailable):
        iso.collect_network_state(run=run)


def test_a_successful_collection_is_parsed(monkeypatch):
    monkeypatch.setattr(iso, "powershell_command", lambda script: ["powershell.exe"])
    done = subprocess.CompletedProcess([], 0, _line(online_state()).encode("ascii") + b"\r\n", b"")
    assert iso.collect_network_state(run=lambda command, **kwargs: done) == online_state()


def test_the_state_cannot_be_read_off_windows(monkeypatch):
    monkeypatch.setattr(iso, "os", types.SimpleNamespace(name="posix", environ=os.environ, kill=os.kill))
    with pytest.raises(iso.NetworkStateUnavailable, match="Windows only"):
        iso.powershell_command("Get-NetRoute")


@pytest.mark.parametrize("script", [iso.SNAPSHOT_ONCE, iso.SNAPSHOT_WATCH])
def test_the_snapshot_script_only_reads_and_never_probes(script):
    words = set(script.replace("(", " ").replace(")", " ").replace("|", " ").split())
    cmdlets = {w for w in words if "-" in w and w[:1].isupper() and not w.startswith("-")}
    assert cmdlets <= {"Get-NetIPInterface", "Get-NetIPAddress", "Get-NetRoute", "Get-NetAdapter",
                       "Get-NetAdapterStatistics", "ForEach-Object", "ConvertTo-Json", "Get-RwaNetworkState",
                       "Start-Sleep"}, cmdlets
    for forbidden in ("Set-", "Enable-", "Disable-", "New-", "Remove-", "Restart-", "Test-NetConnection",
                      "Test-Connection", "Resolve-DnsName", "Invoke-WebRequest", "Invoke-RestMethod", "netsh", "ping"):
        assert forbidden not in script


@pytest.mark.skipif(os.name != "nt", reason="the network state is read on Windows only")
def test_powershell_is_taken_from_system32_and_given_the_script_encoded():
    command = iso.powershell_command(iso.SNAPSHOT_ONCE)
    assert Path(command[0]).parts[-4:] == ("System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert base64.b64decode(command[4]).decode("utf-16-le") == iso.SNAPSHOT_ONCE


@pytest.mark.skipif(os.name != "nt", reason="the network state is read on Windows only")
def test_this_computers_real_snapshot_is_readable():
    """Read-only and local: the snapshot of this computer has the expected shape (offline or not)."""
    state = iso.collect_network_state()
    assert "no loopback interface was found" not in iso.assess_network_state(state).problems


# ── C. Monitor ────────────────────────────────────────────────────────────────


def _printer(lines: list, then_sleep: float = 60.0) -> list:
    """A child process that prints snapshot lines, as the PowerShell watcher does."""
    script = "import time\n" + "".join(f"print({line!r}, flush=True)\ntime.sleep(0.05)\n" for line in lines) \
        + f"time.sleep({then_sleep})\n"
    return [sys.executable, "-c", script]


def _monitor(command, stale_after=10.0):
    reasons, called = [], threading.Event()

    def on_violation(reason):
        reasons.append(reason)
        called.set()

    monitor = iso.NetworkMonitor(on_violation, command=command, stale_after=stale_after)
    monitor.start()
    return monitor, reasons, called


def test_the_monitor_stops_the_run_when_the_computer_comes_back_online():
    monitor, reasons, called = _monitor(_printer([_line(offline_state())] * 3 + [_line(online_state())]))
    try:
        assert called.wait(15)
        assert monitor.snapshots == 4               # three offline, then the one that stopped the run
        assert "default route 0.0.0.0/0 on interface 8" in reasons[0]
    finally:
        monitor.stop()
    assert len(reasons) == 1


@pytest.mark.parametrize("command, stale_after, reason", [
    (_printer([_line(offline_state())]), 1.0, "no network state was read for 1 s"),
    (_printer([_line(offline_state())], then_sleep=0), 10.0, "the network-state reader stopped"),
    (_printer(["not json"]), 10.0, "not valid JSON"),
], ids=["stalled", "reader-ended", "unreadable"])
def test_the_monitor_stops_the_run_when_the_state_can_no_longer_be_read(command, stale_after, reason):
    monitor, reasons, called = _monitor(command, stale_after=stale_after)
    try:
        assert called.wait(15)
        assert reason in reasons[0]
    finally:
        monitor.stop()


def test_a_computer_that_stays_offline_is_not_stopped():
    monitor, reasons, called = _monitor(_printer([_line(offline_state())] * 5))
    deadline = time.monotonic() + 15
    while monitor.snapshots < 5 and time.monotonic() < deadline:
        time.sleep(0.05)
    monitor.stop()
    assert monitor.snapshots == 5 and reasons == [] and monitor.violation is None


# ── D. Sinkhole ───────────────────────────────────────────────────────────────


def _ask(address: str, payload: bytes) -> bytes:
    host, port = address.split(":")
    with socket.create_connection((host, int(port)), timeout=10) as connection:
        connection.sendall(payload)
        chunks = []
        while chunk := connection.recv(4096):
            chunks.append(chunk)
    return b"".join(chunks)


@pytest.fixture
def sinkhole(tmp_path):
    hole = iso.Sinkhole(tmp_path / "sinkhole.jsonl", read_timeout=0.5)
    hole.start()
    yield hole
    hole.stop()


def test_the_sinkhole_refuses_a_tunnel_and_records_its_destination_only(sinkhole):
    answer = _ask(sinkhole.address, b"CONNECT egress.isolation-probe.localhost:443 HTTP/1.1\r\n"
                                    b"Host: egress.isolation-probe.localhost:443\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 403 Forbidden\r\n") and b"X-E2E-Sinkhole: refused" in answer
    assert sinkhole.records == [{"method": "CONNECT", "host": "egress.isolation-probe.localhost", "port": 443}]
    assert sinkhole.address.startswith("127.0.0.1:")


def test_the_sinkhole_never_keeps_a_path_query_or_header(sinkhole):
    _ask(sinkhole.address, b"GET http://blocked.invalid/private/path?token=e2e-secret-value HTTP/1.1\r\n"
                           b"Authorization: Bearer e2e-secret-value\r\nX-API-Key: e2e-secret-value\r\n\r\n")
    assert sinkhole.records == [{"method": "GET", "host": "blocked.invalid", "port": 80}]
    log = sinkhole.log_path.read_text(encoding="utf-8")
    assert json.loads(log) == sinkhole.records[0]
    for kept in ("secret", "private", "token", "Authorization", "X-API-Key"):
        assert kept not in log


@pytest.mark.parametrize("line, record", [
    (b"CONNECT [2001:db8::1]:443 HTTP/1.1", {"method": "CONNECT", "host": "2001:db8::1", "port": 443}),
    (b"CONNECT 192.0.2.7:8443 HTTP/1.1", {"method": "CONNECT", "host": "192.0.2.7", "port": 8443}),
    (b"GET https://Blocked.Invalid/x HTTP/1.1", {"method": "GET", "host": "blocked.invalid", "port": 443}),
    (b"GET ws://ws.isolation-probe.localhost/x HTTP/1.1", {"method": "GET", "host": "ws.isolation-probe.localhost", "port": 80}),
    (b"POST http://127.0.0.2:5555/x HTTP/1.1", {"method": "POST", "host": "127.0.0.2", "port": 5555}),
    (b"GET http://user:pw@blocked.invalid:81/ HTTP/1.1", {"method": "GET", "host": "blocked.invalid", "port": 81}),
    (b"GET / HTTP/1.1", {"method": "GET", "malformed": True}),
    (b"GET ftp://blocked.invalid/ HTTP/1.1", {"method": "GET", "malformed": True}),
    (b"CONNECT blocked.invalid HTTP/1.1", {"method": "CONNECT", "malformed": True}),
    (b"CONNECT bad host:443 HTTP/1.1", {"method": "CONNECT", "malformed": True}),
    (b"\x16\x03\x01 binary", {"method": "", "malformed": True}),
], ids=["ipv6-tunnel", "ip-tunnel", "absolute-https", "websocket", "ip-literal", "userinfo", "origin-form",
        "unknown-scheme", "no-port", "bad-host", "binary"])
def test_proxy_request_lines_are_reduced_to_their_destination(line, record):
    assert iso.parse_proxy_request_line(line) == record


def test_a_connection_without_a_request_line_is_counted_not_recorded(sinkhole):
    host, port = sinkhole.address.split(":")
    with socket.create_connection((host, int(port)), timeout=10):
        pass
    with socket.create_connection((host, int(port)), timeout=10) as connection:
        connection.sendall(b"\x16\x03\x01\x02\x00")     # a TLS hello sent to the proxy directly: no request line
        time.sleep(1.0)
    deadline = time.monotonic() + 10
    while sinkhole.idle_connections < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sinkhole.idle_connections == 2 and sinkhole.records == []


# ── E. Net-log ────────────────────────────────────────────────────────────────

_TYPES = {"TCP_CONNECT_ATTEMPT": 52, "UDP_CONNECT": 95, "UDP_BYTES_SENT": 98, "DNS_TRANSACTION": 483,
          "DNS_TRANSACTION_ATTEMPT": 485, "HOST_RESOLVER_MANAGER_REQUEST": 5, "URL_REQUEST_START_JOB": 135,
          "PROXY_CONFIG_CHANGED": 38}
BEGIN, END, NONE = 1, 2, 0
SINKHOLE = "127.0.0.1:50000"
PROXY = {"bypass_list": ["<-loopback>", "127.0.0.1"], "single_proxy": [f"[{SINKHOLE}]"]}


def _event(name, phase, source, **params):
    return {"params": params, "phase": phase, "source": {"id": source, "type": 0}, "time": "1", "type": _TYPES[name]}


def _netlog_text(events, ending="complete") -> str:
    """The layout Chromium writes: constants on the first line, one event per line, the last closing the list."""
    constants = {"logEventTypes": _TYPES, "logEventPhase": {"PHASE_BEGIN": BEGIN, "PHASE_END": END, "PHASE_NONE": NONE}}
    lines = ['{"constants":' + json.dumps(constants) + ",", '"events": [']
    rendered = [json.dumps(e) for e in events]
    if ending == "complete":
        lines += [r + "," for r in rendered[:-1]] + [rendered[-1] + "],", '"polledData": {}', "}"]
    else:                                      # cut in the middle of the last event
        lines += [r + "," for r in rendered[:-1]] + [rendered[-1][:20]]
    return "\n".join(lines) + "\n"


def _netlog(tmp_path, events, name="netlog-1.json", ending="complete") -> iso.NetLog:
    path = tmp_path / name
    path.write_text(_netlog_text(events, ending), encoding="utf-8")
    return iso.load_netlog(path)


def _proxy_event(config=PROXY):
    return _event("PROXY_CONFIG_CHANGED", NONE, 1, new_config=config)


def test_a_complete_and_a_truncated_net_log_keep_every_complete_event(tmp_path):
    events = [_proxy_event(), _event("TCP_CONNECT_ATTEMPT", BEGIN, 2, address=SINKHOLE), _event("TCP_CONNECT_ATTEMPT", END, 2)]
    complete = _netlog(tmp_path, events)
    assert (complete.truncated, len(complete.events)) == (False, 3)
    cut = _netlog(tmp_path, events, name="netlog-2.json", ending="cut")
    assert (cut.truncated, len(cut.events)) == (True, 2)


def test_a_net_log_whose_list_is_closed_but_whose_end_is_missing_is_complete(tmp_path):
    text = _netlog_text([_proxy_event()]).replace('"polledData": {}\n}\n', '"polledData": {"cut')
    path = tmp_path / "netlog-3.json"
    path.write_text(text, encoding="utf-8")
    log = iso.load_netlog(path)
    assert (log.truncated, len(log.events)) == (False, 1)


def test_a_file_that_is_no_net_log_is_unreadable(tmp_path):
    path = tmp_path / "netlog-4.json"
    path.write_text("garbage\n", encoding="utf-8")
    with pytest.raises(iso.NetLogUnreadable):
        iso.load_netlog(path)


def test_loopback_connections_and_requests_are_counted_as_legitimate(tmp_path):
    findings = iso.classify_netlog([_netlog(tmp_path, [
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 2, address="127.0.0.1:41000"), _event("TCP_CONNECT_ATTEMPT", END, 2),
        _event("URL_REQUEST_START_JOB", BEGIN, 3, url="http://127.0.0.1:41000/assets", initiator="http://127.0.0.1:41001",
               method="GET"),
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 4, host="http://127.0.0.1:41000"),
    ])])
    assert (findings.loopback_connections, findings.loopback_requests) == (1, 1)
    assert findings.transmissions == findings.requests == findings.resolutions_unmapped == []


def test_refused_attempts_dns_queries_and_transmitted_packets_are_told_apart(tmp_path):
    findings = iso.classify_netlog([_netlog(tmp_path, [
        # a direct TCP attempt refused locally, one established, one with no logged outcome
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 10, address="192.0.2.80:443"), _event("TCP_CONNECT_ATTEMPT", END, 10, net_error=-109),
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 11, address="192.0.2.81:443"), _event("TCP_CONNECT_ATTEMPT", END, 11),
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 12, address="192.0.2.82:443"), _event("TCP_CONNECT_ATTEMPT", END, 12, net_error=-102),
        # Chromium's own DNS client: one query that could not leave, one that did (the incident's shape)
        _event("DNS_TRANSACTION", BEGIN, 20, hostname="blocked.invalid", query_type=1),
        _event("DNS_TRANSACTION_ATTEMPT", NONE, 20, source_dependency={"id": 21, "type": 24}),
        _event("UDP_CONNECT", BEGIN, 21, address="192.0.2.53:53"), _event("UDP_CONNECT", END, 21, net_error=-109),
        _event("DNS_TRANSACTION", BEGIN, 22, hostname="sent.invalid", query_type=1),
        _event("DNS_TRANSACTION_ATTEMPT", NONE, 22, source_dependency={"id": 23, "type": 24}),
        _event("UDP_CONNECT", BEGIN, 23, address="192.0.2.54:53"), _event("UDP_CONNECT", END, 23),
        _event("UDP_BYTES_SENT", NONE, 23, address="192.0.2.54:53", byte_count=28),
        # route lookups: Chromium's IPv6 check refused locally, another refused, one that found a route
        _event("UDP_CONNECT", BEGIN, 30, address="[2001:4860:4860::8888]:443"), _event("UDP_CONNECT", END, 30, net_error=-109),
        _event("UDP_CONNECT", BEGIN, 31, address="[2001:db8::31]:3478"), _event("UDP_CONNECT", END, 31, net_error=-109),
        _event("UDP_CONNECT", BEGIN, 32, address="192.0.2.32:443"), _event("UDP_CONNECT", END, 32),
        # resolver requests: mapped by the rules, and one outside them
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 40, host="https://~notfound"),
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 41, host="blocked.invalid:443"),
    ])])
    assert findings.direct_attempts_blocked == ["192.0.2.80:443 (ERR_ADDRESS_UNREACHABLE)"]
    assert findings.transmissions == [
        "TCP connection established to 192.0.2.81:443",
        "TCP connection attempt to 192.0.2.82:443 failed with net error -102: packets may have left",
        "28 bytes sent over UDP to 192.0.2.54:53",
    ]
    assert findings.dns_queries == [{"hostname": "blocked.invalid", "transmitted": False},
                                    {"hostname": "sent.invalid", "transmitted": True}]
    assert findings.route_lookups_blocked == {"[2001:4860:4860::8888]:443": 1}
    assert findings.unexpected_udp == ["[2001:db8::31]:3478"]
    assert "192.0.2.32:443" in findings.routes_available
    assert (findings.resolutions_blocked, findings.resolutions_unmapped) == (1, ["blocked.invalid"])


def test_a_tcp_attempt_cut_off_by_a_truncated_log_counts_as_a_possible_transmission(tmp_path):
    findings = iso.classify_netlog([_netlog(tmp_path, [_event("TCP_CONNECT_ATTEMPT", BEGIN, 2, address="192.0.2.9:443"),
                                                       _proxy_event()])])
    assert findings.transmissions == ["TCP connection attempt to 192.0.2.9:443 with no logged outcome"]


def test_requests_are_attributed_to_a_page_or_to_the_browser(tmp_path):
    findings = iso.classify_netlog([_netlog(tmp_path, [
        _event("URL_REQUEST_START_JOB", BEGIN, 2, url="https://www.google.com/async/x", initiator="not an origin", method="GET"),
        _event("URL_REQUEST_START_JOB", BEGIN, 3, url="http://blocked.invalid/a", initiator="http://127.0.0.1:41001", method="GET"),
        _event("URL_REQUEST_START_JOB", BEGIN, 4, url="wss://blocked.invalid/s", initiator="null", method="GET"),
    ])])
    assert findings.requests == [
        {"host": "www.google.com", "port": 443, "method": "GET", "page": False},
        {"host": "blocked.invalid", "port": 80, "method": "GET", "page": True},
        {"host": "blocked.invalid", "port": 443, "method": "GET", "page": True},   # an opaque origin is still a page
    ]


# ── F. Verdict ────────────────────────────────────────────────────────────────


def _verdict(tmp_path, events=(), records=(), *, offline=True, probes=False, required=(), netlogs=None, unreadable=()):
    logs = netlogs if netlogs is not None else [_netlog(tmp_path, [_proxy_event(), *events])]
    return iso.evaluate_evidence(sinkhole_address=SINKHOLE, sinkhole_records=list(records), idle_connections=0,
                                 netlogs=logs, unreadable_netlogs=list(unreadable), offline_verified=offline,
                                 probes_allowed=probes, required_probes=required)


BACKGROUND = [{"method": "CONNECT", "host": "www.google.com", "port": 443},
              {"method": "GET", "host": "clients2.google.com", "port": 80}]


def test_refused_known_background_attempts_are_reported_not_failed_when_offline(tmp_path):
    verdict = _verdict(tmp_path, [
        _event("URL_REQUEST_START_JOB", BEGIN, 2, url="http://clients2.google.com/time/1/current", initiator="not an origin",
               method="GET")], BACKGROUND)
    assert verdict.passed, verdict.failures
    assert verdict.report["browser_background_blocked"] == {"www.google.com:443": 1, "clients2.google.com:80": 1}


def test_known_background_attempts_are_not_excused_without_a_successful_preflight(tmp_path):
    verdict = _verdict(tmp_path, records=BACKGROUND, offline=False)
    assert "the offline preflight did not succeed: no browser attempt can be counted as blocked" in verdict.failures
    assert verdict.report["browser_background_blocked"] == {}


@pytest.mark.parametrize("record, failure", [
    ({"method": "CONNECT", "host": "stellar.invalid", "port": 443},
     "external attempt refused by the sinkhole, not a known Chromium background service: CONNECT stellar.invalid:443"),
    ({"method": "CONNECT", "host": "www.google.com", "port": 8443},
     "external attempt refused by the sinkhole, not a known Chromium background service: CONNECT www.google.com:8443"),
    ({"method": "GET", "malformed": True}, "malformed request to the sinkhole (method GET)"),
    ({"method": "CONNECT", "host": "egress.isolation-probe.localhost", "port": 443},
     "positive-control address refused outside the isolation preflight: egress.isolation-probe.localhost:443"),
], ids=["unknown-host", "known-host-other-port", "malformed", "probe-outside-preflight"])
def test_any_other_sinkhole_record_fails_the_run(tmp_path, record, failure):
    assert failure in _verdict(tmp_path, records=[record]).failures


def test_an_application_request_fails_even_to_a_known_background_host(tmp_path):
    verdict = _verdict(tmp_path, [
        _event("URL_REQUEST_START_JOB", BEGIN, 2, url="https://www.google.com/x", initiator="http://127.0.0.1:41001", method="GET"),
        _event("URL_REQUEST_START_JOB", BEGIN, 3, url="https://stellar.invalid/tx", initiator="http://127.0.0.1:41001", method="GET"),
        _event("URL_REQUEST_START_JOB", BEGIN, 4, url="https://unknown.invalid/x", initiator="not an origin", method="POST"),
    ], BACKGROUND[:1])
    assert "application-origin external request: GET www.google.com:443" in verdict.failures
    assert "a page of the run requested a Chromium background host: www.google.com:443" in verdict.failures
    assert "application-origin external request: GET stellar.invalid:443" in verdict.failures
    assert "unknown browser-initiated external request: POST unknown.invalid:443" in verdict.failures
    assert verdict.report["browser_background_blocked"] == {}


def test_dns_attempts_refused_attempts_and_transmissions_fail_with_distinct_reasons(tmp_path):
    verdict = _verdict(tmp_path, [
        _event("DNS_TRANSACTION", BEGIN, 20, hostname="blocked.invalid", query_type=1),
        _event("DNS_TRANSACTION_ATTEMPT", NONE, 20, source_dependency={"id": 21, "type": 24}),
        _event("UDP_CONNECT", BEGIN, 21, address="192.0.2.53:53"), _event("UDP_CONNECT", END, 21, net_error=-109),
        _event("DNS_TRANSACTION", BEGIN, 22, hostname="sent.invalid", query_type=1),
        _event("DNS_TRANSACTION_ATTEMPT", NONE, 22, source_dependency={"id": 23, "type": 24}),
        _event("UDP_CONNECT", BEGIN, 23, address="192.0.2.54:53"), _event("UDP_CONNECT", END, 23),
        _event("UDP_BYTES_SENT", NONE, 23, address="192.0.2.54:53", byte_count=28),
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 10, address="192.0.2.80:443"), _event("TCP_CONNECT_ATTEMPT", END, 10, net_error=-109),
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 41, host="unmapped.invalid:443"),
    ])
    failures = verdict.failures
    assert "DNS query for blocked.invalid outside the resolver rules: attempted, no packet sent (no route)" in failures
    assert "DNS query for sent.invalid outside the resolver rules: transmitted" in failures
    assert "outbound packet: 28 bytes sent over UDP to 192.0.2.54:53" in failures
    assert "direct connection attempt outside the sinkhole, refused locally (no route): 192.0.2.80:443 " \
           "(ERR_ADDRESS_UNREACHABLE)" in failures
    assert "name resolved outside the resolver rules: unmapped.invalid" in failures


def test_chromiums_ipv6_check_and_names_mapped_by_the_rules_are_reported_not_failed(tmp_path):
    verdict = _verdict(tmp_path, [
        _event("UDP_CONNECT", BEGIN, 30, address="[2001:4860:4860::8888]:443"), _event("UDP_CONNECT", END, 30, net_error=-109),
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 40, host="https://~notfound"),
    ])
    assert verdict.passed, verdict.failures
    assert verdict.report["route_lookups_refused_locally"] == {"[2001:4860:4860::8888]:443": 1}
    assert verdict.report["resolutions_blocked_by_rules"] == 1


@pytest.mark.parametrize("netlogs, unreadable, failure", [
    ([], (), "no Chromium net-log was written: what the browser attempted cannot be assessed"),
    (None, ("netlog-9.json: not a Chromium net-log",), "unreadable net-log netlog-9.json: not a Chromium net-log"),
], ids=["no-net-log", "unreadable-net-log"])
def test_missing_evidence_fails_the_run(tmp_path, netlogs, unreadable, failure):
    assert failure in _verdict(tmp_path, netlogs=netlogs, unreadable=unreadable).failures


def test_a_proxy_configuration_other_than_the_sinkhole_fails_the_run(tmp_path):
    other = {"auto_detect": True, "bypass_list": ["<-loopback>"]}
    verdict = iso.evaluate_evidence(sinkhole_address=SINKHOLE, sinkhole_records=[], idle_connections=0,
                                    netlogs=[_netlog(tmp_path, [_proxy_event(), _proxy_event(other)])],
                                    unreadable_netlogs=[], offline_verified=True, probes_allowed=False)
    assert f"unexpected proxy configuration {json.dumps(other, sort_keys=True)}" in verdict.failures
    silent = _verdict(tmp_path, netlogs=[_netlog(tmp_path, [_event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 2,
                                                                    host="https://~notfound")], name="netlog-5.json")])
    assert "the net-log states no proxy configuration" in silent.failures


def test_the_positive_controls_pass_only_in_the_preflight_and_only_when_all_are_recorded(tmp_path):
    records = [{"method": "GET", "host": "egress.isolation-probe.localhost", "port": 80},
               {"method": "CONNECT", "host": "egress-tls.isolation-probe.localhost", "port": 443},
               {"method": "CONNECT", "host": "ws.isolation-probe.localhost", "port": 80},
               {"method": "GET", "host": "127.0.0.2", "port": 51000}]
    passed = _verdict(tmp_path, records=records, probes=True, required=iso.REQUIRED_PROBES)
    assert passed.passed, passed.failures
    assert passed.report["deliberate_probes"]["127.0.0.2:51000"] == 1
    missing = _verdict(tmp_path, records=records[1:], probes=True, required=iso.REQUIRED_PROBES)
    assert missing.failures == ["positive control missing: the sinkhole has no record for egress.isolation-probe.localhost:80"]


def test_the_positive_controls_can_only_reach_this_computer():
    for host, _port in iso.REQUIRED_PROBES:
        assert iso.is_probe(host)
        assert host.endswith(".localhost") or iso._is_loopback(host)
    assert not iso.is_probe("isolation-probe.localhost.invalid") and not iso.is_probe("127.0.0.1")


def test_the_incident_net_log_shape_would_fail_the_run(tmp_path):
    """The 2026-10-19 experiment: Chromium's DNS error page queried a public resolver outside the rules."""
    verdict = _verdict(tmp_path, [
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 133, host="diagnosis.invalid:443"),
        _event("DNS_TRANSACTION", BEGIN, 137, hostname="diagnosis.invalid", query_type=1),
        _event("DNS_TRANSACTION_ATTEMPT", NONE, 137, source_dependency={"id": 139, "type": 24}),
        _event("UDP_CONNECT", BEGIN, 139, address="192.0.2.53:53"), _event("UDP_CONNECT", END, 139),
        _event("UDP_BYTES_SENT", NONE, 139, address="192.0.2.53:53", byte_count=28),
    ])
    assert not verdict.passed
    assert "DNS query for diagnosis.invalid outside the resolver rules: transmitted" in verdict.failures


AUTOFILL = {"method": "CONNECT", "host": "content-autofill.googleapis.com", "port": 443}


def _autofill(source=2, method="GET", initiator="not an origin", url="https://content-autofill.googleapis.com/v1/pages/x"):
    return _event("URL_REQUEST_START_JOB", BEGIN, source, url=url, initiator=initiator, method=method)


def test_the_first_offline_runs_autofill_attempts_are_reported_not_failed(tmp_path):
    """Offline run 20261019-204043: browser-initiated GETs, every one refused by the sinkhole."""
    records = [AUTOFILL, *BACKGROUND, AUTOFILL, {"method": "CONNECT", "host": "android.clients.google.com", "port": 443}]
    verdict = _verdict(tmp_path, [_autofill(2), _autofill(3)], records)
    assert verdict.passed, verdict.failures
    assert verdict.report["browser_background_blocked"] == {
        "content-autofill.googleapis.com:443": 2, "www.google.com:443": 1, "clients2.google.com:80": 1,
        "android.clients.google.com:443": 1}


@pytest.mark.parametrize("record", [
    {**AUTOFILL, "port": 80, "method": "GET"},
    {**AUTOFILL, "port": 8443},
    {**AUTOFILL, "host": "autofill.googleapis.com"},
    {**AUTOFILL, "host": "x.content-autofill.googleapis.com"},
    {**AUTOFILL, "host": "content-autofill.googleapis.com.invalid"},
    {**AUTOFILL, "host": "content-autofill.googleapis.co"},
], ids=["port-80", "port-8443", "parent-domain", "subdomain", "suffix-appended", "other-tld"])
def test_autofill_is_known_on_its_exact_host_and_port_only(tmp_path, record):
    verdict = _verdict(tmp_path, records=[record])
    assert verdict.failures == ["external attempt refused by the sinkhole, not a known Chromium background service: "
                                f"{record['method']} {record['host']}:{record['port']}"]
    assert verdict.report["browser_background_blocked"] == {}


def test_autofill_over_plain_http_is_an_unknown_request(tmp_path):
    verdict = _verdict(tmp_path, [_autofill(url="http://content-autofill.googleapis.com/v1/pages/x")])
    assert verdict.failures == ["unknown browser-initiated external request: GET content-autofill.googleapis.com:80"]


@pytest.mark.parametrize("method", ["POST", "PUT", "OPTIONS"])
def test_an_autofill_request_with_a_method_never_observed_fails(tmp_path, method):
    verdict = _verdict(tmp_path, [_autofill(method=method)], [AUTOFILL])
    assert verdict.failures == [f"browser-initiated {method} to a known Chromium background host, observed with GET "
                                "only: content-autofill.googleapis.com:443"]


def test_an_autofill_request_of_a_page_fails_and_is_not_excused(tmp_path):
    verdict = _verdict(tmp_path, [_autofill(initiator="http://127.0.0.1:41001")], [AUTOFILL])
    assert verdict.failures == ["application-origin external request: GET content-autofill.googleapis.com:443",
                                "a page of the run requested a Chromium background host: content-autofill.googleapis.com:443"]
    assert verdict.report["browser_background_blocked"] == {}


def test_autofill_attempts_are_not_excused_without_a_successful_preflight(tmp_path):
    verdict = _verdict(tmp_path, [_autofill()], [AUTOFILL], offline=False)
    assert verdict.failures == ["the offline preflight did not succeed: no browser attempt can be counted as blocked"]
    assert verdict.report["browser_background_blocked"] == {}


def test_a_known_background_host_never_excuses_traffic_that_left_or_bypassed_the_sinkhole(tmp_path):
    verdict = _verdict(tmp_path, [
        _autofill(),
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 10, address="192.0.2.81:443"), _event("TCP_CONNECT_ATTEMPT", END, 10),
        _event("TCP_CONNECT_ATTEMPT", BEGIN, 11, address="192.0.2.80:443"), _event("TCP_CONNECT_ATTEMPT", END, 11, net_error=-109),
        _event("DNS_TRANSACTION", BEGIN, 20, hostname="content-autofill.googleapis.com", query_type=1),
        _event("HOST_RESOLVER_MANAGER_REQUEST", BEGIN, 41, host="content-autofill.googleapis.com:443"),
    ], [AUTOFILL])
    assert "outbound packet: TCP connection established to 192.0.2.81:443" in verdict.failures
    assert "direct connection attempt outside the sinkhole, refused locally (no route): 192.0.2.80:443 " \
           "(ERR_ADDRESS_UNREACHABLE)" in verdict.failures
    assert any(f.startswith("DNS query for content-autofill.googleapis.com outside the resolver rules")
               for f in verdict.failures)
    assert "name resolved outside the resolver rules: content-autofill.googleapis.com" in verdict.failures
    assert not verdict.passed


def test_only_the_reviewed_background_services_are_known():
    assert iso.KNOWN_BROWSER_BACKGROUND == {
        ("clients2.google.com", 80), ("update.googleapis.com", 443), ("accounts.google.com", 443),
        ("www.google.com", 443), ("android.clients.google.com", 443), ("content-autofill.googleapis.com", 443)}
    assert iso.BACKGROUND_METHODS == {("content-autofill.googleapis.com", 443): {"GET"}}
    assert set(iso.BACKGROUND_METHODS) <= iso.KNOWN_BROWSER_BACKGROUND


# ── G. Adapter counters ───────────────────────────────────────────────────────


def test_adapter_counters_that_grew_fail_the_run_and_unchanged_ones_pass():
    before = [{"name": "Wi-Fi", "sentBytes": 100, "sentPackets": 5}, {"name": "Bluetooth", "sentBytes": 0, "sentPackets": 0}]
    after = [{"name": "Wi-Fi", "sentBytes": 100, "sentPackets": 5}, {"name": "Bluetooth", "sentBytes": 60, "sentPackets": 1}]
    failures, report = iso.compare_counters(before, after)
    assert failures == ["adapter Bluetooth sent 60 bytes in 1 packets during the run (Windows counters)"]
    assert report["Wi-Fi"] == {"sent_bytes": 0, "sent_packets": 0}
    failures, report = iso.compare_counters(before, before[:1])
    assert failures == [] and report["Bluetooth"] == "not compared: available only at the start"
    failures, report = iso.compare_counters(before[:1], before)
    assert failures == [] and report["Bluetooth"] == "not compared: available only at the end"


def test_counters_that_could_not_be_compared_are_reported_as_no_evidence():
    """Offline run 20261019-204043: every hardware adapter disabled or absent, so Windows reported no counters."""
    failures, report = iso.compare_counters([], [])
    assert (failures, report) == ([], {})
    assert iso.counter_evidence(report).startswith("no evidence: no adapter had sent counters at both the start and the end")
    only_once = iso.compare_counters([{"name": "Wi-Fi", "sentBytes": 1, "sentPackets": 1}], [])[1]
    assert iso.counter_evidence(only_once).startswith("no evidence:")
    compared = iso.compare_counters([{"name": "Wi-Fi", "sentBytes": 1, "sentPackets": 1}],
                                    [{"name": "Wi-Fi", "sentBytes": 1, "sentPackets": 1}])[1]
    assert iso.counter_evidence(compared) == "compared at the start and the end for 1 adapter(s): Wi-Fi"


# ── H. Runner gate ────────────────────────────────────────────────────────────


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """A runner whose every process start would fail the test, with a fresh gate and reports in tmp_path."""
    control = runner.RunControl()
    monkeypatch.setattr(runner, "CONTROL", control)
    monkeypatch.setattr(runner, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def no_process(*args, **kwargs):
        raise AssertionError("a process was started before the offline preflight succeeded")

    monkeypatch.setattr(runner.subprocess, "Popen", no_process)
    monkeypatch.setattr(runner, "browser_processes", lambda: [])
    return control


@pytest.mark.parametrize("argv, message", [
    ([], "start with --offline-host"),
    (["--offline-host", "--testnet-readonly"], "--testnet-readonly reads the live Stellar Testnet"),
], ids=["no-offline-flag", "testnet"])
def test_the_runner_refuses_without_explicit_offline_execution(gate, argv, message, capsys):
    assert runner.main(argv) == 2
    assert message in capsys.readouterr().err
    assert not gate.offline_verified


def test_the_runner_refuses_while_the_computer_is_online_and_starts_nothing(gate, monkeypatch, capsys):
    monkeypatch.setattr(runner, "collect_network_state", online_state)
    assert runner.main(["--offline-host", "--offline-wait", "0"]) == 2
    output = capsys.readouterr()
    assert "Read all of this before disconnecting" in output.out             # the instructions come first
    assert "did not go offline" in output.err and "nothing was started" in output.err
    assert not gate.offline_verified
    summary = json.loads(next((runner.REPORTS).glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["refused"] == "the computer did not go offline in time"
    assert "default route 0.0.0.0/0 on interface 8" in summary["last_problems"]


@pytest.mark.parametrize("processes, message", [
    (lambda: [4242], "Playwright browser processes are already running (1)"),
    (lambda: (_ for _ in ()).throw(iso.NetworkStateUnavailable("listing failed")), "listing failed"),
], ids=["browser-running", "unlistable"])
def test_the_runner_refuses_when_browser_processes_are_running_or_unknown(gate, monkeypatch, processes, message, capsys):
    monkeypatch.setattr(runner, "browser_processes", processes)
    assert runner.main(["--offline-host"]) == 2
    assert message in capsys.readouterr().err


def test_cancelling_while_waiting_for_offline_starts_nothing(gate, monkeypatch, capsys):
    def interrupted(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "wait_for_offline", interrupted)
    assert runner.main(["--offline-host"]) == 2
    assert "cancelled by the operator (Ctrl+C); nothing was started" in capsys.readouterr().err
    assert not gate.offline_verified


class FakeMonitor:
    """The network-state monitor's interface, without PowerShell; `latest` is the last one created."""
    latest = None

    def __init__(self, on_violation):
        self.on_violation, self.snapshots, self.violation = on_violation, 7, None
        FakeMonitor.latest = self

    def start(self):
        pass

    def stop(self):
        pass

    def violate(self, reason):
        self.violation = reason
        self.on_violation(reason)


@pytest.fixture
def offline_run(gate, monkeypatch, tmp_path):
    """A runner whose preflight finds the computer offline, with a fake monitor and fake environments."""
    monkeypatch.setattr(runner, "wait_for_offline", lambda seconds: (offline_state(), ""))
    monkeypatch.setattr(runner, "NetworkMonitor", FakeMonitor)
    monkeypatch.setattr(runner, "collect_network_state", offline_state)
    monkeypatch.setattr(runner, "DEMO_DATASET", tmp_path / "no-demo-dataset")
    monkeypatch.setattr(runner, "DEV_DATABASE", tmp_path / "no-dev.db")
    ran = []

    def environment(name, out, args):
        assert gate.offline_verified, "an environment ran before the gate opened"
        ran.append(name)
        return {"environment": name, "passed": True}

    monkeypatch.setattr(runner, "run_environment", environment)
    return ran


def test_the_gate_stays_closed_when_the_monitor_cannot_start(offline_run, gate, monkeypatch, capsys):
    class Broken(FakeMonitor):
        def start(self):
            raise iso.NetworkStateUnavailable("PowerShell is missing")

    monkeypatch.setattr(runner, "NetworkMonitor", Broken)
    assert runner.main(["--offline-host"]) == 2
    assert "the network-state monitor could not start (PowerShell is missing); nothing was started" in capsys.readouterr().err
    assert not gate.offline_verified and offline_run == []


def test_a_complete_offline_run_reports_its_evidence_and_says_when_to_reconnect(offline_run, capsys):
    assert runner.main(["--offline-host"]) == 0
    output = capsys.readouterr().out
    assert offline_run == ["synthetic", "showcase", "rehearsal"]
    assert output.index("Read all of this before disconnecting") < output.index("Starting the tests")
    assert "COMPLETE: every test process has stopped. You may now turn airplane mode off." in output
    summary = json.loads(next(runner.REPORTS.glob("*/summary.json")).read_text(encoding="utf-8"))
    isolation = summary["offline_isolation"]
    assert isolation["failures"] == [] and isolation["final_state"] == {"offline": True, "problems": []}
    assert isolation["processes_verified_stopped"] and isolation["monitor"] == {"snapshots": 7, "violation": None}
    assert isolation["adapter_sent_counters"] == {"Wi-Fi": {"sent_bytes": 0, "sent_packets": 0}}
    assert isolation["adapter_counter_evidence"] == "compared at the start and the end for 1 adapter(s): Wi-Fi"
    assert "cannot establish zero egress" in isolation["limits"]


def test_a_run_without_comparable_counters_says_so_and_does_not_fail_on_it(offline_run, monkeypatch):
    def without_counters():
        state = offline_state()
        state["counters"] = []
        return state

    monkeypatch.setattr(runner, "wait_for_offline", lambda seconds: (without_counters(), ""))
    monkeypatch.setattr(runner, "collect_network_state", without_counters)
    assert runner.main(["--offline-host"]) == 0
    isolation = json.loads(next(runner.REPORTS.glob("*/summary.json")).read_text(encoding="utf-8"))["offline_isolation"]
    assert isolation["adapter_sent_counters"] == {} and isolation["failures"] == []
    assert isolation["adapter_counter_evidence"].startswith("no evidence:")


def test_an_unreadable_final_state_compares_no_counters_and_fails_the_run(offline_run, monkeypatch):
    def unreadable():
        raise iso.NetworkStateUnavailable("PowerShell exited with code 1")

    monkeypatch.setattr(runner, "collect_network_state", unreadable)
    assert runner.main(["--offline-host"]) == 1
    isolation = json.loads(next(runner.REPORTS.glob("*/summary.json")).read_text(encoding="utf-8"))["offline_isolation"]
    assert isolation["adapter_counter_evidence"] == "not compared: the final network state could not be read"
    assert "the final network state could not be read: PowerShell exited with code 1" in isolation["failures"]


def test_a_run_stopped_by_the_monitor_fails_and_says_why(offline_run, gate, monkeypatch, capsys):
    def environment(name, out, args):
        FakeMonitor.latest.violate("interface 8 (Wi-Fi, IPv4) is Connected")
        return {"environment": name, "passed": False}

    monkeypatch.setattr(runner, "run_environment", environment)
    assert runner.main(["--offline-host"]) == 1
    output = capsys.readouterr().out
    assert "STOPPING THE RUN: interface 8 (Wi-Fi, IPv4) is Connected" in output and "RESULT: FAILED" in output
    summary = json.loads(next(runner.REPORTS.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["environments_not_run"] == ["showcase", "rehearsal"]
    assert "the network-state monitor stopped the run: interface 8 (Wi-Fi, IPv4) is Connected" \
        in summary["offline_isolation"]["failures"]


def test_counters_that_grew_during_the_run_fail_it(offline_run, monkeypatch):
    sent = offline_state()
    sent["counters"][0]["sentBytes"] += 512
    monkeypatch.setattr(runner, "collect_network_state", lambda: sent)
    assert runner.main(["--offline-host"]) == 1
    summary = json.loads(next(runner.REPORTS.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert "adapter Wi-Fi sent 512 bytes in 0 packets during the run (Windows counters)" in summary["offline_isolation"]["failures"]


def _clock():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep


def test_the_runner_waits_for_consecutive_offline_snapshots(capsys):
    states = iter([online_state(), offline_state(), offline_state(), online_state(),
                   offline_state(), offline_state(), offline_state()])
    clock, sleep = _clock()
    state, reason = runner.wait_for_offline(60, collect=lambda: next(states), sleep=sleep, clock=clock)
    assert state == offline_state() and reason == ""
    assert clock() == 6.0                     # the flap at the fourth snapshot started the count again
    assert "waiting, not offline" in capsys.readouterr().out


def test_an_unreadable_state_never_counts_as_offline():
    def collect():
        raise iso.NetworkStateUnavailable("PowerShell exited with code 1")

    clock, sleep = _clock()
    state, reason = runner.wait_for_offline(5, collect=collect, sleep=sleep, clock=clock)
    assert state is None and reason == "the network state cannot be read: PowerShell exited with code 1"


def test_nothing_starts_before_the_preflight_or_after_an_abort(gate):
    with pytest.raises(runner.Refused, match="offline preflight has not succeeded"):
        runner._run_playwright(["node"], {})
    stack = runner.Stack("synthetic", Path("."), Path("."), True)
    with pytest.raises(runner.Refused):
        stack._spawn("backend", ["python"], {}, Path("."))
    gate.offline_verified = True
    gate.aborted = "interface 8 (Wi-Fi, IPv4) is Connected"
    with pytest.raises(runner.Refused, match="the run was stopped"):
        runner._run_playwright(["node"], {})


def _alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return code.value == 259                                   # STILL_ACTIVE


@pytest.mark.skipif(os.name != "nt", reason="process trees are stopped with taskkill on Windows")
def test_an_abort_stops_every_registered_process_with_its_children(tmp_path, capsys):
    """Local processes only: a parent that starts a child, both sleeping."""
    child_pid = tmp_path / "child.pid"
    script = ("import subprocess, sys, time\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
              f"open({str(child_pid)!r}, 'w').write(str(child.pid))\n"
              "time.sleep(120)\n")
    parent = subprocess.Popen([sys.executable, "-c", script])
    control = runner.RunControl()
    control.register(parent)
    try:
        deadline = time.monotonic() + 30
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        grandchild = int(child_pid.read_text())
        assert _alive(grandchild)
        control.abort("interface 8 (Wi-Fi, IPv4) is Connected")
        assert parent.poll() is not None
        deadline = time.monotonic() + 15
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(grandchild)
        assert control.aborted == "interface 8 (Wi-Fi, IPv4) is Connected"
        assert "STOPPING THE RUN" in capsys.readouterr().out
    finally:
        if parent.poll() is None:
            parent.kill()


# ── I. The browser side ───────────────────────────────────────────────────────

FRONTEND = ROOT / "frontend"


def test_playwright_config_gives_chromium_exactly_the_proxy_the_verdict_expects():
    config = (FRONTEND / "playwright.config.ts").read_text(encoding="utf-8")
    args = config.split("args: [", 1)[1].split("],", 1)[0]
    expected = json.loads(iso.expected_proxy_config("127.0.0.1:1"))
    assert f"'--proxy-bypass-list={';'.join(expected['bypass_list'])}'" in args
    assert "`--proxy-server=http://${sinkhole}`" in args
    assert "'--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'" in args
    assert "--log-net-log=" in args and "--disable-features" not in args   # it would replace Playwright's list
    assert "serviceWorkers: 'block'" in config
    assert "throw new Error('No offline isolation" in config


def test_the_browser_side_uses_the_same_probe_names():
    support = (FRONTEND / "e2e" / "support.ts").read_text(encoding="utf-8")
    spec = (FRONTEND / "e2e" / "isolation" / "isolation.e2e.ts").read_text(encoding="utf-8")
    assert f"export const PROBE_SUFFIX = '{iso.PROBE_SUFFIX}'" in support
    for host, _port in iso.REQUIRED_PROBES:
        label = host.removesuffix(iso.PROBE_SUFFIX)
        assert (f"probe('{label}')" in spec) if host != label else ("127.0.0.2" in spec)
    assert "route.abort('blockedbyclient')" in support and "route.fulfill" not in support


OFFLINE_CMD = ROOT / "scripts" / "e2e_offline.cmd"


def test_the_operator_script_names_the_meaning_of_its_exit_code_not_a_count():
    data = OFFLINE_CMD.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")                         # cmd.exe needs CRLF
    text = data.decode("ascii")
    assert "scripts\\e2e_run.py --offline-host %*" in text
    assert "0 passed, 1 failed, 2 refused" not in text                      # read as test counts after the first run
    assert "not a count of tests" in text


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe")
@pytest.mark.parametrize("code, meaning", [
    (0, "Exit code 0: PASSED."),
    (1, "Exit code 1: FAILED - a test, a check or the isolation evidence failed."),
    (2, "Exit code 2: REFUSED - nothing was started."),
    (7, "Exit code 7: an unexpected exit code."),
])
def test_the_operator_script_legend_states_each_exit_code(tmp_path, code, meaning):
    """Only the legend lines run, after a stand-in for the runner's exit code: nothing else is started."""
    lines = OFFLINE_CMD.read_text(encoding="ascii").splitlines()
    legend = lines[lines.index('set "RESULT=%ERRORLEVEL%"') + 1:lines.index("pause")]
    script = tmp_path / "legend.cmd"
    script.write_bytes(("\r\n".join(["@echo off", "setlocal", f'set "RESULT={code}"', *legend]) + "\r\n").encode("ascii"))
    output = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"), "/d", "/c", str(script)],
                            capture_output=True, text=True, timeout=30, check=True).stdout
    assert meaning in output.splitlines()
    assert "not a count of tests: 0 means passed, 1 failed, 2 refused." in output
