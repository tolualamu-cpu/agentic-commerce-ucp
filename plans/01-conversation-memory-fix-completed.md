# Fix: Orchestrator conversation memory across turns

> **STATUS: COMPLETED 2026-05-13** — implemented, 123/123 tests passing.
> Multi-turn conversation memory works; CONFIRM is case-insensitive; soft
> cap on history prevents runaway token use.
> Verified live in REPL by user.
> Archived before starting next plan (multi-merchant + catalogue diversity).

## Context

During live testing after Phase 4 shipped, the user noticed the Orchestrator has no memory across REPL turns. When they searched for shoes, then asked for headphones (no match), then said "ok buy the demo running shoes", the Orchestrator re-ran discovery from scratch — because each `orchestrator.run(...)` call starts with a fresh `messages = [{"role": "user", "content": user_message}]` list.

This is correct for subagents (Discovery, Evaluation, Purchase, Tracking are single-shot tools with self-contained briefs) but wrong for the Orchestrator, which is supposed to be the persistent conversational front-end.

**Intended outcome:** after this fix, the user can have a multi-turn conversation. "Find me X" → "actually find Y" → "ok buy X" should work without re-searching. The Orchestrator remembers prior tool results (product lists, ranks, mentioned merchants) across REPL prompts within one session.

## Diagnosis

In [agents/base.py](file:///Users/tolualamu/Desktop/Startups%20and%20Entrepreneurship/Agentic%20Commerce/agents/base.py), `BaseAgent.run()` initialises `messages` fresh each call. Both subagents and the Orchestrator inherit this. Subagents should keep this behaviour (they are stateless by design). The Orchestrator needs to thread a persistent history across REPL turns.

`SessionState.conversation` (in [storage/state.py](file:///Users/tolualamu/Desktop/Startups%20and%20Entrepreneurship/Agentic%20Commerce/storage/state.py)) already exists for this purpose but is never wired — `_handle_command` in `main.py` calls `orchestrator.run(ctx, line)` and the conversation field stays empty.

## Fix (already partially in-flight)

### 1. `agents/base.py` — accept optional `history` parameter (DONE pre-plan-mode)

```python
async def run(self, ctx, user_message, history: list[dict] | None = None) -> dict:
    if history is not None:
        history.append({"role": "user", "content": user_message})
        messages = history
    else:
        messages = [{"role": "user", "content": user_message}]
```

Subagents pass nothing (stateless). Orchestrator passes `ctx.session.conversation`.

### 2. `agents/orchestrator.py` — override `run()` to thread session history

```python
async def run(self, ctx: ToolContext, user_message: str) -> dict:
    return await super().run(ctx, user_message,
                              history=ctx.session.conversation)
```

The base class appends assistant turns to the list. After the loop terminates, the list contains the full conversation: user → assistant (with tool_use blocks) → user (with tool_results) → ... → assistant (final). The next `run()` call sees all of this.

### 3. Cap history length to prevent runaway token use

A long multi-turn session will accumulate full tool-result payloads (product lists, order data) which can balloon token cost. Add a soft cap: after each turn, if `len(conversation) > MAX_HISTORY_ENTRIES` (e.g. 40 messages), drop the oldest pair (preserving the user/assistant pairing). Implement in the Orchestrator's `run()` post-loop.

### 4. `cli/display.py` — also tweak the gate prompt to handle typos

A second observation from testing: user typed `cnirm` (typo) and the gate cancelled. Better UX: trim whitespace and uppercase compare — `confirm`, `Confirm`, `CONFIRM` should all pass. (Strict equality with `CONFIRM` is overkill for a CLI demo; a substring check `"confirm" in answer.lower()` would lose the "type exactly" semantics. Best middle ground: case-insensitive exact match on the trimmed string.)

Change in `RichConfirmProvider.explicit_confirm`:
```python
return answer.strip().upper() == "CONFIRM"
```

### 5. Update tests

Two existing tests need attention:
- `tests/test_base_agent.py` and `tests/test_orchestrator*.py` — most should still pass since `history=None` is the default. Confirm by running the suite.
- Add one new test: `tests/test_orchestrator.py::test_conversation_persists_across_runs` — call `orchestrator.run()` twice; assert the second call's `messages` (visible to the FakeAnthropicClient) contains BOTH turns.

## Files to modify

| File | Change |
|---|---|
| [agents/base.py](file:///Users/tolualamu/Desktop/Startups%20and%20Entrepreneurship/Agentic%20Commerce/agents/base.py) | Already edited pre-plan-mode — accept optional `history`. Confirm correctness. |
| [agents/orchestrator.py](file:///Users/tolualamu/Desktop/Startups%20and%20Entrepreneurship/Agentic%20Commerce/agents/orchestrator.py) | Override `run()` to pass `ctx.session.conversation`; add history-cap helper |
| [cli/display.py](file:///Users/tolualamu/Desktop/Startups%20and%20Entrepreneurship/Agentic%20Commerce/cli/display.py) | Case-insensitive CONFIRM check |
| [tests/test_orchestrator.py](file:///Users/tolualamu/Desktop/Startups%20and%20Entrepreneurship/Agentic%20Commerce/tests/test_orchestrator.py) | Add `test_conversation_persists_across_runs` |

No changes needed to `main.py` — the `SessionState` is already constructed once per session and lives on `ToolContext.session`, exactly where the orchestrator will read it.

## Verification

1. Run full test suite — expect 122/121 (one new test added). All others still pass.
   ```bash
   python3 -m pytest tests/ -v
   ```
2. Live REPL test:
   ```bash
   python3 main.py
   > Find me running shoes under $150
   > what was the price again?    ← should remember without re-searching
   > ok buy them                  ← should reach the gate without re-searching
   > confirm                      ← lowercase should now work
   ```
3. Inspect: after multiple turns, `ctx.session.conversation` should be non-empty and growing. Verifiable by adding a `history` REPL command (out of scope here, but trivial follow-up).

## Out of scope

- Persisting conversation history to disk across `python main.py` invocations (currently per-process only; intentional for MVP)
- Compressing/summarising old turns instead of dropping (a Phase 5+ optimisation)
- Showing the conversation history via a REPL command (`history`) — easy follow-up
