"""Typed settings loaded from environment. Single source of truth for config."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, Field


def _backfill_empty_env_from_dotenv(file_values: dict | None = None) -> None:
    """Populate env vars that are MISSING or present-but-EMPTY from ``.env``.

    Why this exists: ``load_dotenv()`` does not override a variable that is
    already set in the process environment — and crucially it treats an empty
    string as "already set". Some launchers (notably the Claude Code harness)
    inject ``ANTHROPIC_API_KEY=''`` into the environment. With a plain
    ``load_dotenv()`` that empty value wins, so the real key in ``.env`` is
    silently ignored and the app boots in "chat offline" mode — chat produces
    no responses even though a valid key is sitting in ``.env``.

    This backfill copies a value from ``.env`` only when the current
    environment value is missing or blank, so a genuinely-set env var (a real
    deployment secret) is never clobbered, while a present-but-empty var falls
    back to the file the user actually edited.
    """
    if file_values is None:
        file_values = dotenv_values()
    for key, value in file_values.items():
        if value and not (os.environ.get(key) or "").strip():
            os.environ[key] = value


# Load .env, then heal any present-but-empty vars from the file (see above).
load_dotenv()
_backfill_empty_env_from_dotenv()


class Models(BaseModel):
    orchestrator: str = "claude-sonnet-4-6"
    subagent: str = "claude-haiku-4-5"


class HITLThresholds(BaseModel):
    """Risk-tiered confirmation gate thresholds (USD)."""

    soft_confirm_max: Decimal = Decimal("30")
    explicit_confirm_min: Decimal = Decimal("100")
    full_summary_min: Decimal = Decimal("500")
    confidence_escalation: float = 0.8


class MandateDefaults(BaseModel):
    """Defaults applied when a mandate is created without explicit caps."""

    per_transaction: Decimal = Decimal("500")
    daily: Decimal = Decimal("1000")
    monthly: Decimal = Decimal("5000")
    default_expiry_hours: int = 24


class Settings(BaseModel):
    anthropic_api_key: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    stripe_test_key: str = Field(default_factory=lambda: os.getenv("STRIPE_TEST_KEY", ""))
    shopify_access_token: str = Field(default_factory=lambda: os.getenv("SHOPIFY_ACCESS_TOKEN", ""))
    shopify_shop_domain: str = Field(default_factory=lambda: os.getenv("SHOPIFY_SHOP_DOMAIN", ""))

    agent_private_key_pem: str = Field(
        default_factory=lambda: os.getenv("AGENT_PRIVATE_KEY_PEM", "")
    )
    agent_key_id: str = Field(default_factory=lambda: os.getenv("AGENT_KEY_ID", "agent-key-1"))
    ap2_signing_key: str = Field(default_factory=lambda: os.getenv("AP2_SIGNING_KEY", ""))

    # Runtime DB lives OUTSIDE the project source tree by default.
    #
    # Why this matters: the documented dev command is
    # ``uvicorn web.app:app --reload``, whose file watcher restarts the
    # server on ANY file change under the working directory. The web app
    # creates a per-session TinyDB file on first request
    # (``data/sessions/<id>.json`` under the old default). If that lived
    # inside the watched tree, every new visitor — and every chat / cart /
    # purchase write — would touch a file in-tree, trigger a reload, and
    # drop the open SSE stream + gate WebSocket. Worse, the restart wipes
    # the in-memory session store, so the browser's cookie points at a
    # now-dead session and the very next request creates ANOTHER in-tree
    # file → another reload → an endless flicker loop where nothing works.
    #
    # Defaulting to ``~/.carto`` (overridable via ``DB_PATH``) keeps all
    # runtime writes off the reload watcher's radar permanently. Tests
    # always set ``DB_PATH`` to a tmp dir, so they are unaffected.
    db_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("DB_PATH", str(Path.home() / ".carto" / "agentic_commerce.json"))
        )
    )

    profile_cache_ttl_seconds: int = 60

    models: Models = Field(default_factory=Models)
    hitl: HITLThresholds = Field(default_factory=HITLThresholds)
    mandate_defaults: MandateDefaults = Field(default_factory=MandateDefaults)


settings = Settings()
