"""Tests for milter_autoref.logic — pure functions, no pymilter dependency."""

import pytest

from milter_autoref.config import Config
from milter_autoref.logic import (
    _ip_in_any,
    compute_new_references,
    extract_message_id_token,
    is_outgoing,
)
from ipaddress import ip_network


def _make_cfg(**overrides) -> Config:
    base = dict(
        socket="/tmp/test.sock",
        outgoing_daemons=frozenset({"ORIGINATING"}),
        trust_auth=True,
        internal_hosts=(),
        dry_run=False,
        log_level=20,
        timeout=600,
    )
    base.update(overrides)
    return Config(**base)


# ---------------------------------------------------------------------------
# extract_message_id_token
# ---------------------------------------------------------------------------


class TestExtractMessageIdToken:
    def test_simple_token(self):
        assert extract_message_id_token("<abc@example.com>") == "<abc@example.com>"

    def test_strips_surrounding_whitespace(self):
        assert extract_message_id_token("  <abc@example.com>  ") == "<abc@example.com>"

    def test_embedded_in_text(self):
        assert extract_message_id_token("Message-ID: <abc@example.com>") == "<abc@example.com>"

    def test_multiple_tokens_returns_first(self):
        assert extract_message_id_token("<a@x.com> <b@x.com>") == "<a@x.com>"

    def test_no_bracket_returns_none(self):
        assert extract_message_id_token("bare-id@example.com") is None

    def test_empty_string_returns_none(self):
        assert extract_message_id_token("") is None

    def test_only_whitespace_returns_none(self):
        assert extract_message_id_token("   ") is None


# ---------------------------------------------------------------------------
# compute_new_references
# ---------------------------------------------------------------------------


class TestComputeNewReferences:
    def test_no_existing_refs_returns_mid(self):
        result = compute_new_references("<new@example.com>", None)
        assert result == "<new@example.com>"

    def test_appends_to_existing_refs(self):
        result = compute_new_references("<c@x.com>", "<a@x.com> <b@x.com>")
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_idempotent_when_already_present(self):
        result = compute_new_references("<a@x.com>", "<a@x.com> <b@x.com>")
        assert result is None

    def test_idempotent_with_folded_whitespace(self):
        refs = "<a@x.com>\r\n <b@x.com>"
        result = compute_new_references("<a@x.com>", refs)
        assert result is None

    def test_missing_message_id_returns_none(self):
        assert compute_new_references(None, "<a@x.com>") is None

    def test_blank_message_id_returns_none(self):
        assert compute_new_references("", "<a@x.com>") is None

    def test_message_id_without_brackets_returns_none(self):
        assert compute_new_references("bare-id@example.com", None) is None

    def test_preserves_internal_whitespace_of_existing_refs(self):
        refs = "<a@x.com>  <b@x.com>"
        result = compute_new_references("<c@x.com>", refs)
        assert result == "<a@x.com>  <b@x.com> <c@x.com>"

    def test_rstrips_trailing_whitespace_before_appending(self):
        result = compute_new_references("<b@x.com>", "<a@x.com>   ")
        assert result == "<a@x.com> <b@x.com>"

    def test_normalises_message_id_whitespace(self):
        result = compute_new_references("  <x@y.com>  ", None)
        assert result == "<x@y.com>"

    def test_extracts_first_token_from_message_id(self):
        # Message-ID header value sometimes has surrounding text
        result = compute_new_references("some text <x@y.com> more text", None)
        assert result == "<x@y.com>"


# ---------------------------------------------------------------------------
# is_outgoing
# ---------------------------------------------------------------------------


class TestIsOutgoing:
    def test_daemon_name_match_returns_true(self):
        assert is_outgoing("ORIGINATING", None, None, None, _make_cfg())

    def test_daemon_name_mismatch_returns_false(self):
        assert not is_outgoing("INBOUND", None, None, None, _make_cfg())

    def test_daemon_name_none_returns_false_without_other_signals(self):
        assert not is_outgoing(None, None, None, None, _make_cfg())

    def test_auth_type_present_trust_auth_true_returns_true(self):
        assert is_outgoing(None, "PLAIN", None, None, _make_cfg())

    def test_auth_authen_present_trust_auth_true_returns_true(self):
        assert is_outgoing(None, None, "user@example.com", None, _make_cfg())

    def test_auth_present_trust_auth_false_returns_false(self):
        cfg = _make_cfg(trust_auth=False)
        assert not is_outgoing(None, "PLAIN", "user@example.com", None, cfg)

    def test_all_signals_absent_returns_false(self):
        assert not is_outgoing(None, None, None, None, _make_cfg())

    def test_client_addr_in_cidr_returns_true(self):
        cfg = _make_cfg(internal_hosts=(ip_network("172.16.0.0/12"),))
        assert is_outgoing(None, None, None, "172.17.0.5", cfg)

    def test_client_addr_not_in_cidr_returns_false(self):
        cfg = _make_cfg(internal_hosts=(ip_network("172.16.0.0/12"),), trust_auth=False)
        assert not is_outgoing(None, None, None, "10.0.0.1", cfg)

    def test_client_addr_ipv6_in_cidr_returns_true(self):
        cfg = _make_cfg(internal_hosts=(ip_network("fc00::/7"),))
        assert is_outgoing(None, None, None, "fc00::1", cfg)

    def test_client_addr_with_ipv6_prefix_stripped(self):
        cfg = _make_cfg(internal_hosts=(ip_network("172.16.0.0/12"),))
        assert is_outgoing(None, None, None, "IPv6:172.17.0.5", cfg)

    def test_client_addr_parse_failure_returns_false(self):
        cfg = _make_cfg(internal_hosts=(ip_network("172.16.0.0/12"),), trust_auth=False)
        assert not is_outgoing(None, None, None, "not-an-ip", cfg)

    def test_localhost_in_internal_hosts(self):
        cfg = _make_cfg(internal_hosts=(ip_network("127.0.0.1/32"),))
        assert is_outgoing(None, None, None, "127.0.0.1", cfg)

    def test_custom_outgoing_daemon_names(self):
        cfg = _make_cfg(outgoing_daemons=frozenset({"SUBMISSION", "RELAY"}))
        assert is_outgoing("SUBMISSION", None, None, None, cfg)
        assert is_outgoing("RELAY", None, None, None, cfg)
        assert not is_outgoing("ORIGINATING", None, None, None, cfg)


# ---------------------------------------------------------------------------
# _ip_in_any (edge cases)
# ---------------------------------------------------------------------------


class TestIpInAny:
    def test_empty_networks_returns_false(self):
        assert not _ip_in_any("127.0.0.1", ())

    def test_ipv4_ipv6_mismatch_does_not_crash(self):
        # IPv4 address against IPv6 network — should return False, not raise
        assert not _ip_in_any("192.168.1.1", (ip_network("fc00::/7"),))
