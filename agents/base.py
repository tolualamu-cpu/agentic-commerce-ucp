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
    # Terminal tools produce the agent's COMPLETE structured result. When the
    # model calls exactly one terminal tool and it returns without error, the
    # loop short-circuits — no extra "reformat into JSON" LLM round-trip — and
    # the (optionally wrapped) tool output is returned directly. Use only where
    # the tool output IS the final answer (e.g. deterministic ranking).
    terminal: bool = False

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
    terminal: bool = False,
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
        terminal=terminal,
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
            # Guard against double-appending the user turn. The web layer
            # persists the user message into ``session.conversation``
            # synchronously inside ``POST /chat`` (so the immediate
            # navigation to /chat renders the active state with the new turn
            # visible, instead of racing the background orchestrator and
            # landing on the empty hero). When that pre-appended turn is the
            # last entry here, appending again would duplicate it — both in
            # the model's context and in the rendered chat log. The CLI and
            # the test suite never pre-append, so this guard is a no-op for
            # them (the last turn there is always an assistant/tool turn).
            already_appended = bool(history) and (
                history[-1].get("role") == "user" and history[-1].get("content") == user_message
            )
            if not already_appended:
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
            invoked: list[tuple[str, Any]] = []
            for tu in tool_uses:
                result = await self._invoke(ctx, tu.name, tu.input)
                invoked.append((tu.name, result))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

            # Terminal-tool fast-path: when the model called exactly ONE tool,
            # that tool is marked terminal, and it returned without error, the
            # tool output IS the complete result — return it directly and skip
            # the otherwise-redundant "reformat into final JSON" LLM round-trip.
            if len(invoked) == 1:
                name, result = invoked[0]
                spec = self._dispatch.get(name)
                if spec is not None and spec.terminal and not _is_tool_error(result):
                    return self._wrap_terminal(name, result)

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

    # ── terminal fast-path ─────────────────────────────────────────────────────

    def _wrap_terminal(self, name: str, result: Any) -> dict:
        """Shape a terminal tool's raw output into the agent's final dict.

        Default: pass the result through if it's already a dict; otherwise wrap
        it under the tool name. Subclasses override to assemble the exact schema
        the orchestrator expects (e.g. EvaluationAgent → {ranked, ...}).
        """
        if isinstance(result, dict):
            return result
        return {name: result}

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
        # Try fenced code block.
        # NOTE: ``chunk.lstrip("json")`` strips the CHARACTERS j/s/o/n from
        # the left, not the literal word "json" — happens to work because
        # ```json fences start with j-s-o-n followed by a newline. The
        # safer ``chunk.removeprefix("json")`` is Python 3.9+; this code
        # also runs on 3.9 so it's fine, but we keep the historical form.
        if "```" in text:
            fence = text.split("```")
            for chunk in fence[1::2]:
                cleaned = chunk.lstrip("json").strip()
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    continue
        # Last resort: try to recover a partial JSON object/array. The
        # discovery agent can hit max_tokens mid-string and produce
        # truncated JSON like:
        #   ```json
        #   {"products": [{"id":"a","name":"x"}, {"id":"b","name":"x...
        # Previously this fell through to {"parse_error": "non_json"} and
        # the orchestrator silently dropped ALL discovered products — so
        # no products SSE event ever fired and no cards appeared in the
        # chat UI even though the model successfully found products.
        # Recovery: find the LAST complete object/array close and truncate.
        partial = _recover_partial_json(text)
        if partial is not None:
            return partial
        # Truly unparseable — surface the raw text for debug logs.
        return {"parse_error": "non_json", "raw": text}


def _recover_partial_json(text: str) -> dict | None:
    """Salvage a partial JSON object from text that was truncated mid-response.

    Real-world failure: the discovery agent emits a large JSON object with a
    ``products`` array. If the response hits max_tokens mid-string the final
    JSON is invalid, but the COMPLETED items in the array are still readable.
    Without this helper, the orchestrator drops EVERYTHING discovered — so the
    user sees the agent describe products in prose but no cards render.

    Strategy:
      1. Find the FIRST `{` to start the JSON object.
      2. Walk forward tracking bracket depth, ignoring brackets inside string
         literals (respect escape sequences).
      3. Whenever depth returns to 0 inside the top-level object's value, that
         is a candidate complete-array boundary — track each.
      4. If the whole object never closes, synthesise a close by truncating to
         the last complete item in the products array (find last balanced `}`
         inside the products array, add ``]}`` to close).
      5. Return the parsed result, or None if recovery isn't possible.

    Handles both raw JSON and markdown-fenced JSON (strips the fences first).
    """
    if not text:
        return None
    # Strip leading markdown fence if present so we look at raw JSON.
    candidate = text
    if "```" in candidate:
        # Take the FIRST fenced block — that's the one the model meant.
        parts = candidate.split("```")
        if len(parts) >= 2:
            chunk = parts[1].lstrip("json").strip()
            # If the fence never closed (truncation), the chunk is the
            # rest of the text. Use it.
            candidate = chunk
    # Find the start of the object.
    start = candidate.find("{")
    if start < 0:
        return None
    # Walk the string, tracking depth and array boundaries inside a
    # ``products`` (or ``ranked``, etc.) array.
    depth = 0
    in_str = False
    escape = False
    last_complete_array_item_end: int | None = None
    products_array_start: int | None = None
    products_depth: int | None = None
    i = start
    while i < len(candidate):
        ch = candidate[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and in_str:
            escape = True
            i += 1
            continue
        if ch == '"':
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            # If we just closed an element object directly INSIDE the
            # salvaged array (i.e. the array item itself), mark the boundary.
            # Items live one level deeper than the array. The array sits at
            # depth ``products_depth`` (counted as the depth BEFORE entering
            # the `[`), so each item sits at depth ``products_depth + 1``
            # while open and returns to ``products_depth + 1`` when closed.
            if (
                products_array_start is not None
                and products_depth is not None
                and depth == products_depth + 1
            ):
                last_complete_array_item_end = i
        elif ch == "[":
            # Detect the salvageable array opening: look backward for a
            # known key (products/ranked/items). If found, snapshot the
            # current depth so item-close detection above knows the level.
            preceding = candidate[max(0, i - 60) : i].rstrip()
            if preceding.endswith(":"):
                if (
                    '"products"' in preceding[-20:]
                    or '"ranked"' in preceding[-20:]
                    or '"items"' in preceding[-20:]
                ):
                    products_array_start = i
                    products_depth = depth  # depth BEFORE entering the `[`
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    # Case A: top-level object fully closed but original parse failed for some
    # other reason — just try once more on the strict slice.
    if depth == 0:
        try:
            return json.loads(candidate[start:i])
        except json.JSONDecodeError:
            pass
    # Case B: truncated. If we tracked the products array and saw at least one
    # complete item, splice ``]}`` after the last complete item to close.
    if (
        products_array_start is not None
        and last_complete_array_item_end is not None
        and last_complete_array_item_end > products_array_start
    ):
        # Slice from object start up to and including the last complete array
        # item, then close the array and the outer object.
        salvaged = candidate[start : last_complete_array_item_end + 1] + "]}"
        try:
            return json.loads(salvaged)
        except json.JSONDecodeError:
            return None
    # Case C: an empty products array opened but no items completed — emit
    # an empty list so the caller knows the agent ran but produced nothing.
    if products_array_start is not None and last_complete_array_item_end is None:
        # Determine which key the array belongs to so we return the right shape.
        preceding = candidate[max(0, products_array_start - 60) : products_array_start]
        for key in ("products", "ranked", "items"):
            if f'"{key}"' in preceding:
                return {key: [], "parse_recovered": "empty_array_after_truncation"}
        return None
    return None


def _is_tool_error(result: Any) -> bool:
    """A tool dispatch failed if it returned an ``{"error": ...}`` envelope.

    ``_invoke`` wraps exceptions as ``{"error": <type>, "message": <str>}`` and
    unknown tools as ``{"error": "unknown tool: ..."}``. In those cases the
    terminal fast-path must NOT fire — fall back to the normal loop so the model
    can react to the error.
    """
    return isinstance(result, dict) and "error" in result


def _serialise(value: Any) -> Any:
    """Convert Pydantic models / lists thereof into JSON-safe primitives."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_serialise(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in value.items()}
    return value
