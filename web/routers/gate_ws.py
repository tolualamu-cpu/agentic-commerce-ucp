"""WebSocket bridge between ``WebsocketConfirmProvider`` and the browser.

The provider holds two queues per session:
  - ``outbox``: events FROM orchestrator (gate.open, picker.open, …) → browser
  - ``inbox``:  replies FROM browser ({"decision": ..., "text": ...}) → orchestrator

This router accepts a single WS connection per session, then runs two
fan-out tasks until either side closes:
  - drain outbox → ``websocket.send_json``
  - websocket.receive_json → push onto inbox

Disconnects are surfaced as a cancel reply so the orchestrator's
``inbox.get()`` doesn't deadlock — the provider also has its own
``GATE_REPLY_TIMEOUT_S`` belt-and-braces.

NB: no ``from __future__ import annotations`` — FastAPI needs concrete types.
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.routers.chat import _classify_gate_intent
from web.session import (
    COOKIE_NAME,
    _serializer,
    get_session_by_id,
)
from web.stream_takeover import stream_until_superseded
from itsdangerous import BadSignature

router = APIRouter()


def _session_from_ws(websocket: WebSocket):
    """Resolve the WebSession from the signed cookie carried over WS.

    HTTP dependencies (which expect a Request) can't be reused on a
    WebSocket route — Starlette injects WebSocket and FastAPI's solver
    crashes when the dependency asks for Request. Parse the cookie here.
    """
    raw = websocket.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        sid = _serializer.loads(raw)
    except BadSignature:
        return None
    return get_session_by_id(sid)


@router.websocket("/gate/ws")
async def gate_ws(websocket: WebSocket):
    sess = _session_from_ws(websocket)
    if sess is None:
        await websocket.close(code=4401)  # "unauthorized"-ish
        return
    """One WS per browser session. Bridges gate provider queues.

    Protocol:
      Server → client:
        {"type": "gate.open", "tier": "explicit"|"soft", "gate": {...}}
        {"type": "picker.open", ...}   (reserved for Phase 7f)
      Client → server:
        {"decision": "confirm"|"cancel"|"question", "text": "..."}

    Both directions JSON. Empty / malformed payloads are coerced to
    cancel so the orchestrator can't hang.
    """
    await websocket.accept()
    provider = sess.gate_provider

    # Claim a generation. The instant a NEWER /gate/ws opens (every page
    # navigation auto-reconnects), ``superseded`` resolves and this connection's
    # pump_out retires WITHOUT consuming — so it can't steal the ``gate.open``
    # off the single-consumer ``outbox`` and write it to an abandoned socket.
    # That theft is exactly why "Review purchase" opened no modal. Shared
    # primitive with the SSE takeover (see ``web.stream_takeover``).
    _my_gen, superseded = provider.new_ws_generation()

    # SELF-HEALING replay: if a gate is already awaiting a reply, re-send it to
    # THIS freshly-opened connection at once. This is the permanent fix for the
    # recurring "Review purchase shows no modal, then chat doesn't register"
    # bug. The original gate.open is delivered to a single outbox consumer; if
    # that consumer was a connection the browser already abandoned (navigation /
    # reconnect race), the live page never saw it and the orchestrator stayed
    # blocked in _present forever. Replaying on connect means whichever page is
    # actually open WILL get the modal — delivery no longer hinges on winning a
    # one-shot timing race. Idempotent for the browser (openModal just
    # re-renders the same gate).
    pending = provider.current_gate()
    if pending is not None:
        try:
            await websocket.send_json(pending)
        except Exception:
            # Socket died between accept() and the replay — the pump loop
            # below will tear the connection down; the next reconnect replays.
            return

    async def pump_out():
        # No ``is_disconnected`` arg: the WS has no cheap disconnect poll, but
        # ``pump_in`` raises WebSocketDisconnect on a dead socket and the
        # FIRST_COMPLETED wait below tears this task down. ``superseded`` covers
        # the navigation-overlap case (the reason this rewrite exists).
        async for evt in stream_until_superseded(provider.outbox, superseded):
            await websocket.send_json(evt)

    async def pump_in():
        while True:
            try:
                msg = await websocket.receive_json()
            except (WebSocketDisconnect, asyncio.CancelledError):
                raise
            except Exception:
                # Malformed JSON — treat as cancel so we don't stall the gate
                msg = {"decision": "cancel"}
            msg = msg or {"decision": "cancel"}
            # The gate modal's text field always sends decision="question"
            # (the CONFIRM/CANCEL *buttons* send those decisions directly).
            # A user who *types* "confirm" / "cancel" / "go ahead" must be
            # routed the same way the chat-sidebar path is (chat.py:414):
            # re-classify the free text so a typed "confirm" completes the
            # purchase instead of being handed to the orchestrator as a
            # question (which the model refuses, since confirm/cancel are
            # runtime-handled). Genuine questions ("why this one?") fall
            # through to {"decision": "question", ...} unchanged.
            if (
                isinstance(msg, dict)
                and msg.get("decision") == "question"
                and isinstance(msg.get("text"), str)
                and msg["text"].strip()
            ):
                msg = _classify_gate_intent(msg["text"])
            await provider.inbox.put(msg)

    try:
        out_task = asyncio.create_task(pump_out())
        in_task = asyncio.create_task(pump_in())
        # FIRST_COMPLETED (not FIRST_EXCEPTION): pump_out RETURNS cleanly (no
        # exception) when this connection is superseded by a newer /gate/ws, and
        # we want that to tear the stale connection down immediately rather than
        # leaving it half-alive draining nothing.
        done, pending = await asyncio.wait(
            {out_task, in_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        # Consume any exception on the finished task(s) so asyncio doesn't log
        # "Task exception was never retrieved". A dead-socket send error or a
        # WebSocketDisconnect both just mean this connection is over.
        for t in done:
            t.exception()
    except WebSocketDisconnect:
        pass
    # Note: we deliberately do NOT push a synthetic cancel on disconnect.
    # The auto-reconnect happens during every page navigation, and a stale
    # cancel sitting in the inbox would poison the next gate. The provider
    # has its own GATE_REPLY_TIMEOUT_S backstop for genuine deadlocks, and
    # the provider drains stale messages at the start of every gate.
