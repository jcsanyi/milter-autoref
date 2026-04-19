"""Tests for milter_autoref.__main__.main() — startup wiring.

The only branching logic in main() is socket-directory creation:
- `inet:` / `inet6:` sockets skip directory creation entirely.
- Unix-socket paths create the parent directory if missing.
- An OSError during directory creation returns 1.

Everything else (Config loading, factory/flags/signal registration,
runmilter invocation) is simple wiring covered by other layers.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Ensure no AUTOREF_* env vars leak in from the developer's shell."""
    for key in (
        "AUTOREF_SOCKET",
        "AUTOREF_AUTH_ONLY",
        "AUTOREF_DRY_RUN",
        "AUTOREF_LOG_LEVEL",
        "AUTOREF_TRIM_REFERENCES",
        "AUTOREF_MAX_REFERENCES",
    ):
        monkeypatch.delenv(key, raising=False)


def _stub_milter():
    """Return a patch context for the Milter module with a no-op runmilter."""
    m = MagicMock()
    m.CHGHDRS = 1
    m.ADDHDRS = 2
    m.runmilter = MagicMock()
    return patch("milter_autoref.__main__.Milter", m), m


def test_inet_socket_skips_directory_creation(monkeypatch):
    monkeypatch.setenv("AUTOREF_SOCKET", "inet:8890@localhost")
    from milter_autoref.__main__ import main

    patcher, _m = _stub_milter()
    with patcher, patch("milter_autoref.__main__.os.makedirs") as mkdir:
        assert main() == 0
        mkdir.assert_not_called()


def test_unix_socket_creates_directory(monkeypatch, tmp_path):
    sock = tmp_path / "sub" / "milter.sock"
    monkeypatch.setenv("AUTOREF_SOCKET", str(sock))
    from milter_autoref.__main__ import main

    patcher, _m = _stub_milter()
    with patcher:
        assert main() == 0
        assert sock.parent.is_dir()


def test_unreadable_socket_dir_returns_1(monkeypatch):
    monkeypatch.setenv("AUTOREF_SOCKET", "/nonexistent-root/sub/milter.sock")
    from milter_autoref.__main__ import main

    patcher, m = _stub_milter()
    with patcher, patch(
        "milter_autoref.__main__.os.makedirs", side_effect=OSError("nope")
    ):
        assert main() == 1
        m.runmilter.assert_not_called()
