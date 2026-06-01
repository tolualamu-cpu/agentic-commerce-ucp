"""Adapter: orchestrator streaming callbacks → per-session SSE queue.

The orchestrator already has a ``StreamingCallbacks`` slot for ``on_text``,
``on_tool_start``, ``on_tool_end``, ``on_gate``. The CLI binds them to
Rich console output. The web binds them to a per-session ``asyncio.Queue``
that the SSE endpoint drains.
"""

import json
from typing import Any

from agents.orchestrator import StreamingCallbacks


def build_web_callbacks(queue) -> StreamingCallbacks:
    """Return a StreamingCallbacks that pushes JSON-serializable events
    onto ``queue`` (typically a ``WebSession.sse_queue``).

    Each event is a dict shaped:
      {"type": "text"|"tool_start"|"tool_end"|"gate", "data": {...}}
    The SSE handler serialises and emits these as text/event-stream frames.
    """

    async def on_text(delta: str) -> None:
        await queue.put({"type": "text", "data": {"delta": delta}})

    async def on_tool_start(name: str, args: dict) -> None:
        # Strip non-serialisable values defensively
        safe_args = {k: _safe(v) for k, v in (args or {}).items()}
        await queue.put({"type": "tool_start", "data": {"name": name, "args": safe_args}})

    async def on_tool_end(name: str, result: Any) -> None:
        # Result can be a dict / Pydantic-ish — coerce to a string preview
        # so we don't blast huge JSON into the SSE stream.
        preview = _safe(result)
        if isinstance(preview, (dict, list)):
            preview = json.dumps(preview, default=str)[:300]
        await queue.put({"type": "tool_end", "data": {"name": name, "preview": preview}})

    async def on_gate(tier: str, gate_data) -> None:
        await queue.put({"type": "gate", "data": {"tier": tier}})

    async def on_bubble_end() -> None:
        await queue.put({"type": "bubble_end", "data": {}})

    return StreamingCallbacks(
        on_text=on_text,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        on_gate=on_gate,
        on_bubble_end=on_bubble_end,
    )


def _safe(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, dict):
        return {k: _safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_safe(x) for x in v]
    return str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
