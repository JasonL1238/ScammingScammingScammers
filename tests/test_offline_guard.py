"""The offline guard guards itself.

Without this file the guard's presence and its absence are indistinguishable to
CI: a review removed ``autouse=True`` from both fixtures in ``conftest.py`` and
the whole suite stayed green with an identical tally. That is the false green
``docs/execution-log.md`` calls the dangerous one — a gate retired as proven when
it never bit.

Every assertion here corresponds to a bypass that was *demonstrated* against an
earlier version of the guard, not imagined. The bytes host in particular opened
real off-box TCP under a guard that believed it blocked everything, and it did so
because nothing was checking.
"""

from __future__ import annotations

import os
import socket

import pytest
from conftest import _LOOPBACK_PORTS, _POISON, _refusal, _sdk_http_bases


class TestCredentialsAreRefused:
    def test_an_ambient_key_does_not_survive_into_a_test(self) -> None:
        # The whole point: a developer with a funded key in their environment
        # must not be able to spend it by running the suite.
        assert os.environ["ANTHROPIC_API_KEY"] == _POISON

    @pytest.mark.parametrize(
        "name",
        [
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_PROFILE",
            "ANTHROPIC_FOUNDRY_API_KEY",
            "ANTHROPIC_WEBHOOK_SIGNING_KEY",
            "ANTHROPIC_FEDERATION_RULE_ID",
        ],
    )
    def test_every_other_credential_route_is_poisoned_too(self, name: str) -> None:
        # Poisoning only ANTHROPIC_API_KEY relies on a precedence rule that is a
        # fact about today's SDK rather than a guarantee.
        assert os.environ[name] == _POISON


class TestRoutingIsPinned:
    def test_the_base_url_is_pinned_not_merely_cleared(self) -> None:
        # Clearing it hands the destination to a profile's resolved_base_url,
        # which is next in precedence — so clearing is not neutral.
        assert os.environ["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1"

    @pytest.mark.parametrize(
        "name", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "https_proxy", "all_proxy"]
    )
    def test_no_proxy_survives(self, name: str) -> None:
        # Measured bypass: the SDK's client trusts the environment, so with a
        # proxy set the only socket it opens is to loopback — inside layer 3's
        # own carve-out — and the request still reaches the API.
        assert name not in os.environ

    @pytest.mark.parametrize("name", ["ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL"])
    def test_the_variant_base_urls_are_cleared(self, name: str) -> None:
        # These plus a loopback listener was a path past *every* layer: the
        # variants read their own URLs, ignore the poisoned credentials, and do
        # not inherit from the classes layer 4 used to patch.
        assert name not in os.environ


class TestTheSocketRuleFailsClosed:
    """Each case here walked past an earlier version of the guard."""

    @staticmethod
    def inet(host: object, port: int = 443) -> str | None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            return _refusal(sock, (host, port))

    def test_a_plain_off_box_address_is_refused(self) -> None:
        assert self.inet("1.1.1.1") is not None

    def test_a_bytes_host_is_refused(self) -> None:
        # The demonstrated escape. CPython accepts bytes for ordinary TCP, and
        # a guard that read "not a str" as "not a network connection" let
        # connect((b"1.1.1.1", 80)) open a real socket.
        assert self.inet(b"1.1.1.1") is not None

    def test_an_unresolved_hostname_is_refused(self) -> None:
        # Nothing can vouch for where a name points, so it is refused rather
        # than guessed at.
        assert self.inet("api.anthropic.com") is not None

    def test_a_hostname_that_merely_starts_with_127_is_refused(self) -> None:
        # A prefix test on the string accepted this and then resolved it off-box
        # inside the C call.
        assert self.inet("127.evil.example.com") is not None

    def test_an_undeclared_loopback_port_is_refused(self) -> None:
        # The narrow rule. All-loopback put the pinned base URL inside the
        # carve-out and left /etc/hosts plus a listener as a live escape.
        assert self.inet("127.0.0.1", 8080) is not None

    @pytest.mark.parametrize("port", sorted(_LOOPBACK_PORTS))
    def test_declared_local_service_ports_are_allowed(self, port: int) -> None:
        assert self.inet("127.0.0.1", port) is None

    @pytest.mark.parametrize("host", ["127.0.0.1", "::ffff:127.0.0.1"])
    def test_loopback_forms_are_recognised(self, host: str) -> None:
        # The v4-mapped form is what a dual-stack getaddrinfo can hand back; an
        # earlier version rejected it and would have failed the migration legs
        # with the guard's error rather than a connection error.
        assert self.inet(host, 5432) is None

    def test_a_unix_socket_is_left_alone(self) -> None:
        # The one carve-out, and it is keyed on the socket's family rather than
        # on the argument's shape — guessing the family from the argument is
        # exactly what cost the bytes-host escape.
        if not hasattr(socket, "AF_UNIX"):  # pragma: no cover - not on Windows
            pytest.skip("no AF_UNIX on this platform")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            assert _refusal(sock, "/tmp/some.sock") is None

    def test_a_live_connection_off_box_actually_raises(self) -> None:
        # End to end through the patched method, not just the predicate.
        with pytest.raises(RuntimeError, match="offline guard refused"):
            socket.create_connection(("api.anthropic.com", 443), timeout=5)


class TestTheSdkHttpLayerIsRefused:
    def test_the_patch_lands_on_the_shared_bases(self) -> None:
        bases = _sdk_http_bases()
        assert bases, "no SDK base classes found — layer 4 is patching nothing"
        for base in bases:
            for method in ("post", "request"):
                assert getattr(base, method).__name__ == "_refuse_sdk_request", (
                    f"{base.__name__}.{method} is unpatched"
                )

    @pytest.mark.parametrize(
        "class_name",
        ["Anthropic", "AsyncAnthropic", "AnthropicBedrock", "AnthropicVertex"],
    )
    def test_every_client_variant_inherits_the_refusal(self, class_name: str) -> None:
        # Patching Anthropic/AsyncAnthropic left these two covered by the socket
        # layer alone — and they are precisely the classes that read their own
        # base URLs and ignore the poisoned credentials.
        anthropic = pytest.importorskip("anthropic")
        client_cls = getattr(anthropic, class_name, None)
        if client_cls is None:  # pragma: no cover - SDK shape changed
            pytest.skip(f"{class_name} not exported by this SDK version")
        for method in ("post", "request"):
            assert getattr(client_cls, method).__name__ == "_refuse_sdk_request"

    def test_a_real_client_with_an_explicit_key_and_url_still_cannot_call(self) -> None:
        # Both credential and base URL passed as constructor arguments, for which
        # the SDK never consults the environment — so layers 1 and 2 are provably
        # no-ops here and layer 4 is what must stand.
        anthropic = pytest.importorskip("anthropic")
        client = anthropic.Anthropic(
            api_key="explicit-literal-never-read-from-env",
            base_url="https://api.anthropic.com",
        )
        with pytest.raises(RuntimeError, match="SDK's HTTP layer"):
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8,
                messages=[{"role": "user", "content": "hi"}],
            )
