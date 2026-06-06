"""FastAPI application factory for the Agentic Commerce web UI.

Boot with:
    uvicorn web.app:app --reload

The app boots without ANTHROPIC_API_KEY (chat will surface a friendly
"chat offline" message); the static product grid + click actions work
without LLM access.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Load .env BEFORE any module reads os.getenv (session.py, settings.py, etc.)
load_dotenv()

WEB_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_ROOT / "templates"
STATIC_DIR = WEB_ROOT / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic Commerce")

    # Mount static and templates
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Jinja global: chat_history(request) — server-renders the persistent
    # conversation into the chat sidebar on every page so the visible log
    # survives navigation. Server state in session.conversation is the
    # source of truth; the client log is just a view.
    #
    # Gate Q&A turns (prefixed "[at confirmation gate]") are intentionally
    # excluded from the rendered log: they are internal orchestrator context
    # kept in session.conversation for Anthropic API adjacency requirements
    # only, and must not surface as user-visible chat bubbles.
    def chat_history(request):
        try:
            from web.session import get_session_id_from_request, get_session_by_id

            sid = get_session_id_from_request(request)
            if not sid:
                return []
            sess = get_session_by_id(sid)
            if sess is None:
                return []
            out = []
            for idx, turn in enumerate(sess.ctx.session.conversation):
                role = turn.get("role")
                content = turn.get("content")
                # Content can be a string OR a list of {type, text, ...} blocks
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    # Skip "intermediary model thoughts": an assistant turn
                    # that ALSO contains tool_use blocks is the model
                    # reasoning before/between tool calls (e.g. "Let me
                    # search for that" + a search tool_use in one turn).
                    # That text is never streamed live — only the final
                    # text-only reply (result["reply"]) is pushed to the
                    # SSE queue — so surfacing it on reload would show
                    # bubbles the user never saw mid-conversation. Drop it.
                    if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
                        continue
                    text = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    text = ""
                if not text.strip():
                    continue
                # Skip internal gate Q&A context turns — not user-visible.
                if text.startswith("[at confirmation gate]"):
                    continue
                # turn_count is 1-indexed position after this turn is appended;
                # matches what _run_orchestrator records in product_card_sets.
                out.append({"role": role, "text": text, "turn_count": idx + 1})
            return out
        except Exception:  # noqa: BLE001 — never break a page render
            return []

    templates.env.globals["chat_history"] = chat_history

    # Jinja filter: money(value) — format a Decimal/str/number as a
    # currency amount with exactly two decimal places (e.g. 193.3200 →
    # "193.32", 1000 → "1000.00"). Used by the daily-spend bar and any
    # other surface that renders a dollar figure. Falls back to the raw
    # value if it can't be parsed so a bad value never breaks a render.
    def _money(value):
        from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

        try:
            d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError, TypeError):
            return value
        return f"{d:.2f}"

    templates.env.filters["money"] = _money

    # Jinja global: mandate_info(request) — returns the active mandate and
    # today's spend so every page can render the Carto daily-limit widget
    # in the navbar without requiring individual routers to pass context.
    def mandate_info(request):
        # Per-request memoized: a single page render references this global
        # from several partials (navbar widget, gate modal, etc.). The
        # underlying ap2._compute_spend scans spend_records, so without the
        # cache we'd re-scan the DB N times per page. Cache lives on
        # request.state and is never shared across requests, so spend always
        # reflects the current state at render time.
        _state = getattr(request, "state", None)
        cached = getattr(_state, "_mandate_info_cache", None) if _state is not None else None
        if cached is not None:
            return cached
        try:
            from datetime import datetime, timezone
            from web.session import get_session_id_from_request, get_session_by_id

            sid = get_session_id_from_request(request)
            if not sid:
                result = {}
            else:
                sess = get_session_by_id(sid)
                if sess is None:
                    result = {}
                else:
                    mandate = sess.ctx.ap2.get_mandate(sess.mandate_id)
                    if mandate is None:
                        result = {}
                    else:
                        spent_day, _ = sess.ctx.ap2._compute_spend(
                            sess.mandate_id, datetime.now(timezone.utc)
                        )
                        result = {"mandate": mandate, "spent_today": str(spent_day)}
        except Exception:  # noqa: BLE001 — never break a page render
            result = {}
        try:
            request.state._mandate_info_cache = result
        except Exception:  # noqa: BLE001 — state may be unavailable in odd contexts
            pass
        return result

    templates.env.globals["mandate_info"] = mandate_info

    # Jinja global: product_card_sets(request) — returns the list of
    # {turn_count, products} dicts stored in SessionState so _chat_log.html
    # can re-render product cards alongside the correct conversation turn.
    def product_card_sets(request):
        try:
            from web.session import get_session_id_from_request, get_session_by_id

            sid = get_session_id_from_request(request)
            if not sid:
                return []
            sess = get_session_by_id(sid)
            if sess is None:
                return []
            return sess.ctx.session.product_card_sets
        except Exception:  # noqa: BLE001
            return []

    templates.env.globals["product_card_sets"] = product_card_sets

    # Jinja global: cart_item_count(request) — used by the header's cart
    # badge so users see live feedback after every click-to-add.
    def cart_item_count(request):
        try:
            from web.session import get_session_id_from_request, get_session_by_id

            sid = get_session_id_from_request(request)
            if not sid:
                return 0
            sess = get_session_by_id(sid)
            if sess is None:
                return 0
            total = 0
            for items in sess.click_basket.values():
                for it in items:
                    total += int(it.get("quantity", 0) or 0)
            return total
        except Exception:  # noqa: BLE001
            return 0

    templates.env.globals["cart_item_count"] = cart_item_count

    # Session cookie middleware — must wrap before router registration so
    # all responses (HTML, JSON, error pages) get the cookie attached.
    from web.session import SessionCookieMiddleware

    app.add_middleware(SessionCookieMiddleware)

    # Register routers
    from web.routers import account as account_router
    from web.routers import cart as cart_router
    from web.routers import chat as chat_router
    from web.routers import gate_ws as gate_ws_router
    from web.routers import products as products_router

    app.include_router(products_router.router)
    app.include_router(chat_router.router)
    app.include_router(cart_router.router)
    app.include_router(gate_ws_router.router)
    app.include_router(account_router.router)

    # Test-only SSE injection hooks for deterministic browser (Playwright)
    # e2e tests. Mounted ONLY when CARTO_ENABLE_TEST_HOOKS=1, so the
    # /__test__/* surface never exists in a normal run or real deployment.
    import os

    if os.getenv("CARTO_ENABLE_TEST_HOOKS") == "1":
        from web.routers import test_hooks as test_hooks_router

        app.include_router(test_hooks_router.router)

    return app


# Module-level instance for uvicorn entry point
app = create_app()
