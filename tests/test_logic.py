"""Tests for milter_autoref.logic — pure functions, no pymilter dependency."""

import pytest

from milter_autoref.config import Config
from milter_autoref.logic import (
    _FOLD_LINE_WIDTH,
    _REFERENCES_HEADER_PREFIX_LEN,
    compute_new_references,
    extract_message_id_token,
    is_outgoing,
)


def _make_cfg(**overrides) -> Config:
    base = dict(
        socket="/tmp/test.sock",
        auth_only=True,
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

    def test_normalises_internal_whitespace_of_existing_refs(self):
        refs = "<a@x.com>  <b@x.com>"
        result = compute_new_references("<c@x.com>", refs)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_strips_trailing_whitespace_before_appending(self):
        result = compute_new_references("<b@x.com>", "<a@x.com>   ")
        assert result == "<a@x.com> <b@x.com>"

    def test_normalises_message_id_whitespace(self):
        result = compute_new_references("  <x@y.com>  ", None)
        assert result == "<x@y.com>"

    def test_extracts_first_token_from_message_id(self):
        # Message-ID header value sometimes has surrounding text
        result = compute_new_references("some text <x@y.com> more text", None)
        assert result == "<x@y.com>"

    def test_unfolds_crlf_folded_existing_refs_on_append(self):
        refs = "<a@x.com>\r\n <b@x.com>"
        result = compute_new_references("<c@x.com>", refs)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_drops_cfws_comments_between_tokens(self):
        refs = "<a@x.com> (old) <b@x.com>"
        result = compute_new_references("<c@x.com>", refs)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_short_result_is_not_folded(self):
        result = compute_new_references("<b@x.com>", "<a@x.com>")
        assert "\r\n" not in result

    def test_long_result_is_folded_to_line_width(self):
        # Build enough tokens that the joined line exceeds 78 chars.
        tokens = [f"<msg-id-{i:03d}@example.com>" for i in range(10)]
        existing = " ".join(tokens)
        result = compute_new_references("<new@example.com>", existing)
        assert "\r\n " in result

        # Validate per-line lengths: first line counts the "References: "
        # prefix the MTA prepends; continuation lines count their leading SP.
        lines = result.split("\r\n")
        assert len(lines[0]) + _REFERENCES_HEADER_PREFIX_LEN <= _FOLD_LINE_WIDTH or \
            len(lines[0].split(" ")) == 1  # lone oversized token allowed
        for cont in lines[1:]:
            # Continuation lines are stored without their fold-leading SP here;
            # the SP is part of the "\r\n " join sequence.
            assert len(cont) + 1 <= _FOLD_LINE_WIDTH or len(cont.split(" ")) == 1

    def test_oversized_single_token_kept_on_own_line(self):
        # A token longer than the line budget must not be split.
        long_tok = "<" + "x" * 100 + "@example.com>"
        result = compute_new_references("<new@example.com>", f"<a@x.com> {long_tok}")
        lines = result.split("\r\n ")
        # Oversized token is on its own line, intact.
        assert long_tok in lines


# ---------------------------------------------------------------------------
# is_outgoing
# ---------------------------------------------------------------------------


class TestIsOutgoing:
    def test_auth_type_present_returns_true(self):
        assert is_outgoing("PLAIN", None, _make_cfg())

    def test_auth_authen_present_returns_true(self):
        assert is_outgoing(None, "user@example.com", _make_cfg())

    def test_both_auth_macros_present_returns_true(self):
        assert is_outgoing("PLAIN", "user@example.com", _make_cfg())

    def test_no_auth_with_auth_only_true_returns_false(self):
        assert not is_outgoing(None, None, _make_cfg())

    def test_empty_auth_strings_with_auth_only_true_returns_false(self):
        assert not is_outgoing("", "", _make_cfg())

    def test_auth_only_false_returns_true_without_auth(self):
        cfg = _make_cfg(auth_only=False)
        assert is_outgoing(None, None, cfg)

    def test_auth_only_false_returns_true_with_auth(self):
        cfg = _make_cfg(auth_only=False)
        assert is_outgoing("PLAIN", "user@example.com", cfg)
