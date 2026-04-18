"""Tests for AutorefMilter callbacks.

We instantiate AutorefMilter directly and drive its callbacks with fakes.
getsymval, addheader, and chgheader are replaced with MagicMocks so no
real pymilter socket or libmilter is needed.
"""

import logging
from unittest.mock import MagicMock

import Milter

from milter_autoref.config import Config
from milter_autoref.milter import AutorefMilter


def _make_config(**overrides) -> Config:
    defaults = dict(
        socket="/tmp/test.sock",
        auth_only=True,
        dry_run=False,
        log_level=logging.DEBUG,
        timeout=600,
        trim_references=True,
        max_references=20,
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

OUTGOING_MACROS = {"{auth_type}": "PLAIN", "{auth_authen}": "user@example.com"}


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

INCOMING_MACROS = {}  # no auth macros


class TestIncomingMessage:
    def test_no_mutation_for_unauthenticated(self):
        m = _make_milter()
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
# auth_only=false escape hatch
# ---------------------------------------------------------------------------


class TestAuthOnlyFalse:
    def test_unauthenticated_rewritten_when_auth_only_false(self):
        m = _make_milter(config=_make_config(auth_only=False))
        _drive(m, [("Message-ID", "<orig@example.com>")], macros=INCOMING_MACROS)
        m.addheader.assert_called_once_with("References", "<orig@example.com>")


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


class TestEomResetsState:
    def test_second_message_does_not_inherit_references_from_first(self):
        m = _make_milter()
        macros = OUTGOING_MACROS

        # First message: has a References header, completes normally via eom()
        m.getsymval = MagicMock(side_effect=lambda key: macros.get(key))
        m.connect("localhost", None, ("127.0.0.1", 0))
        m.envfrom("<a@example.com>")
        m.header("Message-ID", "<first@example.com>")
        m.header("References", "<old@example.com>")
        m.eoh()
        m.body(b"body")
        m.eom()

        # Reset mocks so we only see calls from the second message
        m.addheader.reset_mock()
        m.chgheader.reset_mock()

        # Second message on the same connection: no References header
        m.envfrom("<b@example.com>")
        m.header("Message-ID", "<second@example.com>")
        m.eoh()
        m.body(b"body")
        m.eom()

        # Should addheader (no References on this message), not chgheader
        m.addheader.assert_called_once_with("References", "<second@example.com>")
        m.chgheader.assert_not_called()


class TestTrimReferences:
    def test_trims_to_max_references(self):
        m = _make_milter(config=_make_config(max_references=5))
        tokens = [f"<msg-{i}@example.com>" for i in range(10)]
        _drive(
            m,
            [
                ("Message-ID", "<new@example.com>"),
                ("References", " ".join(tokens)),
            ],
            macros=OUTGOING_MACROS,
        )
        m.chgheader.assert_called_once()
        value = m.chgheader.call_args[0][2]
        import re
        found = re.findall(r"<[^<>]+>", value)
        assert len(found) == 5
        assert found[0] == "<msg-0@example.com>"
        assert found[-1] == "<new@example.com>"

    def test_no_trimming_when_disabled(self):
        m = _make_milter(config=_make_config(trim_references=False))
        tokens = [f"<msg-{i}@example.com>" for i in range(10)]
        _drive(
            m,
            [
                ("Message-ID", "<new@example.com>"),
                ("References", " ".join(tokens)),
            ],
            macros=OUTGOING_MACROS,
        )
        m.chgheader.assert_called_once()
        value = m.chgheader.call_args[0][2]
        import re
        found = re.findall(r"<[^<>]+>", value)
        assert len(found) == 11  # all 10 originals + 1 new


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
