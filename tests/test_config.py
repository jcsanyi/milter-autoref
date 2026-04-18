"""Tests for milter_autoref.config — env-var parsing."""

import logging
import pytest

from milter_autoref.config import Config


class TestConfigDefaults:
    def test_defaults_when_env_empty(self, monkeypatch):
        for key in (
            "AUTOREF_SOCKET",
            "AUTOREF_AUTH_ONLY",
            "AUTOREF_DRY_RUN",
            "AUTOREF_LOG_LEVEL",
            "AUTOREF_TIMEOUT",
            "AUTOREF_TRIM_REFERENCES",
            "AUTOREF_MAX_REFERENCES",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = Config.from_env()
        assert cfg.socket == "/tmp/milter-autoref.sock"
        assert cfg.auth_only is True
        assert cfg.dry_run is False
        assert cfg.log_level == logging.INFO
        assert cfg.timeout == 600
        assert cfg.trim_references is True
        assert cfg.max_references == 20


class TestConfigSocket:
    def test_custom_socket(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_SOCKET", "inet:8892@localhost")
        cfg = Config.from_env()
        assert cfg.socket == "inet:8892@localhost"


class TestConfigAuthOnly:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("AUTOREF_AUTH_ONLY", value)
        cfg = Config.from_env()
        assert cfg.auth_only is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", ""])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("AUTOREF_AUTH_ONLY", value)
        cfg = Config.from_env()
        assert cfg.auth_only is False

    def test_invalid_bool_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_AUTH_ONLY", "maybe")
        with pytest.raises(ValueError, match="AUTOREF_AUTH_ONLY"):
            Config.from_env()


class TestConfigDryRun:
    def test_dry_run_default_false(self, monkeypatch):
        monkeypatch.delenv("AUTOREF_DRY_RUN", raising=False)
        cfg = Config.from_env()
        assert cfg.dry_run is False

    def test_dry_run_true(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_DRY_RUN", "true")
        cfg = Config.from_env()
        assert cfg.dry_run is True


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


class TestConfigTrimReferences:
    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("AUTOREF_TRIM_REFERENCES", value)
        cfg = Config.from_env()
        assert cfg.trim_references is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", ""])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("AUTOREF_TRIM_REFERENCES", value)
        cfg = Config.from_env()
        assert cfg.trim_references is False

    def test_invalid_bool_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_TRIM_REFERENCES", "maybe")
        with pytest.raises(ValueError, match="AUTOREF_TRIM_REFERENCES"):
            Config.from_env()


class TestConfigMaxReferences:
    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_MAX_REFERENCES", "50")
        cfg = Config.from_env()
        assert cfg.max_references == 50

    def test_non_integer_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_MAX_REFERENCES", "lots")
        with pytest.raises(ValueError, match="AUTOREF_MAX_REFERENCES"):
            Config.from_env()

    def test_zero_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_MAX_REFERENCES", "0")
        with pytest.raises(ValueError, match="AUTOREF_MAX_REFERENCES"):
            Config.from_env()

    def test_negative_raises(self, monkeypatch):
        monkeypatch.setenv("AUTOREF_MAX_REFERENCES", "-1")
        with pytest.raises(ValueError, match="AUTOREF_MAX_REFERENCES"):
            Config.from_env()
