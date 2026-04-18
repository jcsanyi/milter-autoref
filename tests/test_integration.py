"""Integration tests — run the actual milter over a real Unix socket.

Uses miltertest (MTA-side milter protocol client) to speak the Sendmail
milter protocol directly against a live AutorefMilter instance.
"""

import logging
import os
import socket
import tempfile
import threading
import time

import Milter
import miltertest
import pytest
from miltertest import constants as mc

from milter_autoref.config import Config
from milter_autoref.milter import AutorefMilter


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
