"""
Stage 10C-3B, T5-S — the outbound-network guard of the test process (tests/network_guard.py).

Positive controls, all inside `tripwired()`: the unguarded functions are tripwires there, so an
attempt the guard failed to stop would end at the tripwire, never at the operating system. Every
external attempt below is deliberate, declared with `deliberate_attempts()`, and must be refused
by the guard itself (no tripwire reached).

Coverage:
 1. The guard is installed on every path: DNS, synchronous and asynchronous connections
 2. External names are never resolved and external addresses never connected; loopback passes
 3. httpx with a real transport (what a removed fake transport falls back to), asynchronous and
    synchronous: refused before DNS
 4. A refusal the code under test swallows is still recorded, so its test fails
"""

import asyncio
import socket

import httpx
import pytest

from tests import network_guard
from tests.network_guard import ExternalNetworkBlocked, deliberate_attempts, tripwired

HORIZON = "horizon-testnet.stellar.org"
UNROUTABLE = "203.0.113.10"            # TEST-NET-3 (RFC 5737): documentation only, never routed


def test_the_guard_is_installed_on_every_path():
    assert network_guard.installed() == dict.fromkeys(network_guard.installed(), True)
    assert set(network_guard.installed()) == {"getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
                                              "connect", "connect_ex", "proactor_sock_connect", "selector_sock_connect"}
    assert getattr(socket.getaddrinfo, "network_guard", False) and getattr(socket.socket.connect, "network_guard", False)


@pytest.mark.parametrize("host, loopback", [
    (None, True), ("", True), ("localhost", True), ("LOCALHOST.", True), ("app.localhost", True),
    ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True), (b"127.0.0.1", True),
    (HORIZON, False), ("example.org", False), (UNROUTABLE, False), ("8.8.8.8", False), ("::ffff:8.8.8.8", False),
    ("localhost.example.org", False), ("0.0.0.0", False),
])
def test_loopback_classification(host, loopback):
    assert network_guard.is_loopback_host(host) is loopback


def test_external_names_are_never_resolved():
    with tripwired() as reached, deliberate_attempts() as seen:
        for lookup in (lambda: socket.getaddrinfo(HORIZON, 443), lambda: socket.gethostbyname(HORIZON),
                       lambda: socket.gethostbyname_ex(HORIZON), lambda: socket.gethostbyaddr(UNROUTABLE)):
            with pytest.raises(ExternalNetworkBlocked):
                lookup()
    assert reached == []                                       # the guard decided; nothing went further
    assert seen == [f"dns {HORIZON}"] * 3 + [f"dns {UNROUTABLE}"]


def test_loopback_lookups_are_let_through():
    with tripwired() as reached, deliberate_attempts() as seen:
        for host in ("127.0.0.1", "localhost", "::1"):
            with pytest.raises(AssertionError, match="reached the unguarded getaddrinfo"):
                socket.getaddrinfo(host, 80)                   # let through: to the tripwire, not the system
    assert reached == ["getaddrinfo"] * 3 and seen == []


def test_external_addresses_are_never_connected():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, tripwired() as reached, \
            deliberate_attempts() as seen:
        for connect in (sock.connect, sock.connect_ex):
            with pytest.raises(ExternalNetworkBlocked):
                connect((UNROUTABLE, 443))
        with pytest.raises(ExternalNetworkBlocked):
            sock.connect((HORIZON, 443))                       # a name here would be resolved by connect itself
    assert reached == [] and seen == [f"connect {UNROUTABLE}"] * 2 + [f"connect {HORIZON}"]


async def test_an_asynchronous_connection_is_refused_before_the_event_loop_connects():
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setblocking(False)
        with tripwired() as reached, deliberate_attempts() as seen:
            with pytest.raises(ExternalNetworkBlocked):
                await loop.sock_connect(sock, (UNROUTABLE, 443))
    assert reached == [] and seen == [f"connect {UNROUTABLE}"]


async def test_httpx_with_a_real_transport_is_refused_before_dns():
    """What a fake transport set to None falls back to: httpx's own AsyncHTTPTransport."""
    with tripwired() as reached, deliberate_attempts() as seen:
        async with httpx.AsyncClient(timeout=5) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(f"https://{HORIZON}/")
    assert reached == [] and seen == [f"dns {HORIZON}"]


def test_synchronous_httpx_is_refused_before_dns():
    with tripwired() as reached, deliberate_attempts() as seen:
        with httpx.Client(timeout=5) as client:
            with pytest.raises(httpx.ConnectError):
                client.get(f"https://{HORIZON}/transactions/{'0' * 64}")
    assert reached == [] and seen == [f"dns {HORIZON}"]


def test_a_swallowed_refusal_is_still_recorded():
    before = network_guard.blocked_total
    with tripwired() as reached, deliberate_attempts() as seen:
        try:
            socket.getaddrinfo("example.org", 80)
        except Exception:                                      # as code under test might do
            pass
    assert reached == [] and seen == ["dns example.org"]
    assert network_guard.blocked_total == before + 1          # undeclared, it would fail its test (conftest)


def test_a_loopback_connection_still_works():
    with socket.socket() as server, socket.socket() as client:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client.connect(server.getsockname())
        accepted, _ = server.accept()
        accepted.close()
