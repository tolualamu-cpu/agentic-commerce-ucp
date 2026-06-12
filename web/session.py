"""Per-browser-session state for the web UI.

A ``WebSession`` holds:
  - ToolContext (DB, ap2, gateways, user, in-memory SessionState)
  - OrchestratorAgent instance
  - WebSocket inbox/outbox for the gate provider
  - asyncio.Lock to serialise click + chat input on the same session

One session per signed cookie ID. Sessions live in a process-local dict;
swap for Redis/etc. when we go multi-process.

This is the bridge between HTTP request handlers and the existing agent
stack — every router imports ``get_or_create_session(request, response)``
and operates on the returned ``WebSession``.

NB: NO ``from __future__ import annotations`` — FastAPI's dependency
introspection requires real class references, not forward strings.
"""

import asyncio
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from adapters.shopify_mcp import LiveShopifyTransport, ShopifyMCPAdapter, StubShopifyTransport
from adapters.stripe import StripeAdapter
from agents.orchestrator import OrchestratorAgent, StreamingCallbacks
from config.catalogue import DEMO_MERCHANT_DISPLAY_NAMES, LIVE_MERCHANTS, MERCHANTS
from config.settings import settings
from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from models.user import Address, UserProfile
from storage.db import DB
from storage.state import SessionState
from tools.context import ToolContext
from ucp.ap2_extension import AP2MandateEngine
from ucp.discovery import UCPProfileDiscovery
from ucp.signing import RequestSigner, generate_keypair
from web.gate_provider import WebsocketConfirmProvider
from web.stream_takeover import StreamGeneration


COOKIE_NAME = "ac_session"
_COOKIE_SECRET = os.getenv("AC_COOKIE_SECRET") or secrets.token_urlsafe(32)
_serializer = URLSafeSerializer(_COOKIE_SECRET, salt="ac.session")

# Process-local session store. Single-instance only; swap for Redis when
# we scale out (the surface is just `dict.get` / `dict.__setitem__`).
_SESSIONS: dict[str, "WebSession"] = {}


@dataclass
class WebSession:
    """All per-browser state. One per signed cookie.

    asyncio primitives are lazily constructed because Python 3.9 requires
    a running event loop to instantiate ``asyncio.Queue``/``asyncio.Lock``;
    the session is built inside a sync request handler.
    """

    session_id: str
    db: DB
    ctx: ToolContext
    orchestrator: OrchestratorAgent
    mandate_id: str
    gate_provider: WebsocketConfirmProvider
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _sse_queue: Optional[asyncio.Queue] = None
    _orchestrator_lock: Optional[asyncio.Lock] = None
    # Tracks the most-recently-opened /chat/stream connection so an older
    # (navigating-away) connection cleanly hands off to the newer one instead
    # of stealing events off the single-consumer ``sse_queue``. See
    # ``web.stream_takeover`` for the full rationale.
    _stream_gen: StreamGeneration = field(default_factory=StreamGeneration)

    @property
    def sse_queue(self) -> asyncio.Queue:
        if self._sse_queue is None:
            self._sse_queue = asyncio.Queue()
        return self._sse_queue

    @property
    def stream_generation(self) -> int:
        """Generation id of the currently-active /chat/stream connection."""
        return self._stream_gen.current

    def new_stream_generation(self):
        """Claim a fresh generation for a new /chat/stream connection.

        Resolves the previous connection's ``superseded`` future so an older
        connection blocked on ``sse_queue.get()`` retires at once — before the
        orchestrator's ``products``/``text``/``done`` burst arrives (seconds
        later, after the LLM round-trips) — keeping a single consumer competing
        for the burst so events arrive in order (cards-first) on the active
        page. Returns ``(generation, superseded_future)``.
        """
        return self._stream_gen.next()

    @property
    def orchestrator_lock(self) -> asyncio.Lock:
        if self._orchestrator_lock is None:
            self._orchestrator_lock = asyncio.Lock()
        return self._orchestrator_lock

    # Backwards-compatible proxy for the draft cart. Phase 8f moved
    # the actual storage to ``ctx.session.click_basket`` so the
    # agent's ``add_to_cart`` tool can mutate it via ``ctx``. The
    # existing Phase 7c click-cart tests reference ``sess.click_basket``
    # directly — this proxy keeps them working unchanged.
    @property
    def click_basket(self) -> dict:
        return self.ctx.session.click_basket

    @click_basket.setter
    def click_basket(self, value: dict) -> None:
        self.ctx.session.click_basket = value


def _make_session(session_id: str) -> WebSession:
    """Build a fully-wired session.

    Mirrors ``main._build_context`` but constructs a fresh DB per session
    (each browser gets its own sandbox so demos don't interfere).
    """
    # Per-session DB file — keeps demo data isolated
    db_dir = settings.db_path.parent / "sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = DB(db_dir / f"{session_id}.json")

    # Signing keys are ephemeral per session for the demo. Production would
    # use one stable agent key (via env var).
    private_pem = os.getenv("AGENT_PRIVATE_KEY_PEM")
    if not private_pem:
        private_pem, _, _ = generate_keypair("agent-key-1")
    ap2_key = os.getenv("AP2_SIGNING_KEY") or secrets.token_hex(32)

    ap2 = AP2MandateEngine(db, ap2_key)
    user = UserProfile(
        user_id=f"web_user_{session_id[:8]}",
        name=os.getenv("USER_NAME", "Friend"),
        payment_method_id="pm_test_card_visa",
        preferred_categories=["running", "apparel", "electronics", "lifestyle"],
        addresses=[
            Address(
                line1="1 Demo Street",
                city="San Francisco",
                region="CA",
                postal_code="94110",
                country="US",
                is_default_shipping=True,
                is_default_billing=True,
            )
        ],
    )
    mandate = ap2.create_mandate(
        user_id=user.user_id,
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id=user.payment_method_id,
        expiry_hours=24,
    )

    signer = RequestSigner(private_pem, key_id=settings.agent_key_id)
    direct_adapters: dict[str, ShopifyMCPAdapter] = {}
    for domain, seed in MERCHANTS.items():
        direct_adapters[domain] = ShopifyMCPAdapter(
            domain,
            StubShopifyTransport(seed_products=seed),
            merchant_display_name=DEMO_MERCHANT_DISPLAY_NAMES.get(domain, domain),
        )
    for domain, meta in LIVE_MERCHANTS.items():
        direct_adapters[domain] = ShopifyMCPAdapter(
            domain,
            LiveShopifyTransport(
                meta["store_url"],
                max_pages=meta.get("max_pages", 3),
            ),
            source_protocol="shopify_storefront",
            merchant_display_name=meta.get("display_name", domain),
        )
    # Longer cache TTLs for the demo: merchant profiles are static stubs, so
    # there's no benefit to the spec-default 60s re-discovery cadence. This keeps
    # the resolved client warm and avoids re-running discovery mid-session.
    # (The library/CLI default stays 60s in config.settings.)
    discovery = UCPProfileDiscovery(db, ttl_seconds=600)
    gateway = MerchantGateway(
        discovery=discovery,
        signer=signer,
        direct_adapters=direct_adapters,
        cache_ttl=600,
    )
    stripe = StripeAdapter(api_key=os.getenv("STRIPE_TEST_KEY") or None)
    ctx = ToolContext(
        db=db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=PaymentGateway(ap2, stripe),
        spending_limiter=SpendingLimiter(db),
        confidence_checker=ConfidenceChecker(),
        user=user,
        session=SessionState(user_id=user.user_id),
    )

    # Anthropic client — graceful no-op if unavailable
    client = _build_anthropic_client_or_none()
    available_merchants = list(direct_adapters.keys())
    gate_provider = WebsocketConfirmProvider()
    orchestrator = OrchestratorAgent(
        client,
        confirmation=gate_provider,
        callbacks=StreamingCallbacks(),  # routes set up by chat router
        mandate_id=mandate.mandate_id,
        available_merchants=available_merchants,
    )

    web_session = WebSession(
        session_id=session_id,
        db=db,
        ctx=ctx,
        orchestrator=orchestrator,
        mandate_id=mandate.mandate_id,
        gate_provider=gate_provider,
    )

    # Wire the cart-event notifier so the agent's add_to_cart tool can
    # push a cart_update event onto this session's SSE queue (and the
    # browser's badge updates in real time on any page).
    def _cart_notifier(evt: dict) -> None:
        try:
            web_session.sse_queue.put_nowait(evt)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    ctx.cart_event_notifier = _cart_notifier

    return web_session


def _build_anthropic_client_or_none():
    """Try to build a real Anthropic client; otherwise return a stub.

    The web app should boot even without an API key (so the static product
    grid works for demos). Chat / agent features will return a polite
    "no key configured" message in that case.
    """
    # Explicit offline override for deterministic browser (Playwright) e2e
    # tests: forces the unconfigured stub regardless of any key present in the
    # environment or .env (the settings backfill otherwise heals an empty key
    # from .env, which would make the test server hit a live model). Gated by
    # an env flag, so it has no effect on a normal run.
    if os.getenv("CARTO_FORCE_OFFLINE") == "1":
        return _UnconfiguredAnthropicClient()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _UnconfiguredAnthropicClient()
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return _UnconfiguredAnthropicClient()
    return AsyncAnthropic(api_key=api_key)


class _UnconfiguredAnthropicClient:
    """Placeholder so the app boots without ANTHROPIC_API_KEY.

    Routers that need real LLM calls should check via ``is_configured``
    before invoking the orchestrator's run() and surface a friendly toast
    if missing.
    """

    is_configured = False

    class _Messages:
        async def create(self, **_):
            raise RuntimeError(
                "ANTHROPIC_API_KEY not configured — chat is offline. "
                "The static product grid + click actions still work."
            )

    def __init__(self):
        self.messages = self._Messages()


# ─── Cookie + lookup ──────────────────────────────────────────────────────


def _read_cookie(request: Request) -> Optional[str]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        return _serializer.loads(raw)
    except BadSignature:
        return None


def _write_cookie(response: Response, session_id: str) -> None:
    token = _serializer.dumps(session_id)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        max_age=60 * 60 * 24,  # 1 day
    )


def get_or_create_session(request: Request) -> WebSession:
    """Idempotent: returns the existing session or creates a new one.

    Use as a dependency in route handlers. Cookie attachment happens in
    the SessionCookieMiddleware so it lands on whatever response the route
    returns — including TemplateResponse/JSONResponse/etc.
    """
    sid = _read_cookie(request)
    if sid and sid in _SESSIONS:
        # Stash on request.state so middleware can re-set the cookie
        # on the way out (with refreshed expiry).
        request.state.session_id = sid
        return _SESSIONS[sid]
    sid = uuid.uuid4().hex
    sess = _make_session(sid)
    _SESSIONS[sid] = sess
    request.state.session_id = sid
    request.state.session_is_new = True
    return sess


class SessionCookieMiddleware:
    """ASGI middleware that attaches the session cookie to every response.

    Reads the session id from ``request.state.session_id`` (populated by
    ``get_or_create_session``) and ensures the signed cookie is present
    on the outgoing response. Works regardless of what the route returns.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                # Starlette stores route-set state on scope under "state"
                # as either a State object (Starlette > 0.40-ish) or a dict.
                state = scope.get("state")
                sid = None
                if state is not None:
                    sid = (
                        state.get("session_id")
                        if isinstance(state, dict)
                        else getattr(state, "session_id", None)
                    )
                if sid:
                    token = _serializer.dumps(sid)
                    cookie = (
                        f"{COOKIE_NAME}={token}; "
                        f"Max-Age={60 * 60 * 24}; "
                        f"HttpOnly; SameSite=strict; Path=/"
                    )
                    headers = list(message.get("headers", []))
                    # Only set if not already there (avoid duplicates)
                    if not any(
                        h[0] == b"set-cookie"
                        and h[1].startswith(f"{COOKIE_NAME}=".encode("latin-1"))
                        for h in headers
                    ):
                        headers.append((b"set-cookie", cookie.encode("latin-1")))
                        message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def get_session_by_id(session_id: str) -> Optional[WebSession]:
    return _SESSIONS.get(session_id)


def get_session_id_from_request(request: Request) -> Optional[str]:
    return _read_cookie(request)


def all_session_ids() -> list[str]:
    return list(_SESSIONS.keys())
