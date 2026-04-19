"""Pure functions for milter-autoref header manipulation and outgoing-mail detection.

No pymilter imports — these functions are independently unit-testable.
"""

import re
from typing import Union

from .config import Config


# ---------------------------------------------------------------------------
# Message-ID helpers
# ---------------------------------------------------------------------------

_MSG_ID_RE = re.compile(r"<[^<>\s]+>")

_REFERENCES_HEADER_PREFIX_LEN = len("References: ")
_FOLD_LINE_WIDTH = 78  # RFC 5322 SHOULD limit


def extract_message_id_token(raw: str) -> Union[str, None]:
    """Return the first <...> token in *raw*, stripped of surrounding whitespace.

    Returns None if no bracketed token is found.
    """
    m = _MSG_ID_RE.search(raw)
    return m.group(0) if m else None


def _fold_references(tokens: list[str]) -> str:
    """Serialize *tokens* space-separated, inserting CRLF+SP folds so no line
    exceeds _FOLD_LINE_WIDTH.

    The first line's budget is reduced by the 'References: ' prefix the MTA
    will prepend. Continuation lines count the leading SP against the budget.
    A token longer than the budget is kept on its own line rather than split.
    """
    if not tokens:
        return ""

    first_budget = _FOLD_LINE_WIDTH - _REFERENCES_HEADER_PREFIX_LEN
    cont_budget = _FOLD_LINE_WIDTH - 1  # leading SP on continuation lines

    lines = [tokens[0]]
    for tok in tokens[1:]:
        budget = first_budget if len(lines) == 1 else cont_budget
        candidate = lines[-1] + " " + tok
        if len(candidate) > budget:
            lines.append(tok)
        else:
            lines[-1] = candidate
    return "\r\n ".join(lines)


def _trim_references(tokens: list[str], max_refs: int) -> list[str]:
    """Trim *tokens* to at most *max_refs* entries.

    Keeps the first token (thread root) and the last *max_refs - 1* tokens
    (most recent ancestors). As a special case, *max_refs == 1* keeps only
    the last token — this function is called after the new Message-ID has
    been appended, so the last token is always the self-reference this
    milter exists to add, and dropping it would defeat the purpose.
    Returns *tokens* unchanged when *max_refs* is zero (disabled) or the
    list is already within the limit.
    """
    if max_refs <= 0 or len(tokens) <= max_refs:
        return tokens
    if max_refs == 1:
        return [tokens[-1]]
    return [tokens[0]] + tokens[-(max_refs - 1):]


def compute_new_references(
    message_id: Union[str, None],
    existing_references: Union[str, None],
    max_references: int,
) -> Union[str, None]:
    """Return the new value for the References header, or None if no change is needed.

    Rules:
    - If *message_id* is missing, blank, or contains no <...> token → None.
    - Normalise to the first <...> token from *message_id*.
    - If *existing_references* is None → return the token alone.
    - If *existing_references* already contains the token (idempotent) → None.
    - Otherwise tokenise *existing_references* (unfolding CRLF-folded
      continuations and dropping CFWS comments and extraneous whitespace),
      append the new token, and re-fold at the RFC 5322 SHOULD line width.
    """
    if not message_id:
        return None

    mid = extract_message_id_token(message_id)
    if mid is None:
        return None

    if existing_references is None:
        return mid

    tokens = _MSG_ID_RE.findall(existing_references)
    if mid in tokens:
        return None

    tokens.append(mid)
    tokens = _trim_references(tokens, max_references)
    return _fold_references(tokens)


# ---------------------------------------------------------------------------
# Outgoing-mail detection
# ---------------------------------------------------------------------------


def is_outgoing(
    auth_type: Union[str, None],
    auth_authen: Union[str, None],
    cfg: Config,
) -> bool:
    """Return True if this message should be treated as outgoing.

    When *cfg.auth_only* is True (the default), only messages with SASL auth
    macros set are treated as outgoing — the safe default for milters that
    may be wired globally in main.cf.

    When *cfg.auth_only* is False, every message is treated as outgoing. The
    operator is responsible for scoping the milter to outbound-only traffic
    via master.cf (e.g. per-service `-o smtpd_milters=`).
    """
    if not cfg.auth_only:
        return True
    return bool(auth_type or auth_authen)
