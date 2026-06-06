"""SSE single-active-connection takeover — the "responses/cards don't show
until I switch pages" fix.

Root cause: the per-session ``sse_queue`` is a single ``asyncio.Queue`` and
``Queue.get`` is NOT a broadcast — each event goes to exactly one consumer.
The empty→active chat navigation (the flicker fix navigates to ``/chat`` after
POST) briefly runs TWO ``/chat/stream`` connections: the page that just
navigated away (whose server-side ``event_gen`` is still blocked in ``get()``
because a disconnect isn't noticed until the next event arrives) and the
freshly-loaded page. ``asyncio.Queue`` then splits the orchestrator's rapid
``products``/``text``/``done`` burst round-robin across the two blocked
waiters, so the new page rendered the summary but lost the product cards (or
rendered them out of order). Reloading worked only because the page re-renders
cards from ``session.product_card_sets``.

The fix (``web.session.WebSession.new_stream_generation`` +
``web.routers.chat._session_sse_events``): each ``/chat/stream`` connection
gets a ``superseded`` future that is resolved the instant a NEWER connection
opens. The stream races ``sse_queue.get()`` against that future and stops
consuming AT ONCE when superseded — well before the orchestrator's burst
arrives (seconds later, after the LLM round-trips) — so only the active
connection competes for the burst and events arrive in order (cards-first).

ASYNC RULE: this file sorts alphabetically BEFORE ``test_user_journeys.py``
(``s`` < ``u``), so per CLAUDE.md it MUST use
``asyncio.get_event_loop().run_until_complete()`` and NEVER ``asyncio.run()``
(which would close the loop and contaminate later suites on Python 3.9).

MULTI-MERCHANT: the handoff burst is exercised with real Athletic Co, Audio
Hub, Coffee Bar and cross-merchant product payloads per the project testing
rule (never validate a product flow against a single category).
"""

from __future__ import annotations

import asyncio

import pytest

from config.catalogue import MERCHANTS
from web.routers.chat import _KEEPALIVE, _session_sse_events
from web.session import WebSession


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _run(coro):
    """Drive a coroutine without closing the loop (CLAUDE.md async rule)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_session() -> WebSession:
    """A WebSession with only the fields the SSE path touches.

    ``sse_queue`` and the stream-generation futures are created lazily inside
    the running loop, so this is safe to call from within ``_run``.
    """
    return WebSession(
        session_id="sess_test",
        db=None,
        ctx=None,
        orchestrator=None,
        mandate_id="m_test",
        gate_provider=None,
    )


async def _never_disconnected() -> bool:
    return False


async def _always_disconnected() -> bool:
    return True


async def _collect(agen, *, stop_on_keepalive: bool = True, stop_on_type: str | None = None):
    """Drain an ``_session_sse_events`` generator into a list.

    Stops at the first ``_KEEPALIVE`` (queue idle) or when an event of
    ``stop_on_type`` is seen, so tests don't block forever on a live stream.
    """
    out: list = []
    async for item in agen:
        if item is _KEEPALIVE:
            if stop_on_keepalive:
                break
            out.append(item)
            continue
        out.append(item)
        if stop_on_type is not None and item.get("type") == stop_on_type:
            break
    return out


def _merchant_product(domain: str) -> dict:
    """First seed product for a merchant, as a plain dict (SSE payload shape)."""
    return dict(MERCHANTS[domain][0])


def _products_event(domains: list[str]) -> dict:
    return {
        "type": "products",
        "data": {"products": [_merchant_product(d) for d in domains]},
    }


ATHLETIC = "athletic-co.myshopify.com"
AUDIO = "audio-hub.myshopify.com"
COFFEE = "coffee-bar.myshopify.com"


# ─── Session generation mechanics ─────────────────────────────────────────────


class TestStreamGeneration:
    def test_new_generation_increments_and_supersedes_previous(self):
        async def _t():
            sess = _make_session()
            gen1, sup1 = sess.new_stream_generation()
            assert gen1 == 1
            assert not sup1.done(), "first connection is not yet superseded"
            assert sess.stream_generation == 1

            gen2, sup2 = sess.new_stream_generation()
            assert gen2 == 2
            assert sess.stream_generation == 2
            # Opening a newer connection retires the previous one.
            assert sup1.done(), "previous connection's future must resolve"
            assert not sup2.done(), "newest connection stays active"

        _run(_t())

    def test_three_connections_each_predecessor_retired(self):
        async def _t():
            sess = _make_session()
            _, sup1 = sess.new_stream_generation()
            _, sup2 = sess.new_stream_generation()
            _, sup3 = sess.new_stream_generation()
            assert sup1.done() and sup2.done()
            assert not sup3.done()
            assert sess.stream_generation == 3

        _run(_t())


# ─── Single-stream behaviour ───────────────────────────────────────────────────


class TestSingleStream:
    def test_events_yielded_in_order(self):
        async def _t():
            sess = _make_session()
            _, sup = sess.new_stream_generation()
            q = sess.sse_queue
            burst = [
                {"type": "user", "data": {"text": "find me running shoes"}},
                _products_event([ATHLETIC]),
                {"type": "text", "data": {"delta": "Here are two solid options."}},
                {"type": "done", "data": {}},
            ]
            for evt in burst:
                q.put_nowait(evt)

            got = await _collect(_session_sse_events(sess, sup, _never_disconnected, timeout=0.05))
            assert got == burst

        _run(_t())

    def test_idle_timeout_yields_keepalive(self):
        async def _t():
            sess = _make_session()
            _, sup = sess.new_stream_generation()
            # Empty queue, never superseded → first item is the keepalive.
            agen = _session_sse_events(sess, sup, _never_disconnected, timeout=0.02)
            first = await agen.__anext__()
            assert first is _KEEPALIVE
            await agen.aclose()

        _run(_t())

    def test_disconnect_ends_stream_immediately(self):
        async def _t():
            sess = _make_session()
            _, sup = sess.new_stream_generation()
            sess.sse_queue.put_nowait({"type": "text", "data": {"delta": "hi"}})
            got = await _collect(_session_sse_events(sess, sup, _always_disconnected, timeout=0.05))
            # Disconnected before consuming → nothing yielded, event untouched.
            assert got == []
            assert sess.sse_queue.qsize() == 1

        _run(_t())


# ─── Takeover: the actual bug ──────────────────────────────────────────────────


class TestTakeover:
    def test_superseded_stream_stops_without_consuming(self):
        """A stream blocked on an empty queue must retire the instant a newer
        connection opens — and consume nothing."""

        async def _t():
            sess = _make_session()
            _, sup1 = sess.new_stream_generation()
            collected: list = []

            async def consume():
                async for item in _session_sse_events(sess, sup1, _never_disconnected, timeout=5.0):
                    collected.append(item)

            task = asyncio.ensure_future(consume())
            await asyncio.sleep(0.05)  # let it block in get()
            assert not task.done()

            # A newer connection opens → supersedes the first.
            sess.new_stream_generation()
            await asyncio.wait_for(task, timeout=1.0)

            assert collected == [], "superseded stream must not yield anything"
            assert sess.sse_queue.empty(), "superseded stream must not consume"

        _run(_t())

    @pytest.mark.parametrize(
        "domains",
        [
            pytest.param([ATHLETIC], id="athletic-co-single"),
            pytest.param([AUDIO], id="audio-hub-single"),
            pytest.param([COFFEE], id="coffee-bar-single"),
            pytest.param([ATHLETIC, AUDIO, COFFEE], id="cross-merchant"),
        ],
    )
    def test_active_stream_receives_full_burst_in_order(self, domains):
        """The crux: with the OLD connection still blocked, a NEW connection
        takes over, and the orchestrator's products→text→done burst is
        delivered to the active connection IN ORDER (cards before summary).
        The stale connection receives nothing — no round-robin split."""

        async def _t():
            sess = _make_session()
            q = sess.sse_queue

            # Connection A (the page about to navigate away).
            _, supA = sess.new_stream_generation()
            collectedA: list = []

            async def consumeA():
                async for item in _session_sse_events(sess, supA, _never_disconnected, timeout=5.0):
                    collectedA.append(item)

            taskA = asyncio.ensure_future(consumeA())
            await asyncio.sleep(0.05)  # A blocks in get()

            # Connection B (the freshly-loaded active page) takes over.
            _, supB = sess.new_stream_generation()
            collectedB: list = []

            async def consumeB():
                async for item in _session_sse_events(sess, supB, _never_disconnected, timeout=5.0):
                    collectedB.append(item)
                    if item is not _KEEPALIVE and item.get("type") == "done":
                        break

            taskB = asyncio.ensure_future(consumeB())
            await asyncio.sleep(0.05)  # A has retired; B blocks in get()
            assert taskA.done(), "A must retire once B connects"

            # Orchestrator burst — emitted seconds after navigation in reality.
            products = _products_event(domains)
            text = {"type": "text", "data": {"delta": "Two solid picks."}}
            done = {"type": "done", "data": {}}
            for evt in (products, text, done):
                await q.put(evt)

            await asyncio.wait_for(taskB, timeout=1.0)

            assert collectedA == [], "stale connection must receive nothing"
            assert collectedB == [products, text, done], "active stream: ordered burst"
            # Cards-first ordering is preserved.
            types = [e["type"] for e in collectedB]
            assert types.index("products") < types.index("text") < types.index("done")
            assert sess.sse_queue.empty()

        _run(_t())

    def test_late_user_event_then_burst_after_takeover(self):
        """Mirrors the real flow: A enqueues the user echo, B takes over, then
        the burst arrives. B must get the user echo + cards + summary in order;
        A gets nothing."""

        async def _t():
            sess = _make_session()
            q = sess.sse_queue

            _, supA = sess.new_stream_generation()
            collectedA: list = []

            async def consumeA():
                async for item in _session_sse_events(sess, supA, _never_disconnected, timeout=5.0):
                    collectedA.append(item)

            taskA = asyncio.ensure_future(consumeA())
            await asyncio.sleep(0.05)

            _, supB = sess.new_stream_generation()
            collectedB: list = []

            async def consumeB():
                async for item in _session_sse_events(sess, supB, _never_disconnected, timeout=5.0):
                    collectedB.append(item)
                    if item is not _KEEPALIVE and item.get("type") == "done":
                        break

            taskB = asyncio.ensure_future(consumeB())
            await asyncio.sleep(0.05)
            assert taskA.done()

            user = {"type": "user", "data": {"text": "compare headphones across stores"}}
            products = _products_event([AUDIO, ATHLETIC])
            text = {"type": "text", "data": {"delta": "Cheapest is the Audio Hub pair."}}
            done = {"type": "done", "data": {}}
            for evt in (user, products, text, done):
                await q.put(evt)

            await asyncio.wait_for(taskB, timeout=1.0)
            assert collectedA == []
            assert collectedB == [user, products, text, done]

        _run(_t())
