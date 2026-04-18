"""Environment-variable configuration for milter-autoref."""

import logging
import os
from dataclasses import dataclass


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


@dataclass(frozen=True)
class Config:
    socket: str
    auth_only: bool
    dry_run: bool
    log_level: int
    timeout: int
    trim_references: bool
    max_references: int

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables, applying defaults."""
        socket = os.environ.get("AUTOREF_SOCKET", "/tmp/milter-autoref.sock")

        auth_only = _parse_bool(
            os.environ.get("AUTOREF_AUTH_ONLY", "true"), "AUTOREF_AUTH_ONLY"
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

        trim_references = _parse_bool(
            os.environ.get("AUTOREF_TRIM_REFERENCES", "true"),
            "AUTOREF_TRIM_REFERENCES",
        )

        raw_max_refs = os.environ.get("AUTOREF_MAX_REFERENCES", "20")
        try:
            max_references = int(raw_max_refs)
        except ValueError:
            raise ValueError(
                f"Invalid integer for AUTOREF_MAX_REFERENCES: {raw_max_refs!r}"
            )
        if max_references <= 0:
            raise ValueError(
                f"AUTOREF_MAX_REFERENCES must be positive, got {max_references}"
            )

        return cls(
            socket=socket,
            auth_only=auth_only,
            dry_run=dry_run,
            log_level=log_level,
            timeout=timeout,
            trim_references=trim_references,
            max_references=max_references,
        )
