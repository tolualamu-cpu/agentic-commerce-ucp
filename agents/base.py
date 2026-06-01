"""BaseAgent — shared tool-loop runner for every subagent.

Why a base class:
  - The Anthropic SDK tool-loop pattern is mechanical and identical across
    every subagent (Discovery / Evaluation / Purchase / Tracking) and the
    Orchestrator. Doing it once here means one place to fix bugs.
  - Tool functions live in Phase 2 (``tools/*``). The base class wraps each
    one with a name + Anthropic-format input schema and dispatches by name.
  - The Anthropic client itself is injected so tests use a FakeAnthropicClient
    with scripted responses and no API key is required.

The shape of the client we depend on (Protocol):
    async def messages.create(*, model, system, tools, messages, max_tokens) -> Message
    where Message has .content (list of blocks) and .stop_reason (str).
    Each block has .type, plus .text or (.id, .name, .input).
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from tools.context import ToolContext


# ─── Anthropic-shaped Protocol (so we don't import the SDK here) ────────────


class AnthropicLike(Protocol):
    """Minimal duck-typed contract for whichever client is injected."""

    messages: Any  # exposes .create(...) coroutine


# ─── Tool spec / dispatch ───────────────────────────────────────────────────


@dataclass
class ToolSpec:
    """One agent-callable tool. ``handler`` is the Phase 2 tool function."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[Any]]
    # Whether the tool needs ctx as the first positional argument
    takes_context: bool = True

    def to_anthropic(self) -> dict:
        """Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _python_type_to_json_schema(t: Any) -> dict:
    """Best-effort mapping of Python annotations → JSON Schema for tool input.

    Kept deliberately small — agents only need scalar args, lists of scalars,
    and dicts. Complex Pydantic models are passed positionally via context.
    Accepts either resolved types OR string annotations (Python 3.9 + future
    annotations + `X | None` breaks typing.get_type_hints so we substring-match).
    """
    import typing

    # String-annotation fallback (covers Python 3.9 + `X | None` syntax)
    if isinstance(t, str):
        s = t.strip().lower()
        # Strip optional wrapper: "int | None", "Optional[int]"
        if "|" in s:
            s = s.split("|", 1)[0].strip()
        if s.startswith("optional["):
            s = s[len("optional[") :].rstrip("]").strip()
        if s.startswith("list[") or s == "list":
            inner = s[5:-1].strip() if s.startswith("list[") else ""
            return {
                "type": "array",
                "items": _python_type_to_json_schema(inner) if inner else {},
            }
        if s.startswith("dict") or s.startswith("mapping"):
            return {"type": "object"}
        if s == "int":
            return {"type": "integer"}
        if s == "float":
            return {"type": "number"}
        if s == "bool":
            return {"type": "boolean"}
        if s == "str":
            return {"type": "string"}
        # Decimal / datetime / pydantic model / unknown → string
        return {"type": "string"}

    origin = typing.get_origin(t)
    args = typing.get_args(t)

    if t is str:
        return {"type": "string"}
    if t is int:
        return {"type": "integer"}
    if t is float:
        return {"type": "number"}
    if t is bool:
        return {"type": "boolean"}
    if origin is list:
        if args:
            return {"type": "array", "items": _python_type_to_json_schema(args[0])}
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    # Union (including X | None) — collapse to the first non-None type.
    # typing.Union covers Optional[X]; types.UnionType covers X | Y (Python 3.10+).
    import sys

    _is_union = origin is typing.Union
    if not _is_union and sys.version_info >= (3, 10):
        import types as _types

        _is_union = isinstance(t, _types.UnionType)
    if _is_union:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _python_type_to_json_schema(non_none[0])
    # Decimal / datetime / arbitrary → string (tool function will parse)
    return {"type": "string"}


def make_tool_spec(
    name: str,
    description: str,
    handler: Callable,
    *,
    required: list[str] | None = None,
    overrides: dict[str, dict] | None = None,
    takes_context: bool = True,
) -> ToolSpec:
    """Build a ToolSpec by inspecting ``handler``'s signature.

    Skips the leading ``ctx`` parameter. Uses ``overrides`` for any param whose
    inferred schema isn't precise enough (e.g. enums, item shapes).
    """
    import typing

    sig = inspect.signature(handler)
    # Resolve forward-reference / future-annotations strings to actual types
    try:
        hints = typing.get_type_hints(handler)
    except Exception:
        hints = {}
    properties: dict[str, dict] = {}
    inferred_required: list[str] = []
    overrides = overrides or {}

    for i, (param_name, param) in enumerate(sig.parameters.items()):
        if takes_context and i == 0:
            continue  # skip ctx
        if param_name in overrides:
            properties[param_name] = overrides[param_name]
        else:
            annotation = hints.get(param_name, param.annotation)
            properties[param_name] = _python_type_to_json_schema(annotation)
        if param.default is inspect.Parameter.empty:
            inferred_required.append(param_name)

    return ToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required if required is not None else inferred_required,
        },
        handler=handler,
        takes_context=takes_context,
    )


# ─── Base agent ─────────────────────────────────────────────────────────────


class BaseAgent:
    """Runs the Anthropic tool loop for one subagent.

    Subclasses set ``model``, ``system_prompt``, and ``tool_specs``. The
    ``run`` method drives the loop until the model emits a non-tool response,
    then returns the parsed final result (subclass implements ``_parse_final``).
    """

    model: str = "claude-haiku-4-5"
    max_tokens: int = 2048
    system_prompt: str = ""
    tool_specs: list[ToolSpec] = []

    # Maximum tool-loop iterations before giving up (safety net)
    MAX_ITERATIONS = 16

    def __init__(self, client: AnthropicLike):
        self.client = client
        self._dispatch = {ts.name: ts for ts in self.tool_specs}

    # ── public ───────────────────────────────────────────────────────────────

    async def run(
        self, ctx: ToolContext, user_message: str, history: list[dict] | None = None
    ) -> dict:
        """Drive the tool loop. Returns the parsed final dict from the model.

        ``history`` is an optional persistent conversation history (for the
        Orchestrator across turns). Subagents leave it None — they are stateless.
        """
        if history is not None:
            history.append({"role": "user", "content": user_message})
            messages = history
        else:
            messages = [{"role": "user", "content": user_message}]
        last_text = ""

        for _ in range(self.MAX_ITERATIONS):
            resp = await self.client.messages.create(
                model=self.model,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[ts.to_anthropic() for ts in self.tool_specs],
                messages=messages,
                max_tokens=self.max_tokens,
            )

            # Collect text + tool_use blocks
            assistant_content = []
            tool_uses = []
            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    last_text = getattr(block, "text", "")
                    assistant_content.append({"type": "text", "text": last_text})
                elif btype == "tool_use":
                    tool_uses.append(block)
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_content})

            if resp.stop_reason != "tool_use":
                return self._parse_final(last_text)

            # Dispatch tool calls
            tool_results = []
            for tu in tool_uses:
                result = await self._invoke(ctx, tu.name, tu.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        # Iteration cap hit — return whatever text we have
        return {"parse_error": "max_iterations_exceeded", "raw": last_text}

    # ── dispatch ─────────────────────────────────────────────────────────────

    async def _invoke(self, ctx: ToolContext, name: str, args: dict) -> Any:
        spec = self._dispatch.get(name)
        if spec is None:
            return {"error": f"unknown tool: {name}"}
        try:
            if spec.takes_context:
                return _serialise(await spec.handler(ctx, **args))
            return _serialise(await spec.handler(**args))
        except Exception as e:  # tool errors must not crash the loop
            return {"error": type(e).__name__, "message": str(e)}

    # ── parsing ──────────────────────────────────────────────────────────────

    def _parse_final(self, text: str) -> dict:
        """Default: try to find JSON in the model's last text block.

        Subclasses can override for stricter parsing or to validate against a
        Pydantic schema.
        """
        if not text:
            return {"parse_error": "empty_response"}
        # Try direct JSON first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try fenced code block
        if "```" in text:
            fence = text.split("```")
            for chunk in fence[1::2]:
                cleaned = chunk.lstrip("json").strip()
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    continue
        # Last resort: surface the raw text
        return {"parse_error": "non_json", "raw": text}


def _serialise(value: Any) -> Any:
    """Convert Pydantic models / lists thereof into JSON-safe primitives."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialise(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in value.items()}
    return value
