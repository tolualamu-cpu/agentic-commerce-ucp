"""Single-active-connection takeover for per-session event queues.

Two browser-facing pipes in this app are single-consumer ``asyncio.Queue``s —
each event is delivered to exactly ONE consumer (``Queue.get`` is not a
broadcast):

  - the SSE ``sse_queue`` drained by ``GET /chat/stream`` (chat replies,
    product cards, cart ``click`` confirmations, ``cart_update`` badge), and
  - the gate ``outbox`` drained by the ``/gate/ws`` WebSocket (the
    purchase-confirmation ``gate.open`` modal).

During page navigation two connections briefly overlap: the page the browser
just left (whose server-side consumer is still blocked in ``get()`` because a
disconnect isn't noticed until the next event arrives) and the freshly-loaded
page. ``asyncio.Queue`` wakes the OLDER waiter first, so the navigating-away
connection STEALS events off the queue and writes them to a socket the browser
already abandoned. The new page then never sees them. This is why chat
responses / product cards didn't render until you switched pages, and why
"Review purchase" showed no confirmation modal at all (the ``gate.open`` was
stolen by a stale connection, so the purchase could never be confirmed).

The fix lives here so BOTH pipes share ONE tested implementation:

  - ``StreamGeneration`` hands each new connection a monotonic generation and a
    single-use ``superseded`` future that is resolved the instant a NEWER
    connection opens.
  - ``stream_until_superseded`` races ``queue.get()`` against that future and
    stops consuming the moment it is superseded — well before the next event
    burst — so only the active connection drains the queue. If it is superseded
    at the exact moment it popped an event, the event is handed back to the
    queue so the active connection still receives it. A cancelled ``get()`` is
    always safe: cancellation only happens when ``get()`` did not complete, so
    nothing was popped.

NB: no ``from __future__ import annotations`` needed here, but kept asyncio-only
so it is importable from both HTTP and WebSocket routers without FastAPI types.
"""

import asyncio

# Sentinel yielded by ``stream_until_superseded`` when ``timeout`` elapses with
# no event. Callers that pass a ``timeout`` map it to a heartbeat; callers that
# pass no timeout never see it.
KEEPALIVE = object()


class StreamGeneration:
    """Tracks the most-recently-opened connection for one queue.

    Each connection calls :meth:`next` on connect to claim a generation and
    receive its ``superseded`` future. Opening a newer connection resolves the
    previous one's future, signalling the older consumer to retire at once.

    Single-use futures (one per connection, awaited by exactly one consumer)
    avoid the shared-``Event`` clearing races that a single flag would suffer.
    """

    def __init__(self) -> None:
        self._generation = 0
        self._superseded = None  # type: asyncio.Future | None

    @property
    def current(self) -> int:
        """Generation id of the currently-active connection (0 if none yet)."""
        return self._generation

    def next(self):
        """Claim a fresh generation for a new connection.

        Increments the counter (newest connection holds the highest
        generation), resolves the PREVIOUS connection's ``superseded`` future
        so an older consumer blocked on ``get()`` wakes immediately, installs a
        new future for this connection, and returns ``(generation, future)``.

        Must be called with a running event loop (it creates a Future).
        """
        self._generation += 1
        if self._superseded is not None and not self._superseded.done():
            self._superseded.set_result(None)
        self._superseded = asyncio.get_event_loop().create_future()
        return self._generation, self._superseded


async def stream_until_superseded(queue, superseded, *, timeout=None, is_disconnected=None):
    """Yield events from ``queue`` for ONE connection until it is superseded.

    Races ``queue.get()`` against the ``superseded`` future:

      - event ready, not superseded     → yield the event
      - event ready, superseded as well  → hand the event back, stop
      - superseded (no event)            → stop (cancel the pending get safely)
      - ``timeout`` elapsed (if given)   → yield :data:`KEEPALIVE`
      - ``is_disconnected()`` true        → stop before consuming anything

    ``is_disconnected`` (optional) is awaited at the top of each loop so a dead
    HTTP connection ends the stream without consuming an event. The gate
    WebSocket passes ``None`` (its own receive loop detects disconnect).
    """
    while True:
        if is_disconnected is not None and await is_disconnected():
            return
        if superseded.done():
            return
        get_task = asyncio.ensure_future(queue.get())
        done, _pending = await asyncio.wait(
            {get_task, superseded},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if get_task in done:
            evt = get_task.result()
            if superseded.done():
                # Superseded at the same moment we popped an event — hand it
                # back so the now-active connection receives it, then stop.
                queue.put_nowait(evt)
                return
            yield evt
            continue
        # get() did not complete — cancel it (safe: nothing was popped).
        get_task.cancel()
        if superseded.done():
            return
        # Neither event nor supersede within the window → heartbeat.
        yield KEEPALIVE
