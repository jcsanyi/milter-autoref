"""Pure functions for milter-autoref header manipulation and outgoing-mail detection.

No pymilter imports — these functions are independently unit-testable.
"""

import re
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from typing import Union

from .config import Config

IPAddress = Union[IPv4Address, IPv6Address]
IPNetwork = Union[IPv4Network, IPv6Network]


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


def compute_new_references(
    message_id: Union[str, None],
    existing_references: Union[str, None],
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
    return _fold_references(tokens)


# ---------------------------------------------------------------------------
# Outgoing-mail detection
# ---------------------------------------------------------------------------

def _parse_client_addr(raw: str) -> Union[IPAddress, None]:
    """Parse a client address string from a Postfix macro into an ip_address object.

    Handles an optional 'IPv6:' prefix that Postfix prepends for IPv6 addresses.
    Returns None on parse failure (fail-closed).
    """
    addr = raw.strip()
    if addr.lower().startswith("ipv6:"):
        addr = addr[5:]
    try:
        return ip_address(addr)
    except ValueError:
        return None


def _ip_in_any(
    client_addr: str,
    networks: tuple[IPNetwork, ...],
) -> bool:
    """Return True if *client_addr* falls within any of *networks*."""
    parsed = _parse_client_addr(client_addr)
    if parsed is None:
        return False
    for net in networks:
        try:
            if parsed in net:
                return True
        except TypeError:
            # ip_address version mismatch (v4 vs v6 network) — not a match
            pass
    return False


def is_outgoing(
    daemon_name: Union[str, None],
    auth_type: Union[str, None],
    auth_authen: Union[str, None],
    client_addr: Union[str, None],
    cfg: Config,
) -> bool:
    """Return True if this message should be treated as outgoing (fail-closed).

    Three independent axes — any match returns True:
    1. {daemon_name} is in *cfg.outgoing_daemons* (e.g. 'ORIGINATING').
    2. SASL authentication macros are present and *cfg.trust_auth* is True.
    3. {client_addr} falls within any network in *cfg.internal_hosts*.
    """
    if daemon_name and daemon_name in cfg.outgoing_daemons:
        return True
    if cfg.trust_auth and (auth_type or auth_authen):
        return True
    if client_addr and cfg.internal_hosts and _ip_in_any(client_addr, cfg.internal_hosts):
        return True
    return False
