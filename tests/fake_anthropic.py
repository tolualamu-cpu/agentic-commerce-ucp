"""FakeAnthropicClient — scripted responses for deterministic agent tests.

Usage:
    client = FakeAnthropicClient([
        text_block("calling search"),
        tool_use("search_products", {"query": "shoes", ...}),     # stop=tool_use
        # ... after tool result is appended ...
        text_block('{"products": [...], "notes": "..."}'),         # stop=end_turn
    ])

Each Message in the queue is consumed by one ``client.messages.create()`` call.
The fake records all calls so tests can assert on system prompt, model, etc.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


# ─── Block + message shapes that match Anthropic SDK duck typing ────────────


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict
    id: str = field(default_factory=lambda: f"toolu_{uuid.uuid4().hex[:8]}")
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "stop_sequence"
    model: str = "claude-haiku-4-5"
    usage: Any = None


# ─── Helpers to build messages ──────────────────────────────────────────────


def text_response(text: str) -> FakeMessage:
    """A final-turn text response. stop_reason='end_turn'."""
    return FakeMessage(content=[TextBlock(text=text)], stop_reason="end_turn")


def tool_use_response(*calls: tuple[str, dict]) -> FakeMessage:
    """A tool-calling turn. Each (name, input) becomes one ToolUseBlock."""
    blocks = [ToolUseBlock(name=n, input=i) for (n, i) in calls]
    return FakeMessage(content=blocks, stop_reason="tool_use")


def text_then_tool_use(text: str, *calls: tuple[str, dict]) -> FakeMessage:
    """Common pattern: short narrative text + a tool call in the same turn."""
    blocks: list = [TextBlock(text=text)]
    blocks.extend(ToolUseBlock(name=n, input=i) for (n, i) in calls)
    return FakeMessage(content=blocks, stop_reason="tool_use")


# ─── The fake client itself ─────────────────────────────────────────────────


@dataclass
class _CallRecord:
    model: str
    system: list
    tools: list
    messages: list


class _MessagesAPI:
    def __init__(self, queue: list[FakeMessage], record: list[_CallRecord]):
        self._queue = queue
        self._record = record

    async def create(
        self, *, model: str, system: list, tools: list, messages: list, max_tokens: int
    ) -> FakeMessage:
        self._record.append(
            _CallRecord(model=model, system=system, tools=tools, messages=messages.copy())
        )
        if not self._queue:
            raise RuntimeError(
                "FakeAnthropicClient queue exhausted — " "test scripted too few responses"
            )
        return self._queue.pop(0)


class FakeAnthropicClient:
    """Pretends to be ``anthropic.AsyncAnthropic`` for testing.

    Construct with a list of scripted responses. Each `messages.create` call
    pops the next response. Inspect `client.calls` to assert on dispatched
    tool calls, models used, etc.
    """

    def __init__(self, responses: list[FakeMessage]):
        self.calls: list[_CallRecord] = []
        self.messages = _MessagesAPI(list(responses), self.calls)

    def remaining(self) -> int:
        return len(self.messages._queue)

    def dispatched_tool_names(self) -> list[str]:
        """Tool names the agent called, in order, across all turns."""
        names = []
        for rec in self.calls:
            # tool_results sent to the model are in the user message content
            for msg in rec.messages:
                if msg.get("role") == "user" and isinstance(msg["content"], list):
                    for block in msg["content"]:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            # The id format encodes nothing; trace from prior assistant block instead
                            pass
        # Better approach: re-derive from the assistant turns recorded
        for rec in self.calls:
            for msg in rec.messages:
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                    for block in msg["content"]:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            names.append(block["name"])
        return names

    def tool_inputs(self, tool_name: str) -> list[dict]:
        """All input dicts the agent passed to a given tool, in call order.

        Deduplicates by tool-use block id so each tool invocation is counted
        once even though growing history means older blocks appear in every
        subsequent call's message list.
        """
        seen: set[str] = set()
        inputs = []
        for rec in self.calls:
            for msg in rec.messages:
                if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                    for block in msg["content"]:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_use"
                            and block.get("name") == tool_name
                        ):
                            block_id = block.get("id", "")
                            if block_id not in seen:
                                seen.add(block_id)
                                inputs.append(block["input"])
        return inputs
