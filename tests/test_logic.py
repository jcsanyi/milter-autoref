"""Tests for milter_autoref.logic — pure functions, no pymilter dependency."""

import re

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
        trim_references=True,
        max_references=20,
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
        result = compute_new_references("<new@example.com>", None, max_references=20)
        assert result == "<new@example.com>"

    def test_appends_to_existing_refs(self):
        result = compute_new_references("<c@x.com>", "<a@x.com> <b@x.com>", max_references=20)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_idempotent_when_already_present(self):
        result = compute_new_references("<a@x.com>", "<a@x.com> <b@x.com>", max_references=20)
        assert result is None

    def test_idempotent_with_folded_whitespace(self):
        refs = "<a@x.com>\r\n <b@x.com>"
        result = compute_new_references("<a@x.com>", refs, max_references=20)
        assert result is None

    def test_missing_message_id_returns_none(self):
        assert compute_new_references(None, "<a@x.com>", max_references=20) is None

    def test_blank_message_id_returns_none(self):
        assert compute_new_references("", "<a@x.com>", max_references=20) is None

    def test_message_id_without_brackets_returns_none(self):
        assert compute_new_references("bare-id@example.com", None, max_references=20) is None

    def test_normalises_internal_whitespace_of_existing_refs(self):
        refs = "<a@x.com>  <b@x.com>"
        result = compute_new_references("<c@x.com>", refs, max_references=20)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_strips_trailing_whitespace_before_appending(self):
        result = compute_new_references("<b@x.com>", "<a@x.com>   ", max_references=20)
        assert result == "<a@x.com> <b@x.com>"

    def test_normalises_message_id_whitespace(self):
        result = compute_new_references("  <x@y.com>  ", None, max_references=20)
        assert result == "<x@y.com>"

    def test_extracts_first_token_from_message_id(self):
        # Message-ID header value sometimes has surrounding text
        result = compute_new_references("some text <x@y.com> more text", None, max_references=20)
        assert result == "<x@y.com>"

    def test_unfolds_crlf_folded_existing_refs_on_append(self):
        refs = "<a@x.com>\r\n <b@x.com>"
        result = compute_new_references("<c@x.com>", refs, max_references=20)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_drops_cfws_comments_between_tokens(self):
        refs = "<a@x.com> (old) <b@x.com>"
        result = compute_new_references("<c@x.com>", refs, max_references=20)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_short_result_is_not_folded(self):
        result = compute_new_references("<b@x.com>", "<a@x.com>", max_references=20)
        assert "\r\n" not in result

    def test_long_result_is_folded_to_line_width(self):
        # Build enough tokens that the joined line exceeds 78 chars.
        tokens = [f"<msg-id-{i:03d}@example.com>" for i in range(10)]
        existing = " ".join(tokens)
        result = compute_new_references("<new@example.com>", existing, max_references=20)
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
        result = compute_new_references("<new@example.com>", f"<a@x.com> {long_tok}", max_references=20)
        lines = result.split("\r\n ")
        # Oversized token is on its own line, intact.
        assert long_tok in lines

    def test_empty_existing_refs_treated_as_no_tokens(self):
        # Empty string is not None (so we don't take the short-circuit),
        # but has no <...> tokens, so we just return the new mid alone.
        result = compute_new_references("<a@x.com>", "", max_references=20)
        assert result == "<a@x.com>"

    def test_preserves_duplicate_tokens_in_existing_refs(self):
        # Duplicates in upstream References indicate a sender bug; we
        # pass them through verbatim rather than silently deduping.
        result = compute_new_references(
            "<c@x.com>", "<a@x.com> <b@x.com> <a@x.com>", max_references=20
        )
        assert result == "<a@x.com> <b@x.com> <a@x.com> <c@x.com>"

    def test_unfolds_htab_folded_existing_refs(self):
        # RFC 5322 permits HTAB as fold-WSP, not just SP.
        refs = "<a@x.com>\r\n\t<b@x.com>"
        result = compute_new_references("<c@x.com>", refs, max_references=20)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_fold_exactly_at_first_line_budget(self):
        # First-line budget = 78 - len("References: ") = 66 chars of content.
        # 32 + 1 (space) + 33 = 66 chars → exactly at budget, no fold
        # (the check is strict `> budget`).
        tok_a = "<" + "a" * 28 + "@x>"   # 1 + 28 + 3 = 32 chars
        tok_b = "<" + "b" * 29 + "@y>"   # 1 + 29 + 3 = 33 chars
        result = compute_new_references(tok_b, tok_a, max_references=20)
        assert "\r\n" not in result

    def test_fold_when_first_line_budget_exceeded_by_one(self):
        # 32 + 1 + 34 = 67 chars → exceeds 66-char budget, folds.
        tok_a = "<" + "a" * 28 + "@x>"   # 32 chars
        tok_b = "<" + "b" * 30 + "@y>"   # 34 chars
        result = compute_new_references(tok_b, tok_a, max_references=20)
        assert "\r\n " in result


# ---------------------------------------------------------------------------
# compute_new_references — trimming
# ---------------------------------------------------------------------------


class TestComputeNewReferencesTrimming:
    def _tokens(self, n, start=0):
        return [f"<msg-{start + i}@example.com>" for i in range(n)]

    def test_no_trimming_when_disabled(self):
        tokens = self._tokens(10)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=0)
        assert "<msg-0@example.com>" in result
        assert "<new@x.com>" in result
        # All original tokens plus the new one should be present
        found = re.findall(r"<[^<>]+>", result)
        assert len(found) == 11

    def test_no_trimming_when_under_limit(self):
        existing = "<a@x.com> <b@x.com>"
        result = compute_new_references("<c@x.com>", existing, max_references=20)
        assert result == "<a@x.com> <b@x.com> <c@x.com>"

    def test_no_trimming_when_exactly_at_limit(self):
        tokens = self._tokens(4)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=5)
        found = re.findall(r"<[^<>]+>", result)
        assert len(found) == 5

    def test_trims_to_limit(self):
        tokens = self._tokens(10)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=5)
        found = re.findall(r"<[^<>]+>", result)
        assert len(found) == 5

    def test_keeps_thread_root_and_newest(self):
        tokens = self._tokens(10)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=5)
        found = re.findall(r"<[^<>]+>", result)
        # First token is the thread root
        assert found[0] == "<msg-0@example.com>"
        # Last token is the newly appended one
        assert found[-1] == "<new@x.com>"

    def test_drops_interior_tokens(self):
        tokens = self._tokens(10)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=5)
        found = re.findall(r"<[^<>]+>", result)
        # Should be: root, last 3 originals, new
        assert found == [
            "<msg-0@example.com>",
            "<msg-7@example.com>",
            "<msg-8@example.com>",
            "<msg-9@example.com>",
            "<new@x.com>",
        ]

    def test_max_references_2_keeps_root_and_newest(self):
        tokens = self._tokens(5)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=2)
        found = re.findall(r"<[^<>]+>", result)
        assert found == ["<msg-0@example.com>", "<new@x.com>"]

    def test_max_references_1_keeps_only_newest(self):
        tokens = self._tokens(5)
        existing = " ".join(tokens)
        result = compute_new_references("<new@x.com>", existing, max_references=1)
        found = re.findall(r"<[^<>]+>", result)
        assert found == ["<new@x.com>"]


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
