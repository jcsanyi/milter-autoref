"""Tests for AutorefMilter callbacks.

We instantiate AutorefMilter directly and drive its callbacks with fakes.
getsymval, addheader, and chgheader are replaced with MagicMocks so no
real pymilter socket or libmilter is needed.
"""

import logging
from unittest.mock import MagicMock, call, patch

import Milter
import pytest

from milter_autoref.config import Config
from milter_autoref.milter import AutorefMilter


def _make_config(**overrides) -> Config:
    defaults = dict(
        socket="/tmp/test.sock",
        outgoing_daemons=frozenset({"ORIGINATING"}),
        trust_auth=True,
        internal_hosts=(),
        dry_run=False,
        log_level=logging.DEBUG,
        timeout=600,
    )
    return Config(**{**defaults, **overrides})


def _make_milter(config=None, macros=None) -> AutorefMilter:
    """Build an AutorefMilter with mocked pymilter API methods.

    *macros* is a dict of macro name → value used by getsymval.
    """
    if config is None:
        config = _make_config()
    log = logging.getLogger("test")

    m = AutorefMilter(config, log)
    m.addheader = MagicMock()
    m.chgheader = MagicMock()

    # _protocol is normally set by pymilter's C extension when running in a
    # real event loop. Set it to 0 here so @Milter.noreply decorators don't
    # crash when callbacks are driven directly in tests.
    m._protocol = 0

    _macros = macros or {}
    m.getsymval = MagicMock(side_effect=lambda key: _macros.get(key))
    return m


def _drive(milter, headers, macros=None):
    """Drive a milter through a full SMTP-phase sequence.

    *headers* is a list of (name, value) tuples.
    *macros* overrides the milter's getsymval (set before envfrom fires).
    """
    if macros is not None:
        milter.getsymval = MagicMock(side_effect=lambda key: macros.get(key))
    milter.connect("localhost", None, ("127.0.0.1", 0))
    milter.envfrom("<sender@example.com>")
    for name, value in headers:
        milter.header(name, value)
    milter.eoh()
    milter.body(b"Test body")
    return milter.eom()


# ---------------------------------------------------------------------------
# Outgoing message scenarios
# ---------------------------------------------------------------------------

OUTGOING_MACROS = {"{daemon_name}": "ORIGINATING"}


class TestOutgoingNoReferences:
    def test_addheader_called_once(self):
        m = _make_milter()
        _drive(m, [("Message-ID", "<orig@example.com>")], macros=OUTGOING_MACROS)
        m.addheader.assert_called_once_with("References", "<orig@example.com>")
        m.chgheader.assert_not_called()

    def test_returns_continue(self):
        m = _make_milter()
        result = _drive(m, [("Message-ID", "<orig@example.com>")], macros=OUTGOING_MACROS)
        assert result == Milter.CONTINUE


class TestOutgoingWithExistingReferences:
    def test_chgheader_called_with_correct_index(self):
        m = _make_milter()
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        m.chgheader.assert_called_once_with(
            "References", 1, "<earlier@example.com> <orig@example.com>"
        )
        m.addheader.assert_not_called()


class TestOutgoingMessageIdAlreadyInReferences:
    def test_no_mutation_when_idempotent(self):
        m = _make_milter()
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com> <orig@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        m.addheader.assert_not_called()
        m.chgheader.assert_not_called()


class TestOutgoingNoMessageId:
    def test_no_mutation_when_no_message_id(self):
        m = _make_milter()
        _drive(m, [("Subject", "Hello")], macros=OUTGOING_MACROS)
        m.addheader.assert_not_called()
        m.chgheader.assert_not_called()


class TestFoldedReferencesHeader:
    def test_folded_existing_refs_produce_normalized_output(self):
        m = _make_milter()
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", "<a@x.com>\r\n <b@x.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        m.chgheader.assert_called_once_with(
            "References", 1, "<a@x.com> <b@x.com> <orig@example.com>"
        )

    def test_long_refs_get_refolded(self):
        m = _make_milter()
        tokens = [f"<msg-{i:03d}@example.com>" for i in range(10)]
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", " ".join(tokens)),
            ],
            macros=OUTGOING_MACROS,
        )
        args = m.chgheader.call_args
        value = args[0][2]
        assert "\r\n " in value


class TestMultipleReferencesHeaders:
    def test_chgheader_called_with_last_index(self):
        m = _make_milter()
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", "<first@example.com>"),
                ("References", "<second@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        m.chgheader.assert_called_once_with(
            "References", 2, "<second@example.com> <orig@example.com>"
        )


class TestCaseInsensitiveHeaderNames:
    def test_lowercase_message_id(self):
        m = _make_milter()
        _drive(m, [("message-id", "<orig@example.com>")], macros=OUTGOING_MACROS)
        m.addheader.assert_called_once_with("References", "<orig@example.com>")

    def test_uppercase_message_id(self):
        m = _make_milter()
        _drive(m, [("MESSAGE-ID", "<orig@example.com>")], macros=OUTGOING_MACROS)
        m.addheader.assert_called_once_with("References", "<orig@example.com>")

    def test_mixed_case_references(self):
        m = _make_milter()
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("REFERENCES", "<earlier@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        m.chgheader.assert_called_once()


# ---------------------------------------------------------------------------
# Incoming message scenarios
# ---------------------------------------------------------------------------

INCOMING_MACROS = {}  # no daemon_name, no auth


class TestIncomingMessage:
    def test_no_mutation_for_incoming(self):
        m = _make_milter(config=_make_config(trust_auth=False))
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com>"),
            ],
            macros=INCOMING_MACROS,
        )
        m.addheader.assert_not_called()
        m.chgheader.assert_not_called()


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_no_mutation_in_dry_run(self):
        m = _make_milter(config=_make_config(dry_run=True))
        _drive(
            m,
            [
                ("Message-ID", "<orig@example.com>"),
                ("References", "<earlier@example.com>"),
            ],
            macros=OUTGOING_MACROS,
        )
        m.addheader.assert_not_called()
        m.chgheader.assert_not_called()


# ---------------------------------------------------------------------------
# State reset between messages on one connection
# ---------------------------------------------------------------------------

class TestAbortResetsState:
    def test_abort_resets_state(self):
        m = _make_milter()
        macros = OUTGOING_MACROS

        # First message: drive through and abort
        m.getsymval = MagicMock(side_effect=lambda key: macros.get(key))
        m.connect("localhost", None, ("127.0.0.1", 0))
        m.envfrom("<a@example.com>")
        m.header("Message-ID", "<first@example.com>")
        m.header("References", "<old@example.com>")
        m.abort()

        # Second message on the same connection
        m.envfrom("<b@example.com>")
        m.header("Message-ID", "<second@example.com>")
        # No References header this time — should addheader, not chgheader
        m.eoh()
        m.eom()

        m.addheader.assert_called_once_with("References", "<second@example.com>")
        m.chgheader.assert_not_called()


# ---------------------------------------------------------------------------
# Internal hosts detection
# ---------------------------------------------------------------------------

class TestInternalHostsDetection:
    def test_client_addr_in_internal_hosts_triggers_outgoing(self):
        from ipaddress import ip_network
        cfg = _make_config(
            trust_auth=False,
            internal_hosts=(ip_network("172.16.0.0/12"),),
        )
        m = _make_milter(config=cfg)
        macros = {"{client_addr}": "172.17.0.5"}  # no daemon_name, no auth
        _drive(m, [("Message-ID", "<orig@example.com>")], macros=macros)
        m.addheader.assert_called_once_with("References", "<orig@example.com>")
