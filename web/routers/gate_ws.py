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

from web.session import (
    COOKIE_NAME,
    _serializer,
    get_session_by_id,
)
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

    async def pump_out():
        while True:
            evt = await provider.outbox.get()
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
            await provider.inbox.put(msg or {"decision": "cancel"})

    try:
        out_task = asyncio.create_task(pump_out())
        in_task = asyncio.create_task(pump_in())
        done, pending = await asyncio.wait(
            {out_task, in_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    # Note: we deliberately do NOT push a synthetic cancel on disconnect.
    # The auto-reconnect happens during every page navigation, and a stale
    # cancel sitting in the inbox would poison the next gate. The provider
    # has its own GATE_REPLY_TIMEOUT_S backstop for genuine deadlocks, and
    # the provider drains stale messages at the start of every gate.
