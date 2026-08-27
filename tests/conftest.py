"""Path setup, the offline guard, and the fixtures every test module may need.

Makes ``from helpers import ...`` work whatever import mode pytest is run in —
the ``sys.path.insert`` below is the mechanism. Without it the import resolves
only under the default ``prepend`` mode (which happens to put this directory on
``sys.path``); under ``--import-mode=importlib``, or with a ``tests/__init__.py``,
the failure would be an ImportError at collection — the whole suite, not one test.

**The offline guard.** README promises the suite needs no API keys and no
network. That was a convention held up by nobody: nothing here stopped a test
from reaching a real endpoint against an ambient key, and the first component
that will want to is the model-backed monitor. Four layers, composed because a
review demonstrated a live escape past each one alone:

1. **Poisoned credentials** — every environment variable the installed SDK
   resolves a credential from, including the Foundry and webhook keys and the
   Bedrock/Vertex base URLs. Being environment-level, this layer and layer 2 are
   the only ones that inherit into the subprocesses ``test_migration_runner.py``
   spawns.
2. **A pinned base URL and no proxies** — ``ANTHROPIC_BASE_URL`` is *pinned*, not
   cleared: clearing it hands the destination to a profile's own
   ``resolved_base_url``, which is next in precedence. The proxy variables matter
   because the SDK's HTTP client trusts the environment, and with one set the
   only socket it opens is to the proxy — often on loopback, which layer 3 must
   partially allow. That is a measured bypass, not a theoretical one.
3. **No sockets except declared local services** — see ``_LOOPBACK_PORTS``. An
   earlier version allowed *all* loopback, which put layer 2's pinned URL inside
   layer 3's own carve-out and left ``/etc/hosts`` plus a listener as a live
   escape.
4. **The SDK's HTTP layer refuses to run** — patched on ``SyncAPIClient`` /
   ``AsyncAPIClient``, the base every client variant inherits. Aimed there rather
   than at ``Anthropic``/``AsyncAnthropic`` because Bedrock, Vertex and Foundry
   do *not* inherit from those, and aimed at ``post``/``request`` rather than
   ``__init__`` because constructing a client opens no socket and production
   constructs one during call setup — refusing construction failed six honest
   tests that only inspect the request shape.

**Fail closed.** Anything this file cannot positively identify as a permitted
local destination is refused. The reverse policy is what let a ``bytes`` host —
which CPython accepts for ordinary TCP — walk straight through an earlier
version and open real off-box TCP.

**Scope limits, stated rather than left to be discovered.** This guard does not
cover: ``_socket.socket`` used directly (the C base, beneath the patched Python
wrapper); connections made by subprocesses, which inherit layers 1 and 2 but not
3 or 4; and UDP ``sendto``, which needs no ``connect``. DNS is deliberately not
blocked — resolving a name costs nothing and reaches nobody; the connection is
what spends money, and that is what layer 3 refuses.

``pytest-socket`` was considered and rejected: it implements layer 3 only, has no
port-scoped allow-rule, and cannot express layers 1, 2 or 4 — a new dependency
for a fraction of this.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


import pytest
import yaml
from helpers import UNSERVABLE_BUNDLE

#: Env vars the Anthropic SDK resolves a credential from, in precedence order.
_CREDENTIAL_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_WEBHOOK_SIGNING_KEY",
)

#: Anything that redirects a request without touching a credential. The Bedrock
#: and Vertex base URLs are here because those clients read their own: with one
#: pointed at a loopback forwarder and layer 3 allowing all loopback, a review
#: found a path past every single layer.
_ROUTING_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

_POISON = "poison-not-a-real-key-see-tests-conftest"

#: Where a request would go if one were somehow built. Port 1 is unbindable
#: without root, so this is a dead end by construction rather than by hope.
_PINNED_BASE_URL = "http://127.0.0.1:1"

#: The only loopback ports a test may reach. Static on purpose: sourcing this
#: from ``MIGRATIONS_TEST_DATABASE_URL`` would let the environment being guarded
#: against widen the guard, and would make the rule differ per CI leg.
_LOOPBACK_PORTS = frozenset(
    {
        5432,  # PostgreSQL, for the `migrations` leg's real database
        1,  # `test_migration_runner` dials this to assert a refusal, and
        #    `_PINNED_BASE_URL` points here. Unbindable without root, so
        #    allowing it grants nothing a stray dev proxy could occupy.
    }
)


def _refusal(sock: socket.socket, address: object) -> str | None:
    """Why ``address`` may not be reached, or ``None`` to allow it.

    Keyed on the socket's own ``family`` rather than on the shape of ``address``.
    Guessing the family from the argument is what cost an earlier version a live
    escape: ``AF_UNIX`` takes a path and ``AF_INET`` takes a tuple, so "not a
    ``(str, int)`` tuple" was read as "not a network connection" — and CPython
    accepts a ``bytes`` host for ordinary TCP.
    """
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        # AF_UNIX and friends are not network egress. The only carve-out.
        return None
    if not isinstance(address, tuple) or len(address) < 2:
        return f"unrecognised {sock.family.name} address {address!r}"

    host, port = address[0], address[1]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return f"unparseable {sock.family.name} host {host!r}"

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A hostname reaching connect() means it was never resolved here, so
        # nothing can vouch for where it points. Refused rather than guessed —
        # a prefix test on the string would accept "127.evil.example.com".
        return f"hostname {host!r} reached connect() unresolved"

    ip = getattr(ip, "ipv4_mapped", None) or ip  # ::ffff:127.0.0.1
    if not ip.is_loopback:
        return f"non-loopback address {host}"
    if port not in _LOOPBACK_PORTS:
        return f"loopback port {port} is not a declared local service"
    return None


@pytest.fixture(autouse=True, scope="session")
def _offline() -> Iterator[None]:
    """Refuse credentials, routing, and undeclared sockets for the whole session."""
    saved = {name: os.environ.get(name) for name in (*_CREDENTIAL_VARS, *_ROUTING_VARS)}
    for name in _CREDENTIAL_VARS:
        os.environ[name] = _POISON
    for name in _ROUTING_VARS:
        os.environ.pop(name, None)
    os.environ["ANTHROPIC_BASE_URL"] = _PINNED_BASE_URL

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guard(original: object) -> object:
        def blocked(self: socket.socket, address: object) -> object:
            reason = _refusal(self, address)
            if reason is None:
                return original(self, address)  # type: ignore[operator]
            message = (
                f"the offline guard refused a connection: {reason}. The suite runs "
                "offline by contract — see tests/conftest.py. If a test needs a "
                "service, fake it."
            )
            if "loopback port" in reason:
                # An undeclared *local* port is refused the way the OS would
                # refuse it, so callers that already handle a dead port keep
                # working — `python -m ssscammers.db` catches OSError and exits 1,
                # which is what `test_migration_runner` asserts.
                raise ConnectionRefusedError(message)
            # Everything else is loud on purpose. Returning an errno from
            # `connect_ex` here would be indistinguishable from a real refusal
            # and swallowed by every `except OSError` — the silent failure this
            # whole file exists to prevent.
            raise RuntimeError(message)

        return blocked

    socket.socket.connect = _guard(real_connect)  # type: ignore[method-assign]
    socket.socket.connect_ex = _guard(real_connect_ex)  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _sdk_http_bases() -> list[type]:
    """The SDK base classes every client variant inherits its HTTP layer from.

    Empty when the SDK is absent. That is not a supported install shape today —
    ``anthropic`` is a base dependency, not a media extra, so every CI leg has
    it — but importing it unconditionally would make this file the reason a
    dependency-shape change breaks collection rather than one test.
    """
    try:
        from anthropic import _base_client
    except ImportError:  # pragma: no cover - no shipped install shape omits the SDK
        return []
    return [
        cls
        for name in ("SyncAPIClient", "AsyncAPIClient")
        if isinstance(cls := getattr(_base_client, name, None), type)
    ]


@pytest.fixture(autouse=True)
def _no_real_anthropic_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real SDK client may be *built*; its HTTP layer may not *run*.

    Patched on the shared base classes rather than on ``Anthropic`` /
    ``AsyncAnthropic``: a review found that ``AnthropicBedrock`` and
    ``AnthropicVertex`` inherit from neither, so patching the two leaves left
    four client classes — the same four that read their own base URLs and ignore
    the poisoned credentials — covered by the socket layer alone.

    This is the only address-independent layer. Layer 3 can say "not that
    destination"; every bypass found while designing it was a different address
    reaching the same place. It also covers the one credential case env poisoning
    cannot: a call site passing a literal key, for which the SDK never consults
    the environment.

    **The refusal is unconditional, and there is deliberately no opt-in.** The
    roadmap sketched one; it was rejected on review. A marker is trivially
    reached for, and the case it would serve is already served better: inject
    ``RecordedAnthropicClient`` (``ssscammers.simscammer.replay``) through
    ``ClaudeBrain(client=...)``. That fake keeps every request the brain builds,
    so a test can assert on the real request surface with no HTTP stack at all.
    An ``httpx.MockTransport`` exemption was also considered and deferred — it
    would be self-enforcing rather than abusable, but detecting it needs two
    private httpx attributes, and a guard that breaks silently on a dependency
    upgrade is the failure mode this file exists to close.
    """
    for base in _sdk_http_bases():
        for method in ("post", "request"):
            if hasattr(base, method):
                monkeypatch.setattr(base, method, _refuse_sdk_request)


def _refuse_sdk_request(*_args: object, **_kwargs: object) -> None:
    """Deliberately says *HTTP layer*, not "the real API".

    A client wired to an ``httpx.MockTransport`` opens no socket and reaches
    nobody, and the previous wording asserted otherwise — a false statement in
    the exact case a monitor test is most likely to hit.
    """
    raise RuntimeError(
        "a test invoked the Anthropic SDK's HTTP layer. Nothing in this suite may. "
        "Inject a fake through ClaudeBrain(client=...) — "
        "ssscammers.simscammer.replay.RecordedAnthropicClient already implements "
        "that seam and keeps every request for assertion. See tests/conftest.py."
    )


@pytest.fixture
def unservable_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Install :data:`helpers.UNSERVABLE_BUNDLE` as the only persona on disk.

    Lives here rather than in ``helpers.py`` so pytest discovers it for every module —
    ``test_media.py`` and ``test_webhooks.py`` both had a verbatim copy of it.
    """
    directory = tmp_path / "unvoiceable"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "core.md").write_text("You are a test persona.\n", encoding="utf-8")
    (directory / "persona.yaml").write_text(yaml.safe_dump(UNSERVABLE_BUNDLE), encoding="utf-8")
    monkeypatch.setattr("ssscammers.agent.persona.PERSONA_DIR", tmp_path)
    return "unvoiceable"
