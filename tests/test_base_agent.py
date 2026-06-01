"""BaseAgent: tool loop mechanics + schema generation + JSON parsing."""

from __future__ import annotations

import asyncio


from agents.base import BaseAgent, ToolSpec, make_tool_spec
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


async def _ping(ctx, *, message: str) -> dict:
    return {"echoed": message}


class _TinyAgent(BaseAgent):
    system_prompt = "test"
    tool_specs = [
        ToolSpec(
            name="ping",
            description="Echo a message back.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            handler=_ping,
        ),
    ]


def test_tool_loop_terminates_on_end_turn(tool_ctx):
    client = FakeAnthropicClient(
        [
            text_response('{"answer": 42}'),
        ]
    )
    agent = _TinyAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "hello"))
    assert result == {"answer": 42}
    assert len(client.calls) == 1


def test_tool_loop_dispatches_then_continues(tool_ctx):
    client = FakeAnthropicClient(
        [
            tool_use_response(("ping", {"message": "hi"})),
            text_response('{"final": "done"}'),
        ]
    )
    agent = _TinyAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert result == {"final": "done"}
    assert len(client.calls) == 2
    # The second call's messages include the tool_result echoing our handler
    last_call_msgs = client.calls[1].messages
    tool_result_blocks = [
        b
        for m in last_call_msgs
        if m["role"] == "user"
        for b in (m["content"] if isinstance(m["content"], list) else [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert any('"echoed"' in tr["content"] for tr in tool_result_blocks)


def test_multiple_tool_calls_in_one_turn(tool_ctx):
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("ping", {"message": "first"}),
                ("ping", {"message": "second"}),
            ),
            text_response('{"k": "v"}'),
        ]
    )
    agent = _TinyAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert client.dispatched_tool_names() == ["ping", "ping"]
    inputs = client.tool_inputs("ping")
    assert inputs == [{"message": "first"}, {"message": "second"}]


def test_unknown_tool_returns_error_to_model(tool_ctx):
    client = FakeAnthropicClient(
        [
            tool_use_response(("nonexistent_tool", {})),
            text_response('{"recovered": true}'),
        ]
    )
    agent = _TinyAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert result == {"recovered": True}
    # Tool result must contain the error so the model can react
    last = client.calls[1].messages[-1]
    assert any("unknown tool" in tr["content"] for tr in last["content"] if isinstance(tr, dict))


def test_tool_exception_does_not_crash_loop(tool_ctx):
    async def _boom(ctx):
        raise ValueError("kaboom")

    class _BoomAgent(BaseAgent):
        system_prompt = ""
        tool_specs = [
            ToolSpec(
                name="boom",
                description="x",
                input_schema={"type": "object", "properties": {}},
                handler=_boom,
            )
        ]

    client = FakeAnthropicClient(
        [
            tool_use_response(("boom", {})),
            text_response('{"ok": true}'),
        ]
    )
    agent = _BoomAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert result == {"ok": True}
    # Error surfaced to the model
    err_blob = client.calls[1].messages[-1]["content"][0]["content"]
    assert "kaboom" in err_blob


def test_parses_fenced_json_code_block(tool_ctx):
    client = FakeAnthropicClient(
        [
            text_response('Here is the result:\n```json\n{"products": []}\n```'),
        ]
    )
    agent = _TinyAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert result == {"products": []}


def test_returns_parse_error_for_non_json(tool_ctx):
    client = FakeAnthropicClient(
        [
            text_response("this is not json at all"),
        ]
    )
    agent = _TinyAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert "parse_error" in result


def test_iteration_cap_prevents_runaway(tool_ctx):
    """If the model keeps calling tools forever, we abort gracefully."""
    # Fill the queue with infinite tool_use responses
    client = FakeAnthropicClient(
        [
            tool_use_response(("ping", {"message": "loop"}))
            for _ in range(20)  # more than MAX_ITERATIONS (16)
        ]
    )
    agent = _TinyAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    assert result.get("parse_error") == "max_iterations_exceeded"


def test_make_tool_spec_infers_schema_from_signature(tool_ctx):
    async def _example(ctx, *, name: str, count: int = 5, flags: list[str] | None = None) -> dict:
        return {}

    spec = make_tool_spec("example", "desc", _example)
    schema = spec.input_schema
    assert schema["type"] == "object"
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["count"] == {"type": "integer"}
    assert schema["properties"]["flags"]["type"] == "array"
    # `name` is required (no default); count + flags optional
    assert schema["required"] == ["name"]


def test_pydantic_results_are_serialised_for_tool_results(tool_ctx):
    """Tool returning a Pydantic model must JSON-serialise cleanly to tool_result."""
    from models.product import ProductResult
    from decimal import Decimal

    async def _make_product(ctx) -> ProductResult:
        return ProductResult(
            product_id="p1",
            name="x",
            price=Decimal("10"),
            merchant="m",
            merchant_domain="m.com",
        )

    class _A(BaseAgent):
        system_prompt = ""
        tool_specs = [
            ToolSpec(
                name="m",
                description="",
                input_schema={"type": "object", "properties": {}},
                handler=_make_product,
            )
        ]

    client = FakeAnthropicClient(
        [
            tool_use_response(("m", {})),
            text_response("{}"),
        ]
    )
    agent = _A(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    tool_result = client.calls[1].messages[-1]["content"][0]["content"]
    assert "p1" in tool_result
    assert "10" in tool_result
