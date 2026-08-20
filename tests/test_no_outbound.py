"""G-1, checked statically: this system cannot originate contact.

The subaccount has outbound voice and SMS disabled, which is the real control. This is
the second lock — the one that catches the commit where somebody adds a "just call them
back" convenience and nothing else notices. It is static on purpose: it holds for code
that no other test ever executes, including code added later.

The rule is not "no Twilio API calls". Acting on a call the caller already opened —
starting its recording, reading its status — is fine and necessary. The rule is that
nothing may *create* a call or a message, and no TwiML may bridge the caller to a second
leg.

Two scanners, because there are two ways to write the violation and each scanner is blind
to the other's:

* **Text**, for TwiML written by hand — ``"<Dial>…"`` in a string literal.
* **AST**, for TwiML written through the SDK — ``connect.append(Dial(number=...))``
  never contains the substring ``<Dial``, so no amount of grepping finds it. This is the
  idiom :mod:`ssscammers.agent.twiml` actually uses, which makes it the likelier mistake.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "ssscammers"

#: Each pattern is a way to originate contact in raw text, with the reason it is banned.
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bcalls\.create\b", "Twilio SDK outbound call"),
    (r"\bmessages\.create\b", "Twilio SDK outbound SMS"),
    (r"/Calls\.json", "Twilio REST call creation"),
    (r"/Messages\.json", "Twilio REST message creation"),
    (r"<Dial\b", "hand-written TwiML that bridges a second leg"),
    (r"<Sms\b", "hand-written TwiML that sends a text"),
    (r"<Message\b", "hand-written TwiML that sends a message"),
)

#: TwiML nouns and verbs that reach a second party. Checked as *names* — constructed,
#: imported, or called as a method — so the SDK's object idiom cannot slip past.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {"Dial", "Sms", "Message", "Refer", "dial", "sms", "message", "refer"}
)


def source_files() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _documentation_lines(tree: ast.AST) -> set[int]:
    """Line numbers occupied by docstrings, including attribute docstrings.

    This project documents constants and dataclass fields with a bare string statement
    underneath them, and several modules explain in prose that they never emit
    ``<Dial>``. A guardrail that cannot tell an explanation from an implementation fails
    on its own documentation, and a guardrail that fails for the wrong reason gets
    deleted.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        # `body` is not always a statement list — on a lambda or a conditional
        # expression it is a single expression node.
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for child in body:
            if (
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
            ):
                lines.update(range(child.lineno, (child.end_lineno or child.lineno) + 1))
    return lines


def executable_lines(source: str) -> list[tuple[int, str]]:
    """``source``'s lines with comments and docstrings removed.

    Ordinary string literals are deliberately kept: TwiML written by hand rather than
    through the SDK would live in one, and that is exactly the case worth catching.
    """
    skip = _documentation_lines(ast.parse(source))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            skip.add(token.start[0])

    return [
        (number, line)
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in skip
    ]


def scan_text(source: str, pattern: str) -> list[int]:
    """Lines of ``source`` matching ``pattern``, ignoring documentation."""
    compiled = re.compile(pattern)
    return [number for number, line in executable_lines(source) if compiled.search(line)]


def scan_names(source: str) -> list[tuple[int, str]]:
    """Every use of a forbidden TwiML name: constructed, called, or imported."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("twilio"):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    found.append((node.lineno, f"imports {alias.name} from {node.module}"))
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            if name in FORBIDDEN_NAMES:
                found.append((node.lineno, f"calls {name}()"))
    return found


def test_there_are_source_files_to_check() -> None:
    # A static check that scans nothing passes for the wrong reason.
    files = source_files()
    assert len(files) >= 10, f"only found {len(files)} source files; is the path right?"


@pytest.mark.parametrize(("pattern", "reason"), FORBIDDEN_PATTERNS, ids=lambda v: str(v)[:40])
def test_no_source_file_contains_outbound_twiml(pattern: str, reason: str) -> None:
    offenders = []
    for path in source_files():
        for number in scan_text(path.read_text(encoding="utf-8"), pattern):
            offenders.append(f"{path.relative_to(PACKAGE_ROOT.parent)}:{number}")

    assert not offenders, f"G-1 violation ({reason}): " + ", ".join(offenders)


def test_no_source_file_constructs_an_outbound_verb() -> None:
    offenders = []
    for path in source_files():
        for number, what in scan_names(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(PACKAGE_ROOT.parent)}:{number}: {what}")

    assert not offenders, "G-1 violation (outbound TwiML verb): " + ", ".join(offenders)


class TestTheScannersActuallyWork:
    """A guard that cannot fail proves nothing.

    Each of these feeds the real scanner a violation written the way somebody would
    plausibly write it, and requires the scanner to find it. Without these, stripping
    comments and docstrings could regress to stripping everything and the whole file
    above would pass vacuously.
    """

    def test_the_text_scanner_catches_hand_written_twiml(self) -> None:
        source = 'def bridge():\n    return "<Response><Dial>+15555550100</Dial></Response>"\n'
        assert scan_text(source, r"<Dial\b") == [2]

    def test_the_text_scanner_catches_sdk_call_creation(self) -> None:
        source = "def ring_back(client, number):\n    client.calls.create(to=number)\n"
        assert scan_text(source, r"\bcalls\.create\b") == [2]

    def test_the_name_scanner_catches_the_sdk_object_idiom(self) -> None:
        # The one shape a text scan cannot see: the SDK renders <Dial>, the source never
        # contains the string. This is the idiom twiml.py itself uses for every verb.
        source = (
            "from twilio.twiml.voice_response import Connect, Dial\n"
            "def bridge(response):\n"
            "    connect = Connect()\n"
            "    connect.append(Dial(number='+15555550100'))\n"
            "    response.append(connect)\n"
        )
        found = dict(scan_names(source))
        assert 1 in found and "Dial" in found[1]
        assert 4 in found

    def test_the_name_scanner_catches_the_builder_method(self) -> None:
        source = "def bridge(response):\n    response.dial('+15555550100')\n"
        assert scan_names(source) == [(2, "calls dial()")]

    def test_documentation_is_not_mistaken_for_code(self) -> None:
        # The other failure mode: prose about <Dial> must not trip the scan, or the
        # guardrail gets deleted the first time someone documents it.
        source = '"""This module never emits <Dial>."""\n# and never <Sms> either\nX = 1\n'
        assert scan_text(source, r"<Dial\b") == []
        assert scan_text(source, r"<Sms\b") == []

    def test_a_violation_hidden_behind_a_docstring_on_one_line_is_still_found(self) -> None:
        # `_documentation_lines` skips whole line spans, so a statement sharing a physical
        # line with a docstring is invisible to the text scanner. The name scanner does not
        # work line-by-line and catches it anyway — which is why there are two.
        source = "def f(response):\n    \"\"\"doc\"\"\"; response.dial('+15555550100')\n"
        assert scan_text(source, r"\.dial\s*\(") == []
        assert scan_names(source) == [(2, "calls dial()")]


class TestTheOneEndpointWeDoPost:
    """The counterpart to the scanners: what the system *is* allowed to ask Twilio for."""

    def test_recording_acts_on_a_call_that_already_exists(self) -> None:
        from ssscammers.agent.webhooks import recording_endpoint

        # The call SID is a path segment, so this endpoint cannot be reached without a
        # call that already exists — which is precisely what makes it safe.
        assert recording_endpoint("AC123", "CA456").endswith("/Calls/CA456/Recordings.json")

    def test_it_is_not_the_call_or_message_creation_endpoint(self) -> None:
        from ssscammers.agent.webhooks import recording_endpoint

        url = recording_endpoint("AC123", "CA456")
        assert "/Calls.json" not in url
        assert "/Messages.json" not in url
