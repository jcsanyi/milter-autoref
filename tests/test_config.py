"""Tests for milter_autoref.config — env-var parsing."""

import logging
import pytest

from milter_autoref.config import Config


class TestConfigDefaults:
    def test_defaults_when_env_empty(self, monkeypatch):
        for key in (
            "AUTOREF_SOCKET",
            "AUTOREF_OUTGOING_DAEMONS",
            "AUTOREF_TRUST_AUTH",
            "AUTOREF_INTERNAL_HOSTS",
            "AUTOREF_DRY_RUN",
            "AUTOREF_LOG_LEVEL",
            "AUTOREF_TIMEOUT",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = Config.from_env()
        assert cfg.socket == "/tmp/milter-autoref.sock"
        assert cfg.outgoing_daemons == frozenset({"ORIGINATING"})
        assert cfg.trust_auth is True
        assert cfg.internal_hosts == ()
        assert cfg.dry_run is False
        assert cfg.log_level == logging.INFO
        assert cfg.timeout == 600


class TestConfigSocket:
    def test_custom_socket(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_SOCKET", "inet:8892@localhost")
        cfg = Config.from_env()
        assert cfg.socket == "inet:8892@localhost"


class TestConfigOutgoingDaemons:
    def test_single_value(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_OUTGOING_DAEMONS", "ORIGINATING")
        cfg = Config.from_env()
        assert cfg.outgoing_daemons == frozenset({"ORIGINATING"})

    def test_csv_multiple_values(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_OUTGOING_DAEMONS", "ORIGINATING, SUBMISSION")
        cfg = Config.from_env()
        assert cfg.outgoing_daemons == frozenset({"ORIGINATING", "SUBMISSION"})

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_OUTGOING_DAEMONS", " ORIGINATING , RELAY ")
        cfg = Config.from_env()
        assert cfg.outgoing_daemons == frozenset({"ORIGINATING", "RELAY"})

    def test_case_preserved(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_OUTGOING_DAEMONS", "originating")
        cfg = Config.from_env()
        assert "originating" in cfg.outgoing_daemons


class TestConfigBoolParsing:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("AUTOREF_TRUST_AUTH", value)
        cfg = Config.from_env()
        assert cfg.trust_auth is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", ""])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("AUTOREF_TRUST_AUTH", value)
        cfg = Config.from_env()
        assert cfg.trust_auth is False

    def test_invalid_bool_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_TRUST_AUTH", "maybe")
        with pytest.raises(ValueError, match="AUTOREF_TRUST_AUTH"):
            Config.from_env()

    def test_dry_run_default_false(self, monkeypatch):
        monkeypatch.delenv("AUTOREF_DRY_RUN", raising=False)
        cfg = Config.from_env()
        assert cfg.dry_run is False

    def test_dry_run_true(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_DRY_RUN", "true")
        cfg = Config.from_env()
        assert cfg.dry_run is True


class TestConfigInternalHosts:
    def test_empty_returns_empty_tuple(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_INTERNAL_HOSTS", "")
        cfg = Config.from_env()
        assert cfg.internal_hosts == ()

    def test_single_cidr(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_INTERNAL_HOSTS", "172.16.0.0/12")
        cfg = Config.from_env()
        assert len(cfg.internal_hosts) == 1
        assert str(cfg.internal_hosts[0]) == "172.16.0.0/12"

    def test_multiple_cidrs(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_INTERNAL_HOSTS", "172.16.0.0/12, 127.0.0.1/32")
        cfg = Config.from_env()
        assert len(cfg.internal_hosts) == 2

    def test_host_address_normalised(self, monkeypatch):
        # ip_network with strict=False normalises host bits
        monkeypatch.setenv("AUTOREF_INTERNAL_HOSTS", "172.16.0.1/12")
        cfg = Config.from_env()
        assert str(cfg.internal_hosts[0]) == "172.16.0.0/12"

    def test_invalid_cidr_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_INTERNAL_HOSTS", "not-a-cidr")
        with pytest.raises(ValueError, match="AUTOREF_INTERNAL_HOSTS"):
            Config.from_env()

    def test_ipv6_cidr(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_INTERNAL_HOSTS", "fc00::/7")
        cfg = Config.from_env()
        assert len(cfg.internal_hosts) == 1


class TestConfigLogLevel:
    def test_debug(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_LOG_LEVEL", "DEBUG")
        cfg = Config.from_env()
        assert cfg.log_level == logging.DEBUG

    def test_lowercase_accepted(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_LOG_LEVEL", "warning")
        cfg = Config.from_env()
        assert cfg.log_level == logging.WARNING

    def test_invalid_log_level_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_LOG_LEVEL", "VERBOSE")
        with pytest.raises(ValueError, match="AUTOREF_LOG_LEVEL"):
            Config.from_env()


class TestConfigTimeout:
    def test_custom_timeout(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_TIMEOUT", "300")
        cfg = Config.from_env()
        assert cfg.timeout == 300

    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_TIMEOUT", "fast")
        with pytest.raises(ValueError, match="AUTOREF_TIMEOUT"):
            Config.from_env()
