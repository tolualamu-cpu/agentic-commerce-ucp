"""End-to-end web purchase tests.

Drives the FULL web purchase flow with a scripted LLM:
  POST /chat → orchestrator → call_purchase_agent → 7-step UCP chain →
  gate.open → confirm/cancel → order in db.orders, spend recorded.

Why this file exists:
  Phase 7a-7f had 73 unit/component tests, all green, while two real bugs
  shipped to the browser:
    1. Stale-cancel poisoning the inbox queue on WS reconnect.
    2. ``update_checkout_session`` receiving list[dict] (real LLM tool
       args) instead of list[CartItem] (every existing unit test).
  Each unit test layer was correct in isolation; the integrated whole
  wasn't. These e2e tests close the gap.

Concurrency model:
  Plain sync test functions; the body is an ``async def`` invoked via
  ``asyncio.run(...)``. Single event loop. Same inline-asyncio pattern
  used elsewhere in the web test files — no pytest-asyncio dependency.

Transport:
  ``httpx.AsyncClient`` with ``ASGITransport`` so we can await POST /chat
  AND interact with the session's provider queues on the same loop.
  Sync TestClient cannot do this (cross-loop queue access).

LLM stub:
  ``FakeAnthropicClient`` from ``tests/fake_anthropic.py``. Scripted
  responses cover both the orchestrator's top-level turns AND every
  subagent it dispatches (they share the same client by design).

Gate driving:
  Direct queue manipulation — push the chosen reply onto
  ``sess.gate_provider.inbox`` after observing ``gate.open`` on the
  outbox. The /gate/ws HTTP bridge is already exercised by
  ``tests/test_web_phase7d.py`` so we don't double-test it here.

TODO (deferred):
  - Picker-overlay flow (orchestrator's search sub-flow doesn't currently
    emit ``picker.open`` over the provider — Phase 7f stub for future
    work).
  - Real /gate/ws round-trip — needs an async-WS client. Phase 7d's unit
    tests already cover the bridge layer.

RESOLVED (Phase 8e):
  ``tools/purchase_tools.py:save_order`` previously had the same
  dict-vs-Pydantic bug as ``update_checkout_session`` — it expected
  a PurchaseOrder but the model emitted a dict, so orders never
  persisted to db.orders even though payment succeeded. Phase 8e
  added a dict→PurchaseOrder coercion at the tool boundary; orders
  now land in db.orders. The script below passes a complete order
  payload so this test exercises the real persistence path. The
  broader Pydantic TypeAdapter coercion in BaseAgent._invoke is
  still deferred (would generalise the dict→model fix to every tool
  boundary at once).
"""

import asyncio
import json
from decimal import Decimal
from typing import Optional

import httpx
import pytest

from agents.orchestrator import OrchestratorAgent, StreamingCallbacks
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)
from web import session as session_mod
from web.app import create_app


@pytest.fixture(autouse=True)
def _isolate_sessions():
    """Clear ``_SESSIONS`` before AND after every e2e test.

    Our tests run a fresh ``asyncio.run(...)`` event loop per test. Sessions
    created here have lazy ``asyncio.Queue`` instances bound to that loop;
    once the loop closes, the queues are unusable. Without cleanup,
    subsequent tests (especially the Phase 7b/c suites which create their
    own event loops via ``asyncio.new_event_loop()``) trip over leftover
    sessions whose queues belong to a dead loop and crash with
    "no current event loop in thread 'MainThread'".
    """
    session_mod._SESSIONS.clear()
    yield
    session_mod._SESSIONS.clear()


# ─── Script builders ────────────────────────────────────────────────────────

MERCHANT = "athletic-co.myshopify.com"


def _purchase_chain_script(
    mandate_id: str, items: list[dict], *, total: str, post_tax_total: str
) -> list:
    """The seven scripted tool calls a PurchaseAgent makes for a happy path,
    plus its final JSON reply. Items deliberately arrive as ``list[dict]``
    — that's how real LLM tool args land, and that's the shape the
    dict-vs-CartItem coercion in ``tools/purchase_tools.py`` must handle.
    """
    return [
        tool_use_response(
            (
                "validate_mandate",
                {
                    "mandate_id": mandate_id,
                    "amount": total,
                    "vendor": MERCHANT,
                },
            )
        ),
        tool_use_response(
            (
                "create_checkout_session",
                {
                    "merchant_domain": MERCHANT,
                    "mandate_id": mandate_id,
                },
            )
        ),
        tool_use_response(
            (
                "update_checkout_session",
                {
                    "session_id": "SID_PLACEHOLDER",
                    "merchant_domain": MERCHANT,
                    "items": items,
                    "mandate_id": mandate_id,
                },
            )
        ),
        tool_use_response(
            (
                "get_payment_token",
                {
                    "mandate_id": mandate_id,
                    "amount": post_tax_total,
                    "vendor": MERCHANT,
                    "merchant_domain": MERCHANT,
                },
            )
        ),
        tool_use_response(
            (
                "complete_order",
                {
                    "session_id": "SID_PLACEHOLDER",
                    "merchant_domain": MERCHANT,
                    "payment_handler_id": "stripe",
                    "payment_token": "tok_test_xyz",
                    "mandate_id": mandate_id,
                },
            )
        ),
        # save_order now requires the full PurchaseOrder shape (Phase
        # 8e coerced the dict path against the Pydantic model). The
        # script supplies a complete payload so the order actually
        # lands in db.orders.
        tool_use_response(
            (
                "save_order",
                {
                    "order": {
                        "order_id": "ord_e2e",
                        "session_id": "SID_PLACEHOLDER",
                        "merchant_domain": MERCHANT,
                        "items": items,
                        "total": post_tax_total,
                        "currency": "USD",
                        "status": "confirmed",
                        "mandate_id": mandate_id,
                        "payment_intent_id": "pi_test_xyz",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            )
        ),
        tool_use_response(
            (
                "record_mandate_spend",
                {
                    "mandate_id": mandate_id,
                    "amount": post_tax_total,
                    "order_id": "ord_e2e",
                    "vendor": MERCHANT,
                },
            )
        ),
        # PurchaseAgent's final JSON reply
        text_response(
            json.dumps(
                {
                    "order": {"order_id": "ord_e2e"},
                    "status": "completed",
                }
            )
        ),
    ]


def _happy_orchestrator_script(mandate_id: str) -> list:
    """Orchestrator dispatches to call_purchase_agent for 3x Stability shoes
    at $159, then narrates after the subagent returns.

    The dict-shaped items below are exactly what a real LLM emits and what
    triggered the production AttributeError before we added coercion.
    """
    items = [
        {
            "product_id": "ath_007",
            "name": "Stability Running Shoes",
            "price": "159.00",
            "quantity": 3,
        }
    ]
    # 3 * 159.00 = 477.00; stub transport adds 8% tax → 515.16
    sub = _purchase_chain_script(
        mandate_id,
        items,
        total="477.00",
        post_tax_total="515.16",
    )
    return [
        # Orchestrator turn 1: dispatch to PurchaseAgent
        tool_use_response(
            (
                "call_purchase_agent",
                {
                    "brief": "buy 3 stability running shoes",
                    "merchant_domain": MERCHANT,
                    "items": items,
                },
            )
        ),
        # PurchaseAgent runs inside the tool dispatch and consumes the
        # 8-message subagent script
        *sub,
        # Orchestrator turn 2: friendly confirmation
        text_response("Done — your order is confirmed."),
    ]


def _cancel_orchestrator_script(mandate_id: str) -> list:
    """Same dispatch, but the gate cancels so the PurchaseAgent never runs.
    Only the orchestrator's two top-level turns are needed.
    """
    items = [
        {
            "product_id": "ath_007",
            "name": "Stability Running Shoes",
            "price": "159.00",
            "quantity": 3,
        }
    ]
    return [
        tool_use_response(
            (
                "call_purchase_agent",
                {
                    "brief": "buy 3 stability running shoes",
                    "merchant_domain": MERCHANT,
                    "items": items,
                },
            )
        ),
        # Gate cancels → no subagent invocation → next response is the
        # orchestrator's final narration
        text_response("Cancelled — no charge was made."),
    ]


def _revoked_purchase_chain_script(mandate_id: str) -> list:
    """PurchaseAgent script for a revoked mandate. The model calls
    validate_mandate first, sees ``authorized=false``, and reports
    failure instead of pressing on with create_checkout_session.
    Mirrors what a real LLM does when the prompt says "STOP immediately
    if validate_mandate returns authorized=false".
    """
    items = [
        {
            "product_id": "ath_007",
            "name": "Stability Running Shoes",
            "price": "159.00",
            "quantity": 3,
        }
    ]
    return [
        tool_use_response(
            (
                "validate_mandate",
                {
                    "mandate_id": mandate_id,
                    "amount": "477.00",
                    "vendor": MERCHANT,
                },
            )
        ),
        text_response(
            json.dumps(
                {
                    "order": None,
                    "status": "failed",
                    "reason": "mandate_revoked",
                }
            )
        ),
    ]


def _revoked_orchestrator_script(mandate_id: str) -> list:
    """Orchestrator dispatches to PurchaseAgent; PurchaseAgent reports
    failed; orchestrator narrates the refusal."""
    items = [
        {
            "product_id": "ath_007",
            "name": "Stability Running Shoes",
            "price": "159.00",
            "quantity": 3,
        }
    ]
    sub = _revoked_purchase_chain_script(mandate_id)
    return [
        tool_use_response(
            (
                "call_purchase_agent",
                {
                    "brief": "buy 3 stability running shoes",
                    "merchant_domain": MERCHANT,
                    "items": items,
                },
            )
        ),
        *sub,
        text_response("Your mandate is revoked — no charge was made."),
    ]


def _cap_exceeded_orchestrator_script(mandate_id: str) -> list:
    """Orchestrator pre-flights with check_spending_limits BEFORE dispatching
    to PurchaseAgent. The real ``check_spending_limits`` tool refuses
    because $649 > $500 per-tx cap, so the orchestrator doesn't fire the
    gate at all and produces a friendly refusal directly.
    """
    return [
        tool_use_response(
            (
                "check_spending_limits",
                {
                    "mandate_id": mandate_id,
                    "amount": "649.00",
                },
            )
        ),
        # After seeing authorized=false the orchestrator just narrates
        text_response(
            "I can't make that purchase — it exceeds your $500 "
            "per-transaction limit. Want to pick something cheaper?"
        ),
    ]


# ─── Scenario harness ───────────────────────────────────────────────────────


async def _new_session(monkeypatch_env: Optional[dict] = None):
    """Build a fresh FastAPI app, establish a session, return (httpx client,
    WebSession). The caller is responsible for replacing
    ``sess.orchestrator`` with a fake-client-backed instance before
    firing any POST /chat.

    Clears the module-level ``_SESSIONS`` dict on entry. Without this,
    leftover WebSession objects from earlier tests (whose asyncio queues
    are bound to long-dead event loops) can deadlock when an HTTP route
    here tries to interact with them.
    """
    session_mod._SESSIONS.clear()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    ac = httpx.AsyncClient(transport=transport, base_url="http://test")
    r = await ac.get("/")
    assert r.status_code == 200
    sid_raw = r.cookies.get("ac_session")
    assert sid_raw, "expected ac_session cookie on first GET"
    sid = session_mod._serializer.loads(sid_raw)
    sess = session_mod.get_session_by_id(sid)
    assert sess is not None
    return ac, sess


def _tool_results(fake: FakeAnthropicClient) -> list[dict]:
    """Collect every tool_result block any agent saw, across all turns.
    A real LLM would see the same blocks; if any tool returned
    {"error": ...} the model might still keep going, which is exactly
    how dict-vs-Pydantic bugs hide in green test suites. We surface
    them here so tests can fail explicitly.
    """
    seen_ids: set = set()
    out = []
    for rec in fake.calls:
        for msg in rec.messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tu_id = block.get("tool_use_id")
                        if tu_id in seen_ids:
                            continue
                        seen_ids.add(tu_id)
                        out.append(block)
    return out


def _assert_no_tool_errors(fake: FakeAnthropicClient, allow: tuple[str, ...] = ()) -> None:
    """Fail loudly if any tool returned {"error": ...} during the run.

    This is what catches the dict-vs-Pydantic class of bug: ``_invoke``
    swallows tool exceptions and returns them as ``{"error": ...}``,
    so without this check a test can pass while the chain silently
    fails halfway through.
    """
    bad = []
    for block in _tool_results(fake):
        content = block.get("content", "")
        if isinstance(content, str) and (
            '"error"' in content or "AttributeError" in content or "TypeError" in content
        ):
            if any(a in content for a in allow):
                continue
            bad.append(content[:300])
    assert not bad, "tool returned an error during e2e run:\n" + "\n---\n".join(bad)


def _swap_orchestrator(sess, fake_client: FakeAnthropicClient) -> None:
    """Replace the session's orchestrator with one backed by the fake
    client so subagents share the scripted queue."""
    sess.orchestrator = OrchestratorAgent(
        fake_client,
        confirmation=sess.gate_provider,
        callbacks=StreamingCallbacks(),
        mandate_id=sess.mandate_id,
        available_merchants=list(sess.ctx.merchant_gateway.direct_adapters.keys()),
    )


async def _drive_gate_once(sess, reply: dict, *, timeout: float = 5.0) -> dict:
    """Wait for one ``gate.open`` event on the provider's outbox, then
    push ``reply`` onto its inbox. Returns the observed event."""
    evt = await asyncio.wait_for(sess.gate_provider.outbox.get(), timeout=timeout)
    assert evt["type"] == "gate.open", f"expected gate.open, got {evt!r}"
    await sess.gate_provider.inbox.put(reply)
    return evt


async def _drain_until_done(sess, *, timeout: float = 15.0) -> list[dict]:
    """Pull SSE events from ``sess.sse_queue`` until a ``done`` event
    arrives or the timeout elapses. Returns the full event list (useful
    for asserting text replies)."""
    events: list[dict] = []
    while True:
        evt = await asyncio.wait_for(sess.sse_queue.get(), timeout=timeout)
        events.append(evt)
        if evt["type"] == "done":
            return events
        if evt["type"] == "error":
            # Surface immediately — caller can assert on it
            return events


# ─── Tier 1: purchase chain correctness ─────────────────────────────────────


def test_happy_purchase():
    """Drives chat → call_purchase_agent → 7-step UCP chain → gate confirm
    → order lands.

    This is the test that would have caught the dict-vs-CartItem bug. If
    coercion in tools/purchase_tools.py:update_checkout_session is removed,
    this test fails with AttributeError because items arrives as list[dict].
    """

    async def body():
        ac, sess = await _new_session()
        try:
            fake = FakeAnthropicClient(
                _happy_orchestrator_script(sess.mandate_id),
            )
            _swap_orchestrator(sess, fake)

            gate_task = asyncio.create_task(_drive_gate_once(sess, {"decision": "confirm"}))
            r = await ac.post(
                "/chat",
                data={
                    "message": "buy 3 stability running shoes",
                },
            )
            assert r.status_code == 202

            events = await _drain_until_done(sess)
            await gate_task

            # No SSE-level error event
            assert not any(e["type"] == "error" for e in events), (
                f"unexpected error event in {events}"
            )

            # THE regression catcher for the dict-vs-CartItem bug:
            # if update_checkout_session crashed on i.product_id, its
            # tool_result here would be {"error": "AttributeError", ...}.
            # ``model_dump`` is the known save_order dict-bug documented
            # at the top of this file — allow it until that's fixed.
            _assert_no_tool_errors(fake, allow=("model_dump",))

            # The full purchase chain ran. This is the assertion that
            # would have caught the dict-vs-CartItem bug:
            #   update_checkout_session received list[dict] items and
            #   must have run without raising AttributeError. Audit
            #   log proves the tool fired.
            # Audit log proves the chain ran. NB: only tools that
            # explicitly call ``shared_tools.audit_log`` appear here
            # (validate_mandate, check_spending_limits, save_order,
            # record_mandate_spend do NOT audit). We assert on the
            # ones that do.
            audit = sess.ctx.db.audit_log.all()
            tools_logged = {row["tool"] for row in audit}
            assert "hitl_gate" in tools_logged, (
                f"gate should have been audited; tools={tools_logged}"
            )
            assert "update_checkout_session" in tools_logged, (
                f"update_checkout_session did not complete (this is the "
                f"dict-vs-CartItem regression catcher); "
                f"tools={tools_logged}"
            )
            assert "complete_order" in tools_logged, (
                f"complete_order did not run; tools={tools_logged}"
            )

            # Spend recorded against the mandate (proves the chain
            # reached its final tool successfully)
            from datetime import datetime, timezone

            spent_day, _ = sess.ctx.ap2._compute_spend(
                sess.mandate_id,
                datetime.now(timezone.utc),
            )
            assert spent_day == Decimal("515.16"), f"expected $515.16 spent today, got {spent_day}"

            # Friendly reply made it onto the SSE stream
            text_events = [e for e in events if e["type"] == "text"]
            joined = " ".join(e["data"]["delta"] for e in text_events)
            assert "confirmed" in joined.lower(), f"no confirmation in {joined!r}"

            # Phase 8e: save_order now coerces the dict payload, so
            # the order MUST land in db.orders. This is the assertion
            # the file-level note said to re-enable once save_order
            # was fixed.
            orders = sess.ctx.db.orders.all()
            assert len(orders) == 1, f"expected one persisted order, got {orders}"
            assert orders[0]["order_id"] == "ord_e2e"
            assert orders[0]["merchant_domain"] == MERCHANT
        finally:
            await ac.aclose()

    asyncio.run(body())


def test_cancel_at_gate():
    """User declines at the gate. No order created, no spend recorded,
    friendly cancellation message."""

    async def body():
        ac, sess = await _new_session()
        try:
            fake = FakeAnthropicClient(
                _cancel_orchestrator_script(sess.mandate_id),
            )
            _swap_orchestrator(sess, fake)

            gate_task = asyncio.create_task(_drive_gate_once(sess, {"decision": "cancel"}))
            r = await ac.post(
                "/chat",
                data={
                    "message": "buy 3 stability running shoes",
                },
            )
            assert r.status_code == 202

            events = await _drain_until_done(sess)
            await gate_task

            # No order should have been persisted
            assert sess.ctx.db.orders.all() == []

            # No spend should have been recorded
            spent_day, _ = sess.ctx.ap2._compute_spend(
                sess.mandate_id,
                __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc,
                ),
            )
            assert spent_day == Decimal("0"), f"expected zero spend after cancel, got {spent_day}"

            # Audit log records the cancel
            audit = sess.ctx.db.audit_log.all()
            tools_logged = {row["tool"] for row in audit}
            assert "hitl_gate" in tools_logged
            gate_rows = [r for r in audit if r["tool"] == "hitl_gate"]
            assert any("cancel" in (r.get("action") or "").lower() for r in gate_rows), (
                f"no cancel audit entry: {gate_rows}"
            )
        finally:
            await ac.aclose()

    asyncio.run(body())


def test_cap_exceeded_no_gate_fires():
    """Pre-flight cap check refuses; gate never fires.

    The model calls ``check_spending_limits`` first (per the orchestrator
    prompt's instruction to use mandate caps as the source of truth). The
    real tool refuses with ``authorized=false``. The model produces a
    friendly refusal text directly — no call_purchase_agent dispatch.
    """

    async def body():
        ac, sess = await _new_session()
        try:
            fake = FakeAnthropicClient(
                _cap_exceeded_orchestrator_script(sess.mandate_id),
            )
            _swap_orchestrator(sess, fake)

            r = await ac.post(
                "/chat",
                data={
                    "message": "buy a $649 thing",
                },
            )
            assert r.status_code == 202

            events = await _drain_until_done(sess)

            # No order, no spend, no gate event ever fired
            assert sess.ctx.db.orders.all() == []
            assert sess.gate_provider.outbox.empty()

            # Friendly refusal in the SSE stream
            text_events = [e for e in events if e["type"] == "text"]
            joined = " ".join(e["data"]["delta"] for e in text_events).lower()
            assert "exceeds" in joined or "limit" in joined, f"refusal not in stream: {joined!r}"

            # No spend recorded (the cap check refused before any
            # purchase tool ran). check_spending_limits doesn't audit,
            # so we verify by side-effect: zero spend + no gate event.
            from datetime import datetime, timezone

            spent_day, _ = sess.ctx.ap2._compute_spend(
                sess.mandate_id,
                datetime.now(timezone.utc),
            )
            assert spent_day == Decimal("0")

            # No PurchaseAgent dispatch happened — call_purchase_agent
            # would have audited via hitl_gate.
            audit = sess.ctx.db.audit_log.all()
            tools_logged = {row["tool"] for row in audit}
            assert "hitl_gate" not in tools_logged, (
                f"gate should not have fired; audit={tools_logged}"
            )
        finally:
            await ac.aclose()

    asyncio.run(body())


def test_revoked_mandate_blocks_purchase():
    """Revoke mandate before the POST. PaymentGateway's re-validation
    inside ``validate_mandate`` refuses; the PurchaseAgent reports
    failure (mirroring how a real LLM stops after authorized=false).
    No spend is recorded."""

    async def body():
        ac, sess = await _new_session()
        try:
            # Revoke before any purchase attempt
            sess.ctx.ap2.revoke_mandate(sess.mandate_id)

            fake = FakeAnthropicClient(
                _revoked_orchestrator_script(sess.mandate_id),
            )
            _swap_orchestrator(sess, fake)

            gate_task = asyncio.create_task(_drive_gate_once(sess, {"decision": "confirm"}))
            r = await ac.post(
                "/chat",
                data={
                    "message": "buy 3 stability running shoes",
                },
            )
            assert r.status_code == 202
            await _drain_until_done(sess)
            await gate_task

            # No spend recorded — the PurchaseAgent halted after
            # validate_mandate returned authorized=false. The mandate
            # is the source of truth and re-validates inside the tool
            # layer regardless of model behaviour.
            from datetime import datetime, timezone

            spent_day, _ = sess.ctx.ap2._compute_spend(
                sess.mandate_id,
                datetime.now(timezone.utc),
            )
            assert spent_day == Decimal("0"), (
                f"expected zero spend on revoked mandate, got {spent_day}"
            )

            # The PurchaseAgent halted early — no UCP chain tool that
            # audits (create/update/get_token/complete) should have run.
            audit = sess.ctx.db.audit_log.all()
            tools_logged = {row["tool"] for row in audit}
            for blocked in (
                "update_checkout_session",
                "get_payment_token",
                "complete_order",
            ):
                assert blocked not in tools_logged, (
                    f"{blocked} should not run on revoked mandate; audit={tools_logged}"
                )
        finally:
            await ac.aclose()

    asyncio.run(body())


# ─── Tier 2: UI flow correctness ────────────────────────────────────────────


def test_click_to_add_updates_session():
    """POST /cart/add for two products: click_basket and conversation note
    both update on each click."""

    async def body():
        ac, sess = await _new_session()
        try:
            r1 = await ac.post(
                f"/cart/add/{MERCHANT}/ath_007",
                data={"quantity": "2", "variant_id": "ath_007-8-Standard"},
            )
            assert r1.status_code == 200

            r2 = await ac.post(
                f"/cart/add/{MERCHANT}/ath_001",
                data={"quantity": "1", "variant_id": "ath_001-8"},
            )
            assert r2.status_code == 200

            # click_basket has both items
            items = sess.click_basket.get(MERCHANT, [])
            ids = {it["product_id"] for it in items}
            assert ids == {"ath_007", "ath_001"}, f"got {ids}"

            # Each click appended a [via UI click] note
            click_notes = [
                turn
                for turn in sess.ctx.session.conversation
                if turn.get("role") == "user"
                and isinstance(turn.get("content"), list)
                and any("[via UI click]" in (b.get("text", "") or "") for b in turn["content"])
            ]
            assert len(click_notes) == 2, f"expected 2 click notes, got {len(click_notes)}"
        finally:
            await ac.aclose()

    asyncio.run(body())


def test_stale_cancel_does_not_poison_next_gate():
    """Regression test for the stale-cancel bug.

    Production scenario: every WS reconnect during page navigation pushed
    a synthetic ``{decision: cancel}`` onto ``inbox``. The next genuine
    gate consumed that stale cancel before the user could click, and the
    purchase aborted with ``cancelled_by_user`` even though the user
    pressed CONFIRM.

    This test pre-loads a stale cancel into the inbox, fires a purchase,
    and asserts the gate is APPROVED (drain in ``_present`` discarded the
    stale reply). Without the drain, the gate would consume the stale
    cancel and ``update_checkout_session`` would never run.
    """

    async def body():
        ac, sess = await _new_session()
        try:
            # Poison the inbox with a stale cancel (mimics a WS reconnect
            # during page navigation)
            await sess.gate_provider.inbox.put({"decision": "cancel"})

            fake = FakeAnthropicClient(
                _happy_orchestrator_script(sess.mandate_id),
            )
            _swap_orchestrator(sess, fake)

            gate_task = asyncio.create_task(_drive_gate_once(sess, {"decision": "confirm"}))
            r = await ac.post(
                "/chat",
                data={
                    "message": "buy 3 stability running shoes",
                },
            )
            assert r.status_code == 202

            events = await _drain_until_done(sess)
            await gate_task

            # If the drain works the gate is APPROVED, the chain runs,
            # and update_checkout_session is audited.
            audit = sess.ctx.db.audit_log.all()
            tools_logged = {row["tool"] for row in audit}
            gate_rows = [r for r in audit if r["tool"] == "hitl_gate"]
            gate_actions = " ".join(r.get("action", "") for r in gate_rows)

            assert "approved" in gate_actions, (
                f"stale cancel poisoned the gate: actions={gate_actions!r}"
            )
            assert "update_checkout_session" in tools_logged, (
                f"chain halted before update_checkout_session: tools={tools_logged}"
            )
        finally:
            await ac.aclose()

    asyncio.run(body())


def test_click_then_review_purchase_runs_gate():
    """Click "Add to cart" twice → POST /chat with the canned "Review
    purchase" text → orchestrator dispatches to PurchaseAgent → gate →
    confirm → order persisted.

    Verifies that clicks and chat flow converge at the same gate.
    """

    async def body():
        ac, sess = await _new_session()
        try:
            # Two clicks add items to the draft cart
            await ac.post(
                f"/cart/add/{MERCHANT}/ath_007",
                data={"quantity": "3", "variant_id": "ath_007-8-Standard"},
            )

            # Script for the "buy these" turn — same script as happy path
            fake = FakeAnthropicClient(
                _happy_orchestrator_script(sess.mandate_id),
            )
            _swap_orchestrator(sess, fake)

            gate_task = asyncio.create_task(_drive_gate_once(sess, {"decision": "confirm"}))

            # Same canned message the Review-purchase button sends
            r = await ac.post(
                "/chat",
                data={
                    "message": "Please buy everything in my cart now.",
                },
            )
            assert r.status_code == 202

            events = await _drain_until_done(sess)
            await gate_task

            assert not any(e["type"] == "error" for e in events)

            # Catches the dict-vs-CartItem regression
            _assert_no_tool_errors(fake, allow=("model_dump",))

            # Same chain ran as in test_happy_purchase. The gate fired
            # and confirmed; update_checkout_session ran with dict items
            # (would have caught the dict-vs-CartItem bug); spend was
            # recorded. The orders.all() assertion is blocked by the
            # save_order bug — see file-level note.
            audit = sess.ctx.db.audit_log.all()
            tools_logged = {row["tool"] for row in audit}
            assert "hitl_gate" in tools_logged
            assert "update_checkout_session" in tools_logged
            assert "complete_order" in tools_logged

            # The original click note is still in the conversation —
            # proves the chat and click paths share session state
            click_notes = [
                turn
                for turn in sess.ctx.session.conversation
                if turn.get("role") == "user"
                and isinstance(turn.get("content"), list)
                and any("[via UI click]" in (b.get("text", "") or "") for b in turn["content"])
            ]
            assert click_notes, "click note should still be in conversation"
        finally:
            await ac.aclose()

    asyncio.run(body())
