"""Integration tests — run the actual milter over a real Unix socket.

Uses miltertest (MTA-side milter protocol client) to speak the Sendmail
milter protocol directly against a live AutorefMilter instance.
"""

import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass

import Milter
import miltertest
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from miltertest import constants as mc

from milter_autoref.config import Config
from milter_autoref.milter import AutorefMilter

_MAX_EXAMPLES = int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", 100))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def milter_socket(tmp_path_factory):
    """Start a real milter and yield the Unix socket path."""
    sock_path = str(tmp_path_factory.mktemp("milter") / "test.sock")
    cfg = Config(
        socket=sock_path,
        auth_only=True,
        dry_run=False,
        log_level=logging.WARNING,
        timeout=600,
        trim_references=True,
        max_references=8,
    )
    log = logging.getLogger("test_integration")

    Milter.factory = lambda: AutorefMilter(cfg, log)
    Milter.set_flags(Milter.CHGHDRS | Milter.ADDHDRS)

    thread = threading.Thread(
        target=Milter.runmilter,
        args=("test-milter", sock_path),
        kwargs={"timeout": 600},
        daemon=True,
    )
    thread.start()

    # Wait up to 2 s for the socket to appear.
    for _ in range(20):
        if os.path.exists(sock_path):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Milter socket did not appear within 2 s")

    yield sock_path

    Milter.stop()
    thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

OUTGOING_MACROS = {"{auth_type}": "PLAIN", "{auth_authen}": "user@example.com"}
INCOMING_MACROS = {}


def _open_connection(sock_path: str) -> miltertest.MilterConnection:
    """Open a Unix socket and perform option negotiation."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(sock_path)
    conn = miltertest.MilterConnection(sock)
    # protocol=0 tells the milter we don't support noreply, so @Milter.noreply
    # callbacks fall back to sending CONTINUE — miltertest's convenience methods
    # (send_ar, send_headers, send_body) can then work without special handling.
    # protocol=0: we don't offer noreply support, so @Milter.noreply callbacks
    # fall back to sending CONTINUE — miltertest's convenience methods work.
    # strict=False: pymilter requests NOHELO/NORCPT skip flags (0xa) even when
    # we offered 0; accept and clamp rather than raising.
    conn.optneg_mta(protocol=0, strict=False)
    return conn


def _drive(sock_path: str, headers: list, macros: dict) -> list:
    """Drive a milter through a full SMTP-phase sequence.

    Returns the list of (cmd, params) tuples from send_eom(), which
    includes any modification actions followed by the final disposition.
    """
    conn = _open_connection(sock_path)
    try:
        conn.send_macro(mc.SMFIC_CONNECT)
        conn.send_ar(mc.SMFIC_CONNECT, hostname="localhost", family=mc.SMFIA_INET, port=0, address="127.0.0.1")
        conn.send_macro(mc.SMFIC_MAIL, **macros)
        conn.send_ar(mc.SMFIC_MAIL, args=["<sender@example.com>"])
        if headers:
            conn.send_headers(headers)
        conn.send_ar(mc.SMFIC_EOH)
        conn.send_body("Test body")
        return conn.send_eom()
    finally:
        try:
            conn._send(mc.SMFIC_QUIT)
        except Exception:
            pass
        conn.sock.close()


def _modification_actions(eom_result: list) -> list:
    """Return only the modification actions from a send_eom() result,
    excluding the final disposition tuple."""
    return [
        (cmd, params)
        for cmd, params in eom_result
        if cmd in (mc.SMFIR_ADDHEADER, mc.SMFIR_CHGHEADER)
    ]


# ---------------------------------------------------------------------------
# Outgoing message tests
# ---------------------------------------------------------------------------


class TestIntegrationOutgoing:
    def test_adds_references_when_none_exist(self, milter_socket):
        result = _drive(
            milter_socket,
            headers=[("Message-ID", "<orig@example.com>")],
            macros=OUTGOING_MACROS,
        )
        actions = _modification_actions(result)
        assert len(actions) == 1
        cmd, params = actions[0]
        assert cmd == mc.SMFIR_ADDHEADER
        assert params["name"] == "References"
        assert params["value"] == "<orig@example.com>"

    def test_appends_to_existing_references(self, milter_socket):
        result = _drive(
            milter_socket,
            headers=[
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        actions = _modification_actions(result)
        assert len(actions) == 1
        cmd, params = actions[0]
        assert cmd == mc.SMFIR_CHGHEADER
        assert params["index"] == 1
        assert params["name"] == "References"
        assert params["value"] == "<earlier@example.com> <orig@example.com>"

    def test_idempotent_no_change(self, milter_socket):
        result = _drive(
            milter_socket,
            headers=[
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com> <orig@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        assert _modification_actions(result) == []

    def test_no_mutation_without_message_id(self, milter_socket):
        result = _drive(
            milter_socket,
            headers=[("Subject", "Hello")],
            macros=OUTGOING_MACROS,
        )
        assert _modification_actions(result) == []


# ---------------------------------------------------------------------------
# Incoming message tests
# ---------------------------------------------------------------------------


class TestIntegrationIncoming:
    def test_no_mutation_without_auth(self, milter_socket):
        result = _drive(
            milter_socket,
            headers=[
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com>"),
            ],
            macros=INCOMING_MACROS,
        )
        assert _modification_actions(result) == []


# ---------------------------------------------------------------------------
# Trimming (fixture uses max_references=8)
# ---------------------------------------------------------------------------


class TestIntegrationTrimming:
    def test_trims_when_over_max_references(self, milter_socket):
        """Send 12 existing tokens + Message-ID (13 total).
        Result should have exactly 8 tokens: thread root + last 7."""
        tokens = [f"<msg-{i}@example.com>" for i in range(12)]
        result = _drive(
            milter_socket,
            headers=[
                ("Message-ID", "<new@example.com>"),
                ("References", " ".join(tokens)),
            ],
            macros=OUTGOING_MACROS,
        )
        actions = _modification_actions(result)
        assert len(actions) == 1
        cmd, params = actions[0]
        assert cmd == mc.SMFIR_CHGHEADER
        found = _TOKEN_RE.findall(params["value"])
        assert len(found) == 8
        assert found[0] == "<msg-0@example.com>"
        assert found[-1] == "<new@example.com>"

    def test_no_trimming_when_under_limit(self, milter_socket):
        result = _drive(
            milter_socket,
            headers=[
                ("Message-ID", "<new@example.com>"),
                ("References", "<a@x.com> <b@x.com> <c@x.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        actions = _modification_actions(result)
        assert len(actions) == 1
        found = _TOKEN_RE.findall(actions[0][1]["value"])
        assert len(found) == 4
        assert found == ["<a@x.com>", "<b@x.com>", "<c@x.com>", "<new@example.com>"]

    def test_trimming_preserves_thread_root(self, milter_socket):
        """Thread root (first token) must always be preserved."""
        tokens = [f"<msg-{i}@example.com>" for i in range(12)]
        result = _drive(
            milter_socket,
            headers=[
                ("Message-ID", "<new@example.com>"),
                ("References", " ".join(tokens)),
            ],
            macros=OUTGOING_MACROS,
        )
        actions = _modification_actions(result)
        found = _TOKEN_RE.findall(actions[0][1]["value"])
        assert found[0] == "<msg-0@example.com>"


# ---------------------------------------------------------------------------
# Multiple messages on one connection
# ---------------------------------------------------------------------------


class TestIntegrationMultipleMessages:
    def test_second_message_state_reset(self, milter_socket):
        """State from the first message must not bleed into the second."""
        conn = _open_connection(milter_socket)
        try:
            # First message: outgoing, has a References header.
            conn.send_macro(mc.SMFIC_CONNECT)
            conn.send_ar(mc.SMFIC_CONNECT, hostname="localhost", family=mc.SMFIA_INET, port=0, address="127.0.0.1")
            conn.send_macro(mc.SMFIC_MAIL, **OUTGOING_MACROS)
            conn.send_ar(mc.SMFIC_MAIL, args=["<a@example.com>"])
            conn.send_headers([
                ("Message-ID", "<first@example.com>"),
                ("References", "<old@example.com>"),
            ])
            conn.send_ar(mc.SMFIC_EOH)
            conn.send_body("body")
            result1 = conn.send_eom()

            actions1 = _modification_actions(result1)
            assert len(actions1) == 1
            assert actions1[0][0] == mc.SMFIR_CHGHEADER

            # Second message on the same connection: no References header.
            conn.send_macro(mc.SMFIC_MAIL, **OUTGOING_MACROS)
            conn.send_ar(mc.SMFIC_MAIL, args=["<b@example.com>"])
            conn.send_headers([("Message-ID", "<second@example.com>")])
            conn.send_ar(mc.SMFIC_EOH)
            conn.send_body("body")
            result2 = conn.send_eom()

            actions2 = _modification_actions(result2)
            assert len(actions2) == 1
            cmd, params = actions2[0]
            # Must be addheader, not chgheader — no References on this message.
            assert cmd == mc.SMFIR_ADDHEADER
            assert params["value"] == "<second@example.com>"
        finally:
            try:
                conn._send(mc.SMFIC_QUIT)
            except Exception:
                pass
            conn.sock.close()


# ---------------------------------------------------------------------------
# Property-based testing
# ---------------------------------------------------------------------------

# Scenario types — each carries its own expected outcome as a docstring and
# field layout. No logic.py used as oracle; expectations are derived directly
# from the inputs we generated.


@dataclass
class OutgoingNoRefs:
    """Outgoing message, no existing References.
    Expected: SMFIR_ADDHEADER, value tokens == [message_id]."""
    message_id: str


@dataclass
class OutgoingAppendsToRefs:
    """Outgoing message, message_id NOT already in References.
    Expected: SMFIR_CHGHEADER index=1, value tokens == existing_tokens + [message_id]."""
    message_id: str
    existing_tokens: list


@dataclass
class OutgoingAlreadyPresent:
    """Outgoing message, message_id IS already in References.
    Expected: no modification."""
    message_id: str
    existing_tokens: list  # contains message_id


@dataclass
class OutgoingNoMessageId:
    """Outgoing message with no Message-ID header.
    Expected: no modification."""


@dataclass
class OutgoingAppendsWithTrimming:
    """Outgoing message with enough existing tokens to exceed max_references (8).
    Expected: SMFIR_CHGHEADER, result has exactly 8 tokens, first is thread root,
    last is message_id."""
    message_id: str
    existing_tokens: list  # len > 7, so total > 8 after append


@dataclass
class IncomingMessage:
    """Unauthenticated (incoming) message.
    Expected: no modification."""
    message_id: str


# Strategies

_mid = st.integers(min_value=0).map(lambda n: f"<msg-{n}@example.com>")


@st.composite
def _appends_scenario(draw):
    n = draw(st.integers(min_value=2, max_value=6))
    start = draw(st.integers(min_value=0))
    # Sequential offsets guarantee uniqueness by construction — no filter retries.
    tokens = [f"<msg-{start + i}@example.com>" for i in range(n)]
    return OutgoingAppendsToRefs(message_id=tokens[-1], existing_tokens=tokens[:-1])


@st.composite
def _appends_with_trimming_scenario(draw):
    n = draw(st.integers(min_value=9, max_value=15))
    start = draw(st.integers(min_value=0))
    tokens = [f"<msg-{start + i}@example.com>" for i in range(n)]
    return OutgoingAppendsWithTrimming(message_id=tokens[-1], existing_tokens=tokens[:-1])


@st.composite
def _idempotent_scenario(draw):
    n = draw(st.integers(min_value=1, max_value=5))
    start = draw(st.integers(min_value=0))
    tokens = [f"<msg-{start + i}@example.com>" for i in range(n)]
    mid = draw(st.sampled_from(tokens))
    return OutgoingAlreadyPresent(message_id=mid, existing_tokens=tokens)


_scenario = st.one_of(
    st.builds(OutgoingNoRefs, message_id=_mid),
    _appends_scenario(),
    _appends_with_trimming_scenario(),
    _idempotent_scenario(),
    st.just(OutgoingNoMessageId()),
    st.builds(IncomingMessage, message_id=_mid),
)

_TOKEN_RE = re.compile(r'<[^<>]+>')


def _run_step(conn, step) -> None:
    """Send one message on an open connection and assert the milter's response."""
    if isinstance(step, IncomingMessage):
        # Explicitly zero out auth macros so a previous outgoing step on the same
        # connection doesn't leak its SASL state into this step's envfrom().
        # An empty MACRO packet does not clear previously set macros in pymilter.
        conn.send_macro(mc.SMFIC_MAIL, **{"{auth_type}": "", "{auth_authen}": ""})
    else:
        conn.send_macro(mc.SMFIC_MAIL, **OUTGOING_MACROS)
    conn.send_ar(mc.SMFIC_MAIL, args=["<sender@example.com>"])

    if isinstance(step, OutgoingNoRefs):
        conn.send_headers([("Message-ID", step.message_id)])
    elif isinstance(step, (OutgoingAppendsToRefs, OutgoingAppendsWithTrimming, OutgoingAlreadyPresent)):
        conn.send_headers([
            ("Message-ID", step.message_id),
            ("References", " ".join(step.existing_tokens)),
        ])
    elif isinstance(step, OutgoingNoMessageId):
        conn.send_headers([("Subject", "Test")])
    elif isinstance(step, IncomingMessage):
        conn.send_headers([("Message-ID", step.message_id)])

    conn.send_ar(mc.SMFIC_EOH)
    conn.send_body("Test body")
    actions = _modification_actions(conn.send_eom())

    if isinstance(step, OutgoingNoRefs):
        assert len(actions) == 1, f"OutgoingNoRefs: expected 1 action, got {actions}"
        cmd, params = actions[0]
        assert cmd == mc.SMFIR_ADDHEADER
        assert params["name"] == "References"
        assert _TOKEN_RE.findall(params["value"]) == [step.message_id]

    elif isinstance(step, OutgoingAppendsToRefs):
        assert len(actions) == 1, f"OutgoingAppendsToRefs: expected 1 action, got {actions}"
        cmd, params = actions[0]
        assert cmd == mc.SMFIR_CHGHEADER
        assert params["index"] == 1
        assert params["name"] == "References"
        assert _TOKEN_RE.findall(params["value"]) == step.existing_tokens + [step.message_id]

    elif isinstance(step, OutgoingAppendsWithTrimming):
        assert len(actions) == 1, f"OutgoingAppendsWithTrimming: expected 1 action, got {actions}"
        cmd, params = actions[0]
        assert cmd == mc.SMFIR_CHGHEADER
        assert params["index"] == 1
        assert params["name"] == "References"
        found = _TOKEN_RE.findall(params["value"])
        # max_references=8: thread root + last 6 existing + message_id
        expected = [step.existing_tokens[0]] + step.existing_tokens[-6:] + [step.message_id]
        assert found == expected, f"expected {expected}, got {found}"

    else:
        assert actions == [], f"{type(step).__name__}: expected no modifications, got {actions}"


class TestPropertyBased:
    @settings(max_examples=_MAX_EXAMPLES)
    @given(steps=st.lists(_scenario, min_size=1, max_size=10))
    def test_random_message_sequences(self, milter_socket, steps):
        """Drive random sequences of typed message scenarios on a single connection.

        Each scenario defines its own expected outcome. No logic.py used as
        oracle — expectations are derived directly from the generated inputs.
        Catches state-leakage bugs across consecutive messages on one connection.
        """
        conn = _open_connection(milter_socket)
        try:
            conn.send_macro(mc.SMFIC_CONNECT)
            conn.send_ar(mc.SMFIC_CONNECT, hostname="localhost", family=mc.SMFIA_INET, port=0, address="127.0.0.1")
            for step in steps:
                _run_step(conn, step)
        finally:
            try:
                conn._send(mc.SMFIC_QUIT)
            except Exception:
                pass
            conn.sock.close()
