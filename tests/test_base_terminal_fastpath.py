"""Unit tests for BaseAgent's terminal-tool fast-path (Lever 2).

When a model calls exactly one tool marked ``terminal=True`` and it returns
without error, the loop returns the (optionally wrapped) tool output directly,
skipping the otherwise-redundant "reformat into final JSON" LLM round-trip.

This file sorts alphabetically BEFORE ``test_user_journeys.py`` so per CLAUDE.md
it uses ``asyncio.get_event_loop().run_until_complete()``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agents.base import BaseAgent, make_tool_spec
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _echo(ctx, *, value: str) -> dict:
    return {"echoed": value}


async def _boom(ctx, *, value: str) -> dict:
    raise ValueError("kaboom")


class _TerminalAgent(BaseAgent):
    system_prompt = "x"
    tool_specs = [
        make_tool_spec("echo", "echo a value", _echo, required=["value"], terminal=True),
    ]


class _NonTerminalAgent(BaseAgent):
    system_prompt = "x"
    tool_specs = [
        make_tool_spec("echo", "echo a value", _echo, required=["value"]),
    ]


class _WrappingAgent(_TerminalAgent):
    def _wrap_terminal(self, name: str, result: Any) -> dict:
        return {"wrapped": True, "from": name, "payload": result}


class _ErrTerminalAgent(BaseAgent):
    system_prompt = "x"
    tool_specs = [
        make_tool_spec("boom", "raises", _boom, required=["value"], terminal=True),
    ]


def test_terminal_tool_short_circuits_with_one_create_call():
    client = FakeAnthropicClient([tool_use_response(("echo", {"value": "hi"}))])
    agent = _TerminalAgent(client)
    result = _run(agent.run(ctx=None, user_message="go"))
    # Exactly one round-trip — no reformat turn consumed.
    assert len(client.calls) == 1
    assert client.remaining() == 0
    # Default _wrap_terminal passes a dict result straight through.
    assert result == {"echoed": "hi"}


def test_non_terminal_tool_still_loops():
    client = FakeAnthropicClient(
        [
            tool_use_response(("echo", {"value": "hi"})),
            text_response('{"done": true}'),
        ]
    )
    agent = _NonTerminalAgent(client)
    result = _run(agent.run(ctx=None, user_message="go"))
    # Two round-trips: tool turn + reformat turn.
    assert len(client.calls) == 2
    assert result == {"done": True}


def test_wrap_terminal_override_shapes_result():
    client = FakeAnthropicClient([tool_use_response(("echo", {"value": "v"}))])
    agent = _WrappingAgent(client)
    result = _run(agent.run(ctx=None, user_message="go"))
    assert result == {"wrapped": True, "from": "echo", "payload": {"echoed": "v"}}
    assert len(client.calls) == 1


def test_terminal_tool_error_falls_back_to_loop():
    # When the terminal tool errors, the fast-path must NOT fire: the loop
    # continues so the model can react to the error envelope.
    client = FakeAnthropicClient(
        [
            tool_use_response(("boom", {"value": "v"})),
            text_response('{"recovered": true}'),
        ]
    )
    agent = _ErrTerminalAgent(client)
    result = _run(agent.run(ctx=None, user_message="go"))
    assert len(client.calls) == 2  # fell back to the reformat turn
    assert result == {"recovered": True}


def test_two_tools_in_one_turn_does_not_fast_path():
    # The fast-path requires EXACTLY one tool call. Two calls → normal loop.
    client = FakeAnthropicClient(
        [
            tool_use_response(("echo", {"value": "a"}), ("echo", {"value": "b"})),
            text_response('{"both": true}'),
        ]
    )
    agent = _TerminalAgent(client)
    result = _run(agent.run(ctx=None, user_message="go"))
    assert len(client.calls) == 2
    assert result == {"both": True}
