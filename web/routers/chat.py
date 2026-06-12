"""Chat router — bridges the orchestrator's run() loop to a browser via SSE.

Two endpoints:
  - POST /chat — accepts a user message, kicks off ``orchestrator.run()``
    as a background task. Returns immediately with 202; the streamed
    output is consumed via the SSE endpoint.
  - GET /chat/stream — Server-Sent Events stream. Drains the session's
    ``sse_queue`` (populated by callbacks wired in ``build_web_callbacks``)
    and emits ``text/event-stream`` frames the browser can render.

The per-session ``orchestrator_lock`` serialises overlapping submissions
(click-while-typing) so the orchestrator state isn't mutated concurrently.

NB: NO ``from __future__ import annotations`` — FastAPI introspects types.
"""

import asyncio
import json
import sys
import traceback


def _dbg(*args):
    """Diagnostic print that always goes to stdout so uvicorn captures it."""
    print("[chat]", *args, file=sys.stdout, flush=True)


# Phase 8h: when the chat router forwards a user message onto an
# active gate's inbox, classify the intent first. Without this, every
# chat-routed message lands as ``decision: "question"`` and the user
# can never confirm or cancel a purchase via chat — they'd be stuck
# unless they used the modal's buttons.
#
# Matching uses word-boundary regex so natural phrasings like "now confirm",
# "please proceed", "go ahead and approve" are correctly routed rather than
# falling through to the orchestrator's Q&A handler as unrecognised intent.
# A negation guard ("don't confirm", "do not proceed") routes to cancel.
import re as _re

_NEGATION_CONFIRM_RE = _re.compile(r"\b(don't|do not|not)\b.*\b(confirm|approve|proceed)\b")
_CONFIRM_RE = _re.compile(
    r"\b(confirm|approve|proceed|go ahead|buy it|purchase it|yes buy|ok buy)\b"
)
_CANCEL_RE = _re.compile(
    r"\b(cancel|abort|nevermind|never mind|don't buy|do not buy|no thanks|nope)\b"
)
# Short standalone words that unambiguously mean cancel when the entire message
# is just that word. NOT added to _CANCEL_RE to avoid false-positive matches
# in longer messages like "no, add 2 more" → should be a question.
_CANCEL_EXACT = frozenset({"no", "nope", "stop", "abort", "exit", "back"})


def _classify_gate_intent(text: str) -> dict:
    """Map a chat message to a gate-inbox payload.

    Returns one of:
      {"decision": "confirm"}
      {"decision": "cancel"}
      {"decision": "question", "text": <original>}

    Matching is case-insensitive, trims leading/trailing whitespace and
    trailing punctuation, and uses word-boundary regex so natural phrasings
    like "now confirm" or "please proceed" are correctly routed. A negation
    guard ensures "don't confirm" / "do not proceed" route as cancel rather
    than confirm. Short standalone exact-match words ("no", "stop", "back")
    are checked separately so they only cancel when the whole message is that
    word, preventing false positives in messages like "no, add 2 more".
    """
    if not text:
        return {"decision": "question", "text": ""}
    normalised = text.strip().strip(".!?,;:").lower()
    if _NEGATION_CONFIRM_RE.search(normalised):
        return {"decision": "cancel"}
    if _CONFIRM_RE.search(normalised):
        return {"decision": "confirm"}
    if normalised in _CANCEL_EXACT:
        return {"decision": "cancel"}
    if _CANCEL_RE.search(normalised):
        return {"decision": "cancel"}
    return {"decision": "question", "text": text}


from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from tools.shared_tools import audit_log
from web.callbacks import build_web_callbacks
from web.session import WebSession, get_or_create_session
from web.stream_takeover import KEEPALIVE, stream_until_superseded


def _cart_key(merchant_domain: str, product_id: str) -> str:
    return f"{merchant_domain}:{product_id}"


def _in_cart(click_basket: dict, merchant_domain: str, product_id: str) -> bool:
    items = click_basket.get(merchant_domain, [])
    return any(i.get("product_id") == product_id for i in items)


async def _enrich_products_with_images(ctx, products: list) -> list:
    """Backfill fields the discovery agent (Claude Haiku) drops when
    serializing tool-result products to its own JSON output.

    Haiku is unreliable about copying ALL fields from search_products
    results — historically `images` would go missing, and we've also
    observed `url` and `brand` being dropped (which kills the "Buy on
    {merchant}" external link on cards). For each product dict, look up
    the canonical record from the in-memory adapter and fill in any
    missing fields (no network call — direct adapter call).

    This is a DEFENSIVE safety net on top of the DISCOVERY prompt which
    already lists `images`, `url`, and `brand` as required fields. Even
    if a future prompt tweak loses one of those, the cards still render
    correctly with the right links.
    """
    enriched = []
    for p in products:
        d = p if isinstance(p, dict) else p.model_dump(mode="json")
        needs_lookup = not d.get("images") or not d.get("url") or not d.get("brand")
        if needs_lookup:
            merchant = d.get("merchant_domain", "")
            pid = d.get("product_id", "")
            adapter = getattr(ctx.merchant_gateway, "direct_adapters", {}).get(merchant)
            if adapter and pid:
                try:
                    full = await adapter.get_product(pid)
                    if full:
                        if not d.get("images") and getattr(full, "images", None):
                            d = {**d, "images": list(full.images)}
                        if not d.get("url") and getattr(full, "url", None):
                            d = {**d, "url": full.url}
                        if not d.get("brand") and getattr(full, "brand", None):
                            d = {**d, "brand": full.brand}
                except Exception:  # noqa: BLE001
                    pass
        enriched.append(d)
    return enriched


def _product_id_set(products: list) -> set:
    """Return the set of product_ids from a list of ProductResult or dicts."""
    ids = set()
    for p in products:
        if isinstance(p, dict):
            ids.add(p.get("product_id", ""))
        else:
            ids.add(getattr(p, "product_id", ""))
    return ids


def _strip_orphaned_tool_use(conversation: list) -> list:
    """Remove assistant turns containing only tool_use blocks that are NOT
    immediately followed by a user turn with matching tool_result blocks.
    Also removes any user turns whose tool_result blocks have no preceding
    tool_use (these become orphaned when the tool_use turn is stripped, or
    can arrive that way after a mid-run server restart).

    This surgically heals conversations that were corrupted by mid-run
    mutations (e.g., a cart click note injected between a tool_use and its
    expected tool_result, or a server restart that cut off a tool call).
    Called defensively at the start of each orchestrator run.
    """
    if not conversation:
        return conversation

    repaired = []
    i = 0
    while i < len(conversation):
        turn = conversation[i]
        role = turn.get("role", "")
        content = turn.get("content", [])

        # Identify an assistant turn that is purely tool_use blocks
        if role == "assistant" and isinstance(content, list):
            tool_use_ids = {
                block.get("id")
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            }
            if tool_use_ids:
                # Check whether the NEXT turn is a user turn with matching
                # tool_result blocks for every tool_use id
                next_turn = conversation[i + 1] if i + 1 < len(conversation) else None
                if next_turn is None or next_turn.get("role") != "user":
                    # No following turn at all → orphaned, skip
                    _dbg(
                        f"stripping orphaned tool_use turn (no following user turn): {tool_use_ids}"
                    )
                    i += 1
                    continue
                next_content = next_turn.get("content", [])
                result_ids = {
                    block.get("tool_use_id")
                    for block in next_content
                    if isinstance(block, dict) and block.get("type") == "tool_result"
                }
                if not tool_use_ids.issubset(result_ids):
                    # Following turn does not have all expected tool_results.
                    # Strip the tool_use turn. Also skip the following user turn
                    # if it has any tool_result blocks — those reference the
                    # tool_use IDs we just discarded and would cause a 400 error
                    # ("unexpected tool_use_id in tool_result blocks").
                    _dbg(
                        f"stripping orphaned tool_use turn (missing tool_results): "
                        f"expected={tool_use_ids}, got={result_ids}"
                    )
                    i += 1  # skip the tool_use assistant turn
                    if result_ids:
                        # The following user turn has some tool_result blocks —
                        # skip it too so no orphaned tool_results remain.
                        _dbg(f"also skipping following tool_result user turn: {result_ids}")
                        i += 1
                    continue

        repaired.append(turn)
        i += 1

    # Second pass: belt-and-suspenders check for any user turns that still
    # contain tool_result blocks without a valid preceding assistant tool_use.
    # This catches cases where the conversation was stored in an inconsistent
    # state before this function was called (e.g. after a server restart that
    # interrupted a tool call mid-flight and left a dangling tool_result).
    final = []
    for idx, turn in enumerate(repaired):
        role = turn.get("role", "")
        content = turn.get("content", [])
        if role == "user" and isinstance(content, list):
            orphaned_result_ids = {
                b.get("tool_use_id")
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id")
            }
            if orphaned_result_ids:
                # Check whether the immediately preceding turn in `final`
                # is an assistant turn with matching tool_use blocks.
                prev = final[-1] if final else None
                if prev is not None and prev.get("role") == "assistant":
                    prev_content = prev.get("content") or []
                    prev_tool_use_ids = {
                        b.get("id")
                        for b in prev_content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    }
                    if orphaned_result_ids.issubset(prev_tool_use_ids):
                        # Valid pair — keep as-is
                        final.append(turn)
                        continue
                # No valid preceding tool_use — strip tool_result blocks,
                # keeping any plain-text content the turn might also have.
                other = [
                    b
                    for b in content
                    if not (isinstance(b, dict) and b.get("type") == "tool_result")
                ]
                if other:
                    _dbg(
                        f"stripped tool_result blocks from user turn (kept {len(other)} other blocks)"
                    )
                    final.append({**turn, "content": other})
                else:
                    _dbg(f"dropped orphaned tool_result user turn entirely: {orphaned_result_ids}")
                continue
        final.append(turn)

    if len(final) != len(conversation):
        _dbg(f"conversation repaired: {len(conversation)} → {len(final)} turns")
    return final


async def _wait_for_lock(lock: asyncio.Lock) -> None:
    """Acquire-then-release helper so we can use ``asyncio.wait_for``
    to bound the wait. Returns once the lock is briefly held — i.e.,
    no orchestrator is mid-run."""
    async with lock:
        return


router = APIRouter()


@router.post("/chat/products-fragment", response_class=HTMLResponse)
async def products_fragment(
    request: Request,
    sess: WebSession = Depends(get_or_create_session),
):
    """Render a list of chat product cards from JSON body.

    Accepts: {"products": [...ProductResult-shaped dicts...]}
    Returns: HTML partial — one _chat_product_card.html per product.

    Called by the browser when it receives a `products` SSE event so that
    cards are server-rendered (Jinja2) and injected into the chat log.
    Cart state is derived from the live session basket so buttons reflect
    whether each product is already in the draft cart.
    """
    try:
        body = await request.json()
        products_raw = body.get("products", [])
    except Exception:
        return HTMLResponse("")

    templates = request.app.state.templates
    basket = sess.ctx.session.click_basket

    parts: list[str] = []
    for p in products_raw:
        if not isinstance(p, dict):
            continue
        merchant = p.get("merchant_domain", "")
        product_id = p.get("product_id", "")
        in_cart = _in_cart(basket, merchant, product_id)
        rendered = templates.TemplateResponse(
            request,
            "_chat_product_card.html",
            context={"product": p, "in_cart": in_cart},
        )
        parts.append(rendered.body.decode())

    return HTMLResponse("\n".join(parts))


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """Render the conversation page.

    Empty state: hero-style centered prompt + suggestion chips, no log.
    Active state: bubbles from ``session.conversation`` rendered top-down
    with the input flowing right below the last bubble.

    All the heavy lifting (history rendering, dedup, SSE wiring) lives
    in the templates — this handler just hands the session over.
    """
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "chat.html")


@router.post("/chat/reset")
async def reset_chat(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """Reset the visible conversation while preserving long-lived state.

    Cleared: ``conversation``, ``last_discovered_products``,
    ``open_checkout_sessions``, and any pending SSE events queued for
    this session.

    Preserved: the mandate, the click_basket (items the user added via
    Add to cart), the orchestrator instance, the audit log (which gains
    a ``reset_chat`` entry), the user identity, and the underlying DB.

    The user's intent is "start a different shopping trip", not "log
    out and forget everything I bought." A separate Clear button on
    ``/cart`` handles cart wiping.
    """
    # Bound wait for any in-flight orchestrator to finish, so the next
    # /chat render doesn't capture a half-written turn. 3s is generous;
    # if the orchestrator is still mid-tool-call we reset anyway and
    # its lingering events get discarded with the queue drain below.
    try:
        await asyncio.wait_for(
            _wait_for_lock(sess.orchestrator_lock),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        _dbg("reset: orchestrator still running after 3s; resetting anyway")

    # Drain any pending SSE events; subsequent page load gets a fresh queue
    while not sess.sse_queue.empty():
        try:
            sess.sse_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    # Clear conversation-scoped state
    sess.ctx.session.conversation.clear()
    sess.ctx.session.last_discovered_products.clear()
    sess.ctx.session.open_checkout_sessions.clear()
    sess.ctx.session.product_card_sets.clear()

    # Audit the reset so it's traceable
    await audit_log(
        sess.ctx,
        agent="WebUI",
        tool="reset_chat",
        action="user clicked reset on /chat",
        mandate_id=sess.mandate_id,
        args={},
    )

    return RedirectResponse("/chat", status_code=303)


@router.post("/chat")
async def post_chat(
    request: Request,
    message: str = Form(...),
    sess: WebSession = Depends(get_or_create_session),
):
    """Enqueue a user message and start the orchestrator in the background.

    Returns 202 immediately so the browser can pivot to consuming the
    /chat/stream SSE feed. If the Anthropic client is unconfigured we
    emit a friendly "chat offline" event onto the queue rather than 500.
    """
    text = (message or "").strip()
    if not text:
        return JSONResponse({"error": "empty message"}, status_code=400)

    # Echo the user's own line into the stream so the chat UI can append it
    await sess.sse_queue.put({"type": "user", "data": {"text": text}})

    # Phase 8g: if a gate is currently active (the orchestrator is
    # blocked inside ``confirmation.explicit_confirm`` awaiting input),
    # route the user's chat message to the gate's inbox AS A QUESTION
    # instead of starting a new orchestrator run. Without this, the
    # second POST would deadlock on ``orchestrator_lock`` (held by the
    # already-running orchestrator) AND the gate would never receive
    # the user's input.
    #
    # Reuses the existing gate Q&A loop: a mutation parses correctly
    # (e.g. "remove 1"), a non-mutating question gets answered, and
    # the orchestrator decides whether to keep the gate alive.
    if getattr(sess.gate_provider, "awaiting_input", False):
        payload = _classify_gate_intent(text)
        try:
            await sess.gate_provider.inbox.put(payload)
        except Exception as exc:  # noqa: BLE001
            _dbg("failed to route chat to active gate:", type(exc).__name__, exc)
            return JSONResponse(
                {"error": "could not route to active gate"},
                status_code=500,
            )
        return JSONResponse(
            {"status": "routed_to_gate", "decision": payload["decision"]},
            status_code=202,
        )

    # Persist the user turn into the conversation SYNCHRONOUSLY, before we
    # return 202 and the empty-state form navigates to /chat.
    #
    # Why this matters (the "flicker" bug): the empty /chat hero hides the
    # chat-log and navigates to /chat after a successful POST so the page
    # re-renders in the active (streaming) state. But the orchestrator runs
    # in a BACKGROUND task and only appends the user turn once
    # ``BaseAgent.run`` executes inside it — which is AFTER this handler has
    # already returned and the browser has already navigated. The GET /chat
    # therefore raced an empty ``session.conversation`` and re-rendered the
    # EMPTY hero, while the streamed reply landed in a hidden #chat-log
    # (invisible). The user saw nothing happen, re-submitted, and the page
    # flickered through repeated reloads.
    #
    # Appending here means the post-navigation GET /chat sees a non-empty
    # history and renders the ACTIVE state (user bubble visible, #chat-log
    # visible), so the SSE reply streams into a visible log. This is exactly
    # the "PARTIAL" dedup case the chat SSE handler was built for: the user
    # turn is already in the server-rendered history, the queued "user" SSE
    # event is de-duplicated, and only the agent reply renders live.
    #
    # ``BaseAgent.run`` guards against re-appending this identical last turn,
    # so the model's context is not duplicated.
    sess.ctx.session.conversation.append({"role": "user", "content": text})

    # Wire the orchestrator's callbacks to push deltas onto this session's
    # SSE queue. Safe to overwrite each turn — only one run() at a time
    # thanks to ``orchestrator_lock``.
    sess.orchestrator.callbacks = build_web_callbacks(sess.sse_queue)

    # If chat is offline (no API key), short-circuit with a friendly note
    if not getattr(sess.orchestrator._client, "is_configured", True):
        await sess.sse_queue.put(
            {
                "type": "text",
                "data": {
                    "delta": (
                        "Chat is offline — ANTHROPIC_API_KEY isn't set. "
                        "Try clicking 'Add to cart' on a product to exercise "
                        "the click-flow instead."
                    )
                },
            }
        )
        await sess.sse_queue.put({"type": "done", "data": {}})
        return JSONResponse({"status": "offline"}, status_code=202)

    # Kick off run() in the background; the lock prevents concurrent turns.
    asyncio.create_task(_run_orchestrator(sess, text))
    return JSONResponse({"status": "accepted"}, status_code=202)


async def _run_orchestrator(sess: WebSession, text: str) -> None:
    """Run the orchestrator under the per-session lock and emit a final
    'done' marker so the client can close its loading state.

    Product cards are emitted AFTER the run completes (not mid-run) so
    that cart interactions on those cards cannot race with the in-flight
    tool loop. The sequence is:
      1. Repair any orphaned tool_use blocks from a previous corruption
      2. Snapshot which product IDs are already cached
      3. Run the orchestrator to completion (all tool_use/tool_result paired)
      4. If discovery was called (cached product IDs changed), push a
         ``products`` SSE event so the browser renders product cards
      5. Push the text reply
      6. Push ``done``

    NB: the orchestrator's ``run()`` returns ``{"reply": "..."}`` (the
    final assistant text) but does NOT stream text via ``on_text`` — that
    callback only fires for in-flow synthetic messages (gate Q&A, search
    sub-flow). So after the run completes we push the reply text onto
    the SSE queue ourselves; the CLI does the equivalent via Rich.
    """
    async with sess.orchestrator_lock:
        try:
            # Repair any corrupted conversation before re-running. This
            # heals state left by a previous race-condition incident.
            sess.ctx.session.conversation[:] = _strip_orphaned_tool_use(
                sess.ctx.session.conversation
            )

            # Snapshot product IDs cached before this run so we can detect
            # whether discovery was called (product set changed).
            products_before = _product_id_set(sess.ctx.session.last_discovered_products)

            _dbg("running:", repr(text))
            result = await sess.orchestrator.run(sess.ctx, text)

            # Surface the FULL result to the server log so we can debug
            # purchase failures that the agent renders as friendly text.
            _dbg("result:", json.dumps(result, default=str)[:2000] if result else result)
            # Also dump the last ~4 conversation turns so we can see tool
            # results that led to the failure (validate_mandate, etc.).
            convo = sess.ctx.session.conversation[-6:]
            _dbg("last conversation turns:")
            for turn in convo:
                _dbg("  ", json.dumps(turn, default=str)[:600])

            # Emit product cards if discovery was called this turn.
            # Fired here (post-run) so the conversation is fully settled —
            # no tool_use blocks are pending, eliminating the race condition
            # where a cart click appended a user message between tool_use
            # and tool_result.
            emitted_products = False
            discovered = sess.ctx.session.last_discovered_products
            products_after = _product_id_set(discovered)
            _dbg(
                f"emit-decision: products_before={len(products_before)} "
                f"products_after={len(products_after)} "
                f"changed={products_after != products_before} "
                f"discovered_count={len(discovered) if discovered else 0}"
            )
            if discovered and products_after != products_before:
                product_dicts = [
                    p if isinstance(p, dict) else p.model_dump(mode="json") for p in discovered
                ]
                # Enrich any products missing images (discovery agent may have
                # omitted the images field when serialising the tool result).
                product_dicts = await _enrich_products_with_images(sess.ctx, product_dicts)
                _dbg(f"emitting products event: {len(product_dicts)} cards")
                await sess.sse_queue.put(
                    {
                        "type": "products",
                        "data": {"products": product_dicts},
                    }
                )
                # Persist card set so it survives page reload — linked to
                # the current conversation length so _chat_log.html can
                # re-render the cards in the right position.
                sess.ctx.session.product_card_sets.append(
                    {
                        "turn_count": len(sess.ctx.session.conversation),
                        "products": product_dicts,
                    }
                )
                emitted_products = True

            # Drain any cards the agent explicitly asked the UI to re-render
            # via the ``show_product_cards`` tool (e.g. "show me that card
            # again"). This is meant for re-show turns where discovery did NOT
            # run. If discovery DID run this turn (set-change block above
            # already emitted), the model may *also* have called
            # show_product_cards on the same freshly-discovered set — emitting
            # again here would duplicate the cards in the UI. So we only drain
            # when discovery did not already emit. Either way we clear the
            # staged list so it can never bleed into the next turn.
            to_show = sess.ctx.session.cards_to_show
            _dbg(
                f"show_product_cards drain: staged={len(to_show)} "
                f"emitted_products_already={emitted_products}"
            )
            if to_show and not emitted_products:
                show_dicts = [
                    p if isinstance(p, dict) else p.model_dump(mode="json") for p in to_show
                ]
                show_dicts = await _enrich_products_with_images(sess.ctx, show_dicts)
                _dbg(f"emitting products event (from cards_to_show): {len(show_dicts)} cards")
                await sess.sse_queue.put(
                    {
                        "type": "products",
                        "data": {"products": show_dicts},
                    }
                )
                sess.ctx.session.product_card_sets.append(
                    {
                        "turn_count": len(sess.ctx.session.conversation),
                        "products": show_dicts,
                    }
                )
            # Always clear — whether we emitted, or skipped because discovery
            # already showed these cards this turn.
            sess.ctx.session.cards_to_show = []

            reply = ""
            if isinstance(result, dict):
                reply = (result.get("reply") or result.get("raw") or "").strip()
            if reply:
                await sess.sse_queue.put(
                    {
                        "type": "text",
                        "data": {"delta": reply},
                    }
                )
        except Exception as exc:  # noqa: BLE001
            _dbg("orchestrator.run raised:", type(exc).__name__, exc)
            _dbg(traceback.format_exc())
            await sess.sse_queue.put(
                {
                    "type": "error",
                    "data": {"message": f"{type(exc).__name__}: {exc}"},
                }
            )
        finally:
            await sess.sse_queue.put({"type": "done", "data": {}})


# Re-exported sentinel: yielded when the queue idles past the heartbeat
# timeout. The endpoint maps it to an SSE keepalive comment. Aliased from the
# shared takeover module so existing imports of ``chat._KEEPALIVE`` keep
# working and identity comparisons hold.
_KEEPALIVE = KEEPALIVE


async def _session_sse_events(sess, superseded, is_disconnected, *, timeout: float = 15.0):
    """Yield queued SSE events for ONE ``/chat/stream`` connection.

    Thin wrapper over the shared ``stream_until_superseded`` takeover primitive
    (see ``web.stream_takeover``): each connection claims a generation via
    ``sess.new_stream_generation()`` and stops consuming the instant a newer
    connection supersedes it, so the navigating-away page can never steal the
    orchestrator's ``products``/``text``/``done`` burst from the active page.

    Yields either an event ``dict`` or the ``_KEEPALIVE`` sentinel (idle
    timeout). ``timeout`` is parameterised for tests; production uses 15s so
    proxies don't drop an idle connection.
    """
    async for item in stream_until_superseded(
        sess.sse_queue, superseded, timeout=timeout, is_disconnected=is_disconnected
    ):
        yield item


def _cart_item_count(sess) -> int:
    """Absolute number of items in the session's click-basket.

    Single source of truth for the SSE badge resync. Mirrors the count the
    cart router computes in ``_cart_summary`` (sum of line quantities) but
    without importing the cart module (avoids a router import cycle).
    """
    return sum(
        int(i.get("quantity", 0))
        for items in getattr(sess, "click_basket", {}).values()
        for i in items
    )


def _cart_resync_event(sess) -> "dict | None":
    """The ``cart_update`` frame ``/chat/stream`` leads every (re)connect with,
    so the header badge converges to the correct absolute count on any page
    load / navigation / reconnect. ``None`` only if the count can't be read
    (best-effort — never break the stream)."""
    try:
        return {"type": "cart_update", "data": {"count": _cart_item_count(sess)}}
    except Exception:  # noqa: BLE001 — badge resync is best-effort
        return None


@router.get("/chat/stream")
async def chat_stream(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """SSE endpoint that drains the session's queue.

    Emits one ``data:`` line per event with a JSON-encoded payload.
    The client (htmx/sse.js or raw EventSource) decodes and dispatches
    by ``type``. Claims a stream generation so a newer connection for the
    same session cleanly supersedes this one (see ``_session_sse_events``).
    """
    _my_gen, superseded = sess.new_stream_generation()

    async def event_gen():
        # SELF-HEALING badge resync: the first frame on every (re)connect is the
        # current absolute cart count. The header badge therefore converges to
        # correct on every page load / navigation / SSE reconnect, even if a
        # live ``cart_update`` frame was missed earlier. Pairs with the
        # optimistic ``__bumpCart`` on the cards so the badge is both instant
        # and eventually-consistent. Best-effort: never block the stream.
        _dbg(f"stream open gen={_my_gen}")
        resync = _cart_resync_event(sess)
        if resync is not None:
            yield f"data: {json.dumps(resync)}\n\n"
        try:
            async for item in _session_sse_events(sess, superseded, request.is_disconnected):
                if item is _KEEPALIVE:
                    # Heartbeat keeps proxies from closing the connection
                    yield ": keepalive\n\n"
                else:
                    etype = item.get("type", "?") if isinstance(item, dict) else "raw"
                    _dbg(f"stream gen={_my_gen} writing {etype}")
                    yield f"data: {json.dumps(item)}\n\n"
        finally:
            _dbg(f"stream close gen={_my_gen}")

    return StreamingResponse(event_gen(), media_type="text/event-stream")
