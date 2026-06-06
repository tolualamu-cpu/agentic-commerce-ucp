"""Gate WebSocket single-active-connection takeover — the "Review purchase
does nothing / no confirmation modal appears" fix.

Root cause (identical to the SSE bug, different pipe): the per-session gate
``outbox`` is a single ``asyncio.Queue`` and ``Queue.get`` is NOT a broadcast —
each event goes to exactly ONE consumer. Every page navigation auto-reconnects
the ``/gate/ws`` WebSocket, so two ``pump_out`` consumers briefly overlap: the
page that just navigated away (whose server-side ``pump_out`` is still blocked
in ``outbox.get()`` because a socket disconnect isn't noticed until the next
event arrives) and the freshly-loaded page. When the orchestrator then puts the
purchase-confirmation ``gate.open`` on the outbox, ``asyncio.Queue`` wakes the
OLDER waiter, which writes it to a socket the browser already abandoned. The
active page's modal never opens, so the user can never confirm and the purchase
silently stalls.

The fix (``WebsocketConfirmProvider.new_ws_generation`` +
``web.routers.gate_ws`` ``pump_out`` built on ``stream_until_superseded``):
each ``/gate/ws`` connection gets a ``superseded`` future resolved the instant a
NEWER connection opens. ``pump_out`` races ``outbox.get()`` against that future
and retires AT ONCE when superseded — WITHOUT consuming — so only the active
connection drains the outbox and the ``gate.open`` reaches the live modal.

ASYNC RULE: this file sorts alphabetically BEFORE ``test_user_journeys.py``
(``g`` < ``u``), so per CLAUDE.md it MUST use
``asyncio.get_event_loop().run_until_complete()`` and NEVER ``asyncio.run()``
(which would close the loop and contaminate later suites on Python 3.9).

MULTI-MERCHANT: the gate handoff is exercised with Athletic Co, Audio Hub and
Coffee Bar purchase gates across the soft / explicit / >$500 price tiers per the
project testing rule (never validate a flow against a single category).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from cli.confirmation import GateData
from web.gate_provider import WebsocketConfirmProvider
from web.stream_takeover import stream_until_superseded


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _run(coro):
    """Drive a coroutine without closing the loop (CLAUDE.md async rule)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# Per-merchant, per-tier purchase gates. Tiers mirror the project rule:
# soft (<$30), explicit ($100–$500), full summary (>$500).
ATHLETIC_SOFT = GateData(
    merchant_domain="athletic-co.myshopify.com",
    amount=Decimal("24.00"),
    currency="USD",
    item_summary="Performance Crew Socks (3-pack)",
)
AUDIO_EXPLICIT = GateData(
    merchant_domain="audio-hub.myshopify.com",
    amount=Decimal("249.00"),
    currency="USD",
    item_summary="Studio Wireless Headphones",
)
COFFEE_FULL = GateData(
    merchant_domain="coffee-bar.myshopify.com",
    amount=Decimal("899.00"),
    currency="USD",
    item_summary="Prosumer Espresso Machine",
    full_summary="Prosumer Espresso Machine — $899.00. High-value purchase.",
    risk_flags=["HIGH_VALUE"],
)


def _gate_event(provider: WebsocketConfirmProvider, gate: GateData, tier: str) -> dict:
    """The exact shape ``_present`` puts on the outbox, built directly so the
    test doesn't need a live orchestrator turn."""
    from web.gate_provider import _gate_to_dict

    return {"type": "gate.open", "tier": tier, "gate": _gate_to_dict(gate)}


# ─── Generation mechanics ──────────────────────────────────────────────────────


class TestWsGeneration:
    def test_new_generation_increments_and_supersedes_previous(self):
        async def _t():
            provider = WebsocketConfirmProvider()
            gen1, sup1 = provider.new_ws_generation()
            assert gen1 == 1
            assert not sup1.done(), "first connection is not yet superseded"
            assert provider.ws_generation == 1

            gen2, sup2 = provider.new_ws_generation()
            assert gen2 == 2
            assert provider.ws_generation == 2
            assert sup1.done(), "previous connection's future must resolve"
            assert not sup2.done(), "newest connection stays active"

        _run(_t())

    def test_three_connections_each_predecessor_retired(self):
        async def _t():
            provider = WebsocketConfirmProvider()
            _, sup1 = provider.new_ws_generation()
            _, sup2 = provider.new_ws_generation()
            _, sup3 = provider.new_ws_generation()
            assert sup1.done() and sup2.done()
            assert not sup3.done()
            assert provider.ws_generation == 3

        _run(_t())


# ─── pump_out drain behaviour ───────────────────────────────────────────────────


class TestSingleConnectionDrains:
    @pytest.mark.parametrize(
        "gate,tier",
        [
            pytest.param(ATHLETIC_SOFT, "soft", id="athletic-soft"),
            pytest.param(AUDIO_EXPLICIT, "explicit", id="audio-explicit"),
            pytest.param(COFFEE_FULL, "explicit", id="coffee-full-summary"),
        ],
    )
    def test_active_connection_receives_gate_open(self, gate, tier):
        """A lone connection drains the gate.open the orchestrator enqueues."""

        async def _t():
            provider = WebsocketConfirmProvider()
            _, sup = provider.new_ws_generation()
            evt = _gate_event(provider, gate, tier)
            await provider.outbox.put(evt)

            got = []
            async for item in stream_until_superseded(provider.outbox, sup):
                got.append(item)
                break  # one event is enough for this assertion

            assert got == [evt]
            assert got[0]["type"] == "gate.open"
            assert got[0]["gate"]["merchant_domain"] == gate.merchant_domain

        _run(_t())


# ─── Takeover: the actual bug ──────────────────────────────────────────────────


class TestGateTakeover:
    def test_superseded_pump_out_stops_without_consuming(self):
        """A pump_out blocked on an empty outbox must retire the instant a
        newer /gate/ws connects — and consume nothing."""

        async def _t():
            provider = WebsocketConfirmProvider()
            _, sup1 = provider.new_ws_generation()
            collected: list = []

            async def consume():
                async for item in stream_until_superseded(provider.outbox, sup1):
                    collected.append(item)

            task = asyncio.ensure_future(consume())
            await asyncio.sleep(0.05)  # let it block in get()
            assert not task.done()

            provider.new_ws_generation()  # newer connection supersedes
            await asyncio.wait_for(task, timeout=1.0)

            assert collected == [], "superseded pump_out must not yield anything"
            assert provider.outbox.empty(), "superseded pump_out must not consume"

        _run(_t())

    @pytest.mark.parametrize(
        "gate,tier",
        [
            pytest.param(ATHLETIC_SOFT, "soft", id="athletic-soft"),
            pytest.param(AUDIO_EXPLICIT, "explicit", id="audio-explicit"),
            pytest.param(COFFEE_FULL, "explicit", id="coffee-full-summary"),
        ],
    )
    def test_gate_open_reaches_active_not_stale_connection(self, gate, tier):
        """THE BUG: connection A (page navigating away) is still blocked in
        outbox.get(); connection B (freshly-loaded active page) takes over.
        The orchestrator's gate.open must reach B, NOT be stolen by A — which
        is what left "Review purchase" with no modal."""

        async def _t():
            provider = WebsocketConfirmProvider()

            # Connection A — the page about to navigate away.
            _, supA = provider.new_ws_generation()
            collectedA: list = []

            async def consumeA():
                async for item in stream_until_superseded(provider.outbox, supA):
                    collectedA.append(item)

            taskA = asyncio.ensure_future(consumeA())
            await asyncio.sleep(0.05)  # A blocks in get()

            # Connection B — the active page — takes over.
            _, supB = provider.new_ws_generation()
            collectedB: list = []

            async def consumeB():
                async for item in stream_until_superseded(provider.outbox, supB):
                    collectedB.append(item)
                    break  # stop once we have the gate.open

            taskB = asyncio.ensure_future(consumeB())
            await asyncio.sleep(0.05)
            assert taskA.done(), "A must retire once B connects"

            # Orchestrator opens the purchase gate (seconds later in reality).
            evt = _gate_event(provider, gate, tier)
            await provider.outbox.put(evt)

            await asyncio.wait_for(taskB, timeout=1.0)

            assert collectedA == [], "stale connection must NOT steal gate.open"
            assert collectedB == [evt], "active connection receives gate.open"
            assert provider.outbox.empty()

        _run(_t())

    def test_confirm_reply_completes_after_takeover(self):
        """End-to-end through the provider: B takes over, the gate opens on B,
        the user's CONFIRM reply lands on the inbox, and ``_present`` returns a
        confirm decision — i.e. the purchase can actually complete."""

        async def _t():
            provider = WebsocketConfirmProvider()

            # A connects, blocks; B takes over.
            _, supA = provider.new_ws_generation()
            collectedA: list = []

            async def consumeA():
                async for item in stream_until_superseded(provider.outbox, supA):
                    collectedA.append(item)

            taskA = asyncio.ensure_future(consumeA())
            await asyncio.sleep(0.05)

            _, supB = provider.new_ws_generation()
            collectedB: list = []

            async def consumeB():
                async for item in stream_until_superseded(provider.outbox, supB):
                    collectedB.append(item)
                    break

            taskB = asyncio.ensure_future(consumeB())
            await asyncio.sleep(0.05)
            assert taskA.done()

            # Orchestrator presents the gate and awaits a reply.
            present = asyncio.ensure_future(provider.explicit_confirm(AUDIO_EXPLICIT))
            await asyncio.wait_for(taskB, timeout=1.0)

            # B received the gate.open; A got nothing.
            assert collectedA == []
            assert len(collectedB) == 1 and collectedB[0]["type"] == "gate.open"

            # The active page's CONFIRM click pushes onto the inbox.
            await provider.inbox.put({"decision": "confirm", "text": ""})
            resp = await asyncio.wait_for(present, timeout=1.0)
            assert resp.decision == "confirm", "purchase confirmation completes"

        _run(_t())

    def test_stale_connection_cannot_swallow_reply_path(self):
        """Two overlapping connections, but only the active one's gate.open is
        delivered, so a single coherent gate cycle runs (no split across the
        two sockets). Mirrors the multi-merchant cross-store buy."""

        async def _t():
            provider = WebsocketConfirmProvider()

            _, supA = provider.new_ws_generation()

            async def consumeA():
                got = []
                async for item in stream_until_superseded(provider.outbox, supA):
                    got.append(item)
                return got

            taskA = asyncio.ensure_future(consumeA())
            await asyncio.sleep(0.05)

            _, supB = provider.new_ws_generation()
            await asyncio.sleep(0)
            await asyncio.wait_for(taskA, timeout=1.0)
            assert taskA.result() == [], "A retired with nothing"

            # Now B is the sole consumer; two gates back-to-back both reach B.
            collectedB: list = []

            async def consumeB():
                async for item in stream_until_superseded(provider.outbox, supB):
                    collectedB.append(item)
                    if len(collectedB) == 2:
                        break

            taskB = asyncio.ensure_future(consumeB())
            await provider.outbox.put(_gate_event(provider, ATHLETIC_SOFT, "soft"))
            await provider.outbox.put(_gate_event(provider, COFFEE_FULL, "explicit"))
            await asyncio.wait_for(taskB, timeout=1.0)

            assert len(collectedB) == 2
            domains = [e["gate"]["merchant_domain"] for e in collectedB]
            assert domains == [
                "athletic-co.myshopify.com",
                "coffee-bar.myshopify.com",
            ]

        _run(_t())


# ─── Self-healing replay: current_gate() ─────────────────────────────────────
#
# The takeover (above) makes the ACTIVE connection the sole drainer, but it
# still relies on the right connection being alive at the exact instant the
# orchestrator enqueues gate.open. A reconnect a moment later (the page that
# clicked "Review purchase" navigates, or the WS drops and re-opens) would miss
# the already-drained event entirely — modal never opens, awaiting_input stays
# True, every later chat message is swallowed into the invisible gate. The
# permanent fix is REPLAY: the provider remembers the pending gate and /gate/ws
# re-sends it on every new connection, so whichever page is actually open
# converges to showing the modal. These tests pin that provider contract.


class TestPendingGateReplay:
    def test_current_gate_none_before_any_gate(self):
        async def _t():
            provider = WebsocketConfirmProvider()
            assert provider.current_gate() is None

        _run(_t())

    @pytest.mark.parametrize(
        "gate,tier",
        [
            pytest.param(ATHLETIC_SOFT, "soft", id="athletic-soft"),
            pytest.param(AUDIO_EXPLICIT, "explicit", id="audio-explicit"),
            pytest.param(COFFEE_FULL, "explicit", id="coffee-full-summary"),
        ],
    )
    def test_pending_gate_is_replayable_while_awaiting(self, gate, tier):
        """While the orchestrator is blocked in _present, current_gate()
        returns the exact gate.open a freshly-connected /gate/ws must replay."""

        async def _t():
            provider = WebsocketConfirmProvider()
            method = provider.soft_confirm if tier == "soft" else provider.explicit_confirm
            present = asyncio.ensure_future(method(gate))
            await asyncio.sleep(0.05)  # let _present enqueue + flag awaiting

            assert provider.awaiting_input is True
            pending = provider.current_gate()
            assert pending is not None, "a pending gate must be replayable"
            assert pending["type"] == "gate.open"
            assert pending["gate"]["merchant_domain"] == gate.merchant_domain
            # The replay payload is byte-identical to what was put on the outbox
            # (so the browser modal renders the same thing either way).
            queued = await asyncio.wait_for(provider.outbox.get(), timeout=1.0)
            assert queued == pending

            # Resolve the gate so the task doesn't dangle.
            await provider.inbox.put({"decision": "confirm", "text": ""})
            resp = await asyncio.wait_for(present, timeout=1.0)
            assert resp.decision == "confirm"

        _run(_t())

    def test_current_gate_cleared_after_gate_resolves(self):
        """Once a reply lands and _present returns, the replay snapshot must be
        dropped so a late reconnect can't replay a stale modal."""

        async def _t():
            provider = WebsocketConfirmProvider()
            present = asyncio.ensure_future(provider.explicit_confirm(AUDIO_EXPLICIT))
            await asyncio.sleep(0.05)
            assert provider.current_gate() is not None

            await provider.inbox.put({"decision": "confirm", "text": ""})
            await asyncio.wait_for(present, timeout=1.0)

            assert provider.awaiting_input is False
            assert provider.current_gate() is None, "resolved gate must not replay"

        _run(_t())

    def test_current_gate_guarded_on_awaiting_input(self):
        """current_gate() must gate on awaiting_input, not just the snapshot —
        belt-and-braces against a stale _pending_gate ever leaking out."""

        async def _t():
            provider = WebsocketConfirmProvider()
            provider._pending_gate = {"type": "gate.open", "gate": {}}
            provider.awaiting_input = False
            assert provider.current_gate() is None

        _run(_t())
