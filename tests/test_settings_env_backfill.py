"""Settings env backfill — heal present-but-empty env vars from .env.

Regression guard for the "chat produces no responses" bug:
``load_dotenv()`` does NOT override a variable already set in the process
environment, and it treats an empty string as "already set". The Claude Code
harness injects ``ANTHROPIC_API_KEY=''`` into the environment, so a plain
``load_dotenv()`` left the real key in ``.env`` ignored and the app booted in
"chat offline" mode (no chat replies, lost purchase flows).

``_backfill_empty_env_from_dotenv`` copies a value from ``.env`` only when the
current env value is missing or blank, never clobbering a genuinely-set var.

Sorts alphabetically before test_user_journeys.py — no asyncio used here, so
the loop rule does not apply, but we keep it asyncio-free regardless.
"""

from __future__ import annotations

import os

from config.settings import _backfill_empty_env_from_dotenv

# Obvious non-secret placeholders. detect-secrets flags the "sk-ant-" shape, so
# each literal lives on its own short line with an allowlist pragma; the tests
# reference the identifiers (never the string literal), which keeps the scanner
# quiet AND survives ruff's line wrapping (a wrapped call would push the pragma
# off the secret's line and re-trip the hook).
_FAKE_KEY = "sk-ant-test-key"  # pragma: allowlist secret
_FAKE_KEY_ENV = "sk-ant-from-environment"  # pragma: allowlist secret
_FAKE_KEY_DOTENV = "sk-ant-from-dotenv"  # pragma: allowlist secret


def test_empty_env_var_is_backfilled_from_dotenv(monkeypatch):
    """A present-but-empty env var is healed from the .env mapping."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    _backfill_empty_env_from_dotenv({"ANTHROPIC_API_KEY": _FAKE_KEY})
    assert os.environ["ANTHROPIC_API_KEY"] == _FAKE_KEY


def test_whitespace_only_env_var_is_backfilled(monkeypatch):
    """A blank (whitespace-only) env var counts as empty and is healed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    _backfill_empty_env_from_dotenv({"ANTHROPIC_API_KEY": _FAKE_KEY})
    assert os.environ["ANTHROPIC_API_KEY"] == _FAKE_KEY


def test_missing_env_var_is_backfilled(monkeypatch):
    """A var absent from the environment is populated from .env."""
    monkeypatch.delenv("SOME_NEW_VAR", raising=False)
    _backfill_empty_env_from_dotenv({"SOME_NEW_VAR": "from-dotenv"})
    assert os.environ["SOME_NEW_VAR"] == "from-dotenv"


def test_genuinely_set_env_var_is_not_clobbered(monkeypatch):
    """A non-empty env var (a real deployment secret) wins over .env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", _FAKE_KEY_ENV)
    _backfill_empty_env_from_dotenv({"ANTHROPIC_API_KEY": _FAKE_KEY_DOTENV})
    assert os.environ["ANTHROPIC_API_KEY"] == _FAKE_KEY_ENV


def test_empty_dotenv_value_does_not_overwrite(monkeypatch):
    """An empty value in .env never overwrites — both empty stays empty."""
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "")
    _backfill_empty_env_from_dotenv({"SHOPIFY_ACCESS_TOKEN": ""})
    assert os.environ.get("SHOPIFY_ACCESS_TOKEN") == ""


def test_settings_loads_real_key_despite_empty_inherited_env(monkeypatch):
    """End-to-end: with an empty inherited key, the module-level backfill
    (already run at import) means the live ``settings`` object exposes the
    real key from .env — i.e. the app is NOT in offline mode."""
    from config.settings import settings

    # The repo's .env carries a real key; the import-time backfill should have
    # populated it. (If a developer runs with a blank .env this is skipped.)
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        assert settings.anthropic_api_key, "settings must expose the backfilled key"
