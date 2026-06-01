"""TinyDB wrapper. File-based persistence for MVP — swap for Postgres at scale.

Tables:
- mandates        — AgentMandate records
- orders          — PurchaseOrder records
- audit_log       — every agent action (append-only)
- spend_records   — SpendRecord per mandate, used for cap calculations
- profile_cache   — /.well-known/ucp responses (60s TTL)
- user_profiles   — UserProfile records
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB
from tinydb.storages import JSONStorage
from tinydb.middlewares import CachingMiddleware


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class _SerialisingStorage(JSONStorage):
    """TinyDB storage that handles Decimal + datetime."""

    def write(self, data):
        with open(self._handle.name, "w") as f:
            json.dump(data, f, default=_json_default, indent=2)


class DB:
    """Thin wrapper around TinyDB providing typed table accessors."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(
            str(self.path),
            storage=CachingMiddleware(_SerialisingStorage),
        )

    @property
    def mandates(self):
        return self._db.table("mandates")

    @property
    def orders(self):
        return self._db.table("orders")

    @property
    def audit_log(self):
        return self._db.table("audit_log")

    @property
    def spend_records(self):
        return self._db.table("spend_records")

    @property
    def profile_cache(self):
        return self._db.table("profile_cache")

    @property
    def user_profiles(self):
        return self._db.table("user_profiles")

    def close(self):
        self._db.close()

    def clear_all(self):
        """For tests only."""
        self._db.drop_tables()


def append_audit(
    db: DB,
    *,
    agent: str,
    tool: str,
    action: str,
    mandate_id: str | None,
    args: dict | None = None,
) -> None:
    """Write an immutable audit entry. Called BEFORE the action executes."""
    db.audit_log.insert(
        {
            "agent": agent,
            "tool": tool,
            "action": action,
            "mandate_id": mandate_id,
            "args": args or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


# Exposed for callers that want to construct queries
MandateQ = Query()
OrderQ = Query()
SpendQ = Query()
ProfileCacheQ = Query()
AuditQ = Query()
UserQ = Query()
