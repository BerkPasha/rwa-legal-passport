"""
Fail-closed outbound-network guard of the test process (Stage 10C-3B, T5-S).

tests/conftest.py installs it before anything else is imported. In this process, every DNS lookup
of a name that is not loopback, and every connection to an address that is not loopback, is
refused before the operating system is asked, and recorded. conftest then fails the test that
caused it, even when the code under test swallowed the error (a broad `except`). Loopback stays
allowed (127.0.0.0/8, ::1, localhost): the event loop's own socket pair and the local test
servers use it.

Covered:
- socket.getaddrinfo, gethostbyname, gethostbyname_ex, gethostbyaddr (DNS);
- socket.socket.connect and connect_ex (synchronous clients, socket.create_connection);
- sock_connect of asyncio's proactor and selector event loops (asyncio and anyio connections,
  so httpx and httpcore).

Not covered, by nature:
- child processes, which do not inherit it;
- code that calls the C modules directly (_socket, _overlapped);
- code that undoes these patches on purpose.

Only operating-system egress control covers those.
"""

import asyncio.proactor_events
import asyncio.selector_events
import ipaddress
import socket
import threading
from contextlib import contextmanager

_lock = threading.Lock()
attempts: list[str] = []          # refused attempts not yet attributed (conftest fails the test)
deliberate: list[str] = []        # refused attempts of the guard's own positive controls
blocked_total = 0
REAL: dict = {}                   # the unguarded functions; a test may replace them with tripwires


class ExternalNetworkBlocked(ConnectionRefusedError):
    """An outbound attempt refused before it reached the network."""


def is_loopback_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    host = str(host).strip().rstrip(".").lower()
    if host in ("", "localhost") or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _is_numeric(host) -> bool:
    """A literal address: resolving it asks no DNS server (its connection is checked separately)."""
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    try:
        ipaddress.ip_address(str(host).split("%", 1)[0])
        return True
    except ValueError:
        return False


def _text(host) -> str:
    """Hosts arrive as text or, from anyio, as IDNA bytes."""
    return bytes(host).decode("ascii", "replace") if isinstance(host, (bytes, bytearray)) else str(host)


def _refuse(what: str):
    global blocked_total
    with _lock:
        attempts.append(what)
        blocked_total += 1
    raise ExternalNetworkBlocked(f"outbound network blocked in tests: {what}")


def _getaddrinfo(host, port, *args, **kwargs):
    if not is_loopback_host(host) and not _is_numeric(host):
        _refuse(f"dns {_text(host)}")
    return REAL["getaddrinfo"](host, port, *args, **kwargs)


def _gethostbyname(host):
    if not is_loopback_host(host) and not _is_numeric(host):
        _refuse(f"dns {_text(host)}")
    return REAL["gethostbyname"](host)


def _gethostbyname_ex(host):
    if not is_loopback_host(host) and not _is_numeric(host):
        _refuse(f"dns {_text(host)}")
    return REAL["gethostbyname_ex"](host)


def _gethostbyaddr(host):
    if not is_loopback_host(host):                  # a reverse lookup asks DNS even for a literal address
        _refuse(f"dns {_text(host)}")
    return REAL["gethostbyaddr"](host)


def _target(sock, address):
    """The host of an internet address (None for a local socket, e.g. AF_UNIX)."""
    if getattr(sock, "family", None) in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
        return address[0]
    return None


def _connect(self, address):
    host = _target(self, address)
    if host is not None and not is_loopback_host(host):
        _refuse(f"connect {_text(host)}")
    return REAL["connect"](self, address)


def _connect_ex(self, address):
    host = _target(self, address)
    if host is not None and not is_loopback_host(host):
        _refuse(f"connect {_text(host)}")
    return REAL["connect_ex"](self, address)


def _sock_connect(real_key: str):
    async def sock_connect(self, sock, address):
        host = _target(sock, address)
        if host is not None and not is_loopback_host(host):
            _refuse(f"connect {_text(host)}")
        return await REAL[real_key](self, sock, address)
    sock_connect.__name__ = "sock_connect"
    sock_connect.network_guard = True
    return sock_connect


_PATCHES = {
    "getaddrinfo": (socket, "getaddrinfo", _getaddrinfo),
    "gethostbyname": (socket, "gethostbyname", _gethostbyname),
    "gethostbyname_ex": (socket, "gethostbyname_ex", _gethostbyname_ex),
    "gethostbyaddr": (socket, "gethostbyaddr", _gethostbyaddr),
    "connect": (socket.socket, "connect", _connect),
    "connect_ex": (socket.socket, "connect_ex", _connect_ex),
    "proactor_sock_connect": (asyncio.proactor_events.BaseProactorEventLoop, "sock_connect",
                              _sock_connect("proactor_sock_connect")),
    "selector_sock_connect": (asyncio.selector_events.BaseSelectorEventLoop, "sock_connect",
                              _sock_connect("selector_sock_connect")),
}
for _name, (_owner, _attribute, _guard) in _PATCHES.items():
    if _guard.__name__ != "sock_connect":
        _guard.network_guard = True


def install() -> None:
    """Idempotent: the originals are kept once, then replaced by the guards."""
    for name, (owner, attribute, guard) in _PATCHES.items():
        current = getattr(owner, attribute)
        if getattr(current, "network_guard", False):
            continue
        REAL[name] = current
        setattr(owner, attribute, guard)


def installed() -> dict[str, bool]:
    return {name: getattr(getattr(owner, attribute), "network_guard", False)
            for name, (owner, attribute, _) in _PATCHES.items()}


def take_attempts() -> list[str]:
    """The attempts recorded since the last call, removed from the record."""
    with _lock:
        taken = attempts[:]
        attempts.clear()
    return taken


@contextmanager
def tripwired():
    """
    Inside the block, the unguarded functions are replaced by tripwires: even an attempt a guard
    let through would stop there, before the operating system is asked. Yields the names of the
    tripwires reached (none, when the guards decide before them).
    """
    reached: list[str] = []
    saved = dict(REAL)

    def tripwire(name: str):
        if name.endswith("sock_connect"):
            async def wire(*args, **kwargs):
                reached.append(name)
                raise AssertionError(f"reached the unguarded {name}")
        else:
            def wire(*args, **kwargs):
                reached.append(name)
                raise AssertionError(f"reached the unguarded {name}")
        return wire

    REAL.update({name: tripwire(name) for name in saved})
    try:
        yield reached
    finally:
        REAL.clear()
        REAL.update(saved)


@contextmanager
def deliberate_attempts():
    """For the guard's positive controls: the attempts made inside are expected, and returned."""
    with _lock:
        start = len(attempts)
    seen: list[str] = []
    try:
        yield seen
    finally:
        with _lock:
            seen.extend(attempts[start:])
            del attempts[start:]
            deliberate.extend(seen)
