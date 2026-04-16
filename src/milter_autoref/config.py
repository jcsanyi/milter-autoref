"""Environment-variable configuration for milter-autoref."""

import logging
import os
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Union


def _parse_bool(value: str, name: str) -> bool:
    """Parse a boolean env var. Raises ValueError on unrecognised values."""
    norm = value.strip().lower()
    if norm in ("1", "true", "yes", "on"):
        return True
    if norm in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(
        f"Invalid boolean value for {name}: {value!r}. "
        "Use one of: 1, true, yes, on, 0, false, no, off"
    )


def _parse_log_level(value: str, name: str) -> int:
    """Parse a log level name into a logging int. Raises ValueError on unknown levels."""
    level = logging.getLevelName(value.strip().upper())
    if not isinstance(level, int):
        raise ValueError(
            f"Invalid log level for {name}: {value!r}. "
            "Use one of: DEBUG, INFO, WARNING, ERROR, CRITICAL"
        )
    return level


def _parse_internal_hosts(value: str, name: str) -> tuple:
    """Parse a comma-separated list of CIDR networks. Raises ValueError on bad input."""
    if not value.strip():
        return ()
    networks = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            raise ValueError(f"Invalid CIDR network in {name}: {item!r}")
    return tuple(networks)


@dataclass(frozen=True)
class Config:
    socket: str
    outgoing_daemons: frozenset
    trust_auth: bool
    internal_hosts: tuple
    dry_run: bool
    log_level: int
    timeout: int

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables, applying defaults."""
        socket = os.environ.get("AUTOREF_SOCKET", "/tmp/milter-autoref.sock")

        raw_daemons = os.environ.get("AUTOREF_OUTGOING_DAEMONS", "ORIGINATING")
        outgoing_daemons = frozenset(
            d.strip() for d in raw_daemons.split(",") if d.strip()
        )

        trust_auth = _parse_bool(
            os.environ.get("AUTOREF_TRUST_AUTH", "true"), "AUTOREF_TRUST_AUTH"
        )

        internal_hosts = _parse_internal_hosts(
            os.environ.get("AUTOREF_INTERNAL_HOSTS", ""), "AUTOREF_INTERNAL_HOSTS"
        )

        dry_run = _parse_bool(
            os.environ.get("AUTOREF_DRY_RUN", "false"), "AUTOREF_DRY_RUN"
        )

        log_level = _parse_log_level(
            os.environ.get("AUTOREF_LOG_LEVEL", "INFO"), "AUTOREF_LOG_LEVEL"
        )

        raw_timeout = os.environ.get("AUTOREF_TIMEOUT", "600")
        try:
            timeout = int(raw_timeout)
        except ValueError:
            raise ValueError(
                f"Invalid integer for AUTOREF_TIMEOUT: {raw_timeout!r}"
            )

        return cls(
            socket=socket,
            outgoing_daemons=outgoing_daemons,
            trust_auth=trust_auth,
            internal_hosts=internal_hosts,
            dry_run=dry_run,
            log_level=log_level,
            timeout=timeout,
        )
