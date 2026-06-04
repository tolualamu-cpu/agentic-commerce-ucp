"""Test-only HTTP hooks for deterministic browser (Playwright) e2e tests.

WHY THIS EXISTS
The chat UI's hardest-to-test behaviours are CLIENT-SIDE: the JavaScript in
``_chat_sse.html`` that turns the ``/chat/stream`` SSE burst into DOM (user
bubble, product cards rendered ABOVE the summary, a single summary bubble, the
cart-action confirmation, the absolute badge). Reproducing that burst end-to-end
normally needs a live LLM, which is non-deterministic and slow. These hooks let
a browser test inject an EXACT, ordered SSE burst onto the session's queue — the
same queue the real orchestrator writes to — so the client rendering pipeline is
exercised deterministically, with no model in the loop.

SAFETY
This router is mounted by ``create_app`` ONLY when the environment variable
``CARTO_ENABLE_TEST_HOOKS == "1"``. It is never mounted in normal runs
(``uvicorn web.app:app``) or in CI's app boot, so the ``/__test__/*`` surface
does not exist in any real deployment. The endpoints only ever write to the
CURRENT session's own SSE queue / click-basket (resolved from the caller's
session cookie) — they cannot touch another user's session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from web.session import WebSession, get_or_create_session

router = APIRouter(prefix="/__test__", tags=["test-only"])


@router.post("/sse/emit")
async def emit_sse_events(
    payload: dict,
    sess: WebSession = Depends(get_or_create_session),
):
    """Inject a list of SSE event dicts onto THIS session's queue.

    Body: ``{"events": [{"type": "...", "data": {...}}, ...]}``. Each event is
    delivered to the session's single open ``/chat/stream`` connection exactly
    as if the orchestrator had emitted it, so the browser's ``_chat_sse.html``
    dispatcher renders it. Returns the number queued.
    """
    events = payload.get("events") or []
    queued = 0
    for evt in events:
        if not isinstance(evt, dict) or "type" not in evt:
            continue
        evt.setdefault("data", {})
        sess.sse_queue.put_nowait(evt)
        queued += 1
    return JSONResponse({"queued": queued})


@router.post("/cart/reset")
async def reset_cart(sess: WebSession = Depends(get_or_create_session)):
    """Empty this session's click-basket (browser-test isolation helper)."""
    sess.click_basket.clear()
    return JSONResponse({"status": "cleared"})
