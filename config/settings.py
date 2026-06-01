"""Typed settings loaded from environment. Single source of truth for config."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


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

    db_path: Path = Field(
        default_factory=lambda: Path(os.getenv("DB_PATH", "./data/agentic_commerce.json"))
    )

    profile_cache_ttl_seconds: int = 60

    models: Models = Field(default_factory=Models)
    hitl: HITLThresholds = Field(default_factory=HITLThresholds)
    mandate_defaults: MandateDefaults = Field(default_factory=MandateDefaults)


settings = Settings()
