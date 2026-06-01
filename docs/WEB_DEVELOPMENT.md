# Web UI — Development phase document

This document captures Phase 7 of the Agentic Commerce build: a
consumer-facing web shopping site that wraps the existing Python agent
stack. Backend code (`agents/`, `tools/`, `gateway/`, `ucp/`, `storage/`)
is **untouched**; the web layer is a thin adapter built on FastAPI +
Jinja + HTMX that maps HTTP requests, SSE streams, and WebSocket
messages onto the existing orchestrator interface.

---

## Quick start

```bash
pip install -r requirements.txt
uvicorn web.app:app --reload
# open http://localhost:8000
```

The app boots even without `ANTHROPIC_API_KEY` — chat features surface
a friendly "chat offline" message; product browsing and click-actions
still work.

Optional env vars:

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Required for chat features |
| `AC_COOKIE_SECRET` | random per boot | Stable cookie signing in prod |
| `DB_PATH` | `./.data/demo.json` | Base path; per-session DBs land in `.data/sessions/` |
| `USER_NAME` | "Friend" | Display name on profile |
| `STRIPE_TEST_KEY` | unset | Live Stripe testmode (else offline tokeniser) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                         BROWSER                           │
│  HTMX-augmented Jinja HTML + Tailwind via CDN             │
│  EventSource (SSE) for chat · WebSocket for gate          │
└─────────────────────────────────────────────────────────┬┘
                                                          │ HTTP / SSE / WS
┌─────────────────────────────────────────────────────────▼┐
│                        FastAPI                            │
│  Routers: products, chat, cart, gate_ws, account          │
│  SessionCookieMiddleware attaches signed cookie           │
│  per-session WebSession holds ToolContext + Orchestrator  │
└─────────────────────────────────────────────────────────┬┘
                                                          │ direct Python
┌─────────────────────────────────────────────────────────▼┐
│            Existing agent stack (UNCHANGED)              │
│  OrchestratorAgent → subagents → tools → gateway → AP2   │
└──────────────────────────────────────────────────────────┘
```

Per-session state lives in `web.session.WebSession`:

- `ctx`: `ToolContext` (db, ap2, gateway, user, session state)
- `orchestrator`: `OrchestratorAgent` with `WebsocketConfirmProvider`
- `mandate_id`: active AP2 mandate created at session boot
- `click_basket`: draft cart assembled via click
- `sse_queue`: per-session event queue drained by `/chat/stream`
- `orchestrator_lock`: serialises overlapping click + chat submissions

Sessions live in a process-local dict; swap for Redis when scaling out.

---

## Click vs chat — hybrid model

Two sources, one destination.

| User does | What runs | Why |
|---|---|---|
| Types in chat | `orchestrator.run(ctx, message)` — full LLM round-trip | Reasoning over the whole conversation |
| Clicks "Add to cart" on a card | Cart router mutates `click_basket`, appends `[via UI click] added X` to `session.conversation` | Instant response; the agent sees the click on its next turn |
| Clicks "Review purchase" | Submits a synthesized chat message ("Please buy everything in my cart now.") → orchestrator → gate flow | Click eventually goes through the same gate the chat path would hit |
| Clicks CONFIRM / Cancel in the gate modal | `WebsocketConfirmProvider` resolves the pending `inbox.get()` with `GateResponse(decision="confirm"|"cancel")` | Same code path the CLI's typed CONFIRM/cancel uses |
| Clicks an item in the picker overlay | Sends `{decision:"question", text:"N"}` → orchestrator's numeric resolver picks item N | Re-uses the existing search sub-flow logic |

The orchestrator never knows or cares which surface invoked it.

---

## API contract

All endpoints honour `Accept: application/json` for a JSON response; HTML
is the default. The seam exists so a future React frontend can use the
same endpoints unchanged.

### Products

| Method | Path | Body / Query | HTML | JSON |
|---|---|---|---|---|
| GET | `/` | — | featured grid | `{products: [...], merchants: [...], mandate, spent_today}` |
| GET | `/search` | `?q=&merchant=` | filtered grid | `{products, query, merchants_searched}` |
| GET | `/product/{merchant}/{id}` | — | product detail | `{product: {...}}` |
| GET | `/healthz` | — | — | `{status: "ok"}` |

### Chat / SSE

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/chat` | `message=...` (form) | `202 {status:"accepted"\|"offline"}` |
| GET | `/chat/stream` | — | SSE stream; events `{type, data}` where type ∈ `user`, `text`, `tool_start`, `tool_end`, `click`, `error`, `done` |

### Cart (click-side draft basket)

| Method | Path | Body | Effect |
|---|---|---|---|
| POST | `/cart/add/{merchant}/{id}` | `quantity=N` | add or bump |
| POST | `/cart/remove/{merchant}/{id}` | — | remove line |
| POST | `/cart/quantity/{merchant}/{id}` | `quantity=N` | set qty (0 removes) |
| POST | `/cart/clear` | — | empty basket |
| GET | `/cart` | — | drawer HTML or JSON summary |

Every state-changing call appends a `[via UI click] …` note to
`session.conversation` so the orchestrator sees the action next turn.

### Gate WebSocket

`GET ws://{host}/gate/ws` — one per browser session, cookie-authenticated.

Server → client events:

```jsonc
{"type": "gate.open", "tier": "explicit", "gate": {
  "merchant_domain": "...",
  "amount": "42.00", "currency": "USD",
  "items": [{"name": "...", "quantity": 1, "line_total": "..."}],
  "risk_flags": [], "confidence_score": 0.9
}}
{"type": "picker.open", "prompt": "...", "options": [{"label": "...", "price": "..."}, ...]}
```

Client → server replies (drives the gate provider's `inbox`):

```jsonc
{"decision": "confirm"}
{"decision": "cancel"}
{"decision": "question", "text": "remove 1"}  // any free-text or "N"
```

Disconnect pushes a synthetic `cancel` onto inbox so the orchestrator
never deadlocks.

### Account, mandate, orders, audit

| Method | Path | Effect |
|---|---|---|
| GET | `/profile` | User info; `payment_method_id` redacted |
| GET | `/mandate` | Caps + live spend |
| POST | `/mandate/revoke` | Revokes the active mandate |
| GET | `/orders` | Order list |
| GET | `/orders/{id}` | Detail + live tracking |
| POST | `/orders/{id}/return` | Calls `initiate_return` tool |
| GET | `/audit` | Recent agent actions |

---

## Component reference

| Template | Purpose | Notes |
|---|---|---|
| `base.html` | Layout + header + persistent sidebar/modal/toasts | All other pages extend this |
| `home.html` | Featured product grid | Featured cap-aware via mandate |
| `search.html` | Search results | Caches results into `session.last_discovered_products` |
| `product_detail.html` | Single product view + "Add to cart" | |
| `_product_card.html` | Reusable card | Includes HTMX add form |
| `_chat_sidebar.html` | Right-side chat panel | Reads `/chat/stream` via EventSource |
| `_cart_drawer.html` | Cart line items + qty controls | Swapped via HTMX `outerHTML` |
| `_gate_modal.html` | Full-screen confirmation overlay | Listens to `/gate/ws` |
| `_picker_overlay.html` | Numbered-list picker | Sends `{decision:"question", text:"N"}` |
| `_toast.html` | Notification stack | Exposes `window.__toast(msg, kind)` |
| `profile.html`, `mandate.html`, `orders.html`, `order_detail.html`, `audit.html` | Account pages | — |

---

## Click-action catalogue

| UI element | Server handler | Orchestrator path |
|---|---|---|
| "Add to cart" button on card | `POST /cart/add/{m}/{id}` | `click_basket` mutation + synth note |
| "Remove" (✕) in drawer | `POST /cart/remove/{m}/{id}` | basket mutation + synth note |
| Quantity input + ↻ in drawer | `POST /cart/quantity/{m}/{id}` | basket mutation + synth note |
| "Clear" button in drawer | `POST /cart/clear` | basket reset + synth note |
| "Review purchase" button | Synth chat message → `POST /chat` | full orchestrator → `call_purchase_agent` → gate |
| Gate modal CONFIRM | WS `{decision:"confirm"}` | provider resolves `explicit_confirm` |
| Gate modal Cancel | WS `{decision:"cancel"}` | provider resolves `explicit_confirm` |
| Gate modal inline question | WS `{decision:"question", text}` | orchestrator's gate Q&A loop |
| Picker overlay item N | WS `{decision:"question", text:"N"}` | numeric resolver in search sub-flow |
| Revoke mandate | `POST /mandate/revoke` | `ap2.revoke_mandate(...)` |
| Initiate return | `POST /orders/{id}/return` | `initiate_return` tool |

---

## Extending the UI

### Adding a new page

1. Add a route under `web/routers/<topic>.py`. Use the established
   pattern: `async def handler(request: Request, sess: WebSession = Depends(get_or_create_session))`.
2. Honour `_wants_json(request)` for the dual-format seam.
3. Register the router in `web/app.py:create_app`.
4. Add a Jinja template under `web/templates/<page>.html` that extends
   `base.html` and a header link in `base.html`.
5. Add a test file `tests/test_web_phase7X.py` using `TestClient`.

### Adding a new click action

1. Add an endpoint that calls into the existing tool layer (do not
   re-implement business logic in the router).
2. Call `append_click_note(ctx, note)` so the agent sees the action.
3. Optionally push an SSE event onto `sess.sse_queue` to surface the
   action as a toast in real time.
4. Add an HTMX form to the relevant template with `hx-post` + `hx-target`.

### Adding a new gate event

1. Push an event onto `gate_provider.outbox` with a stable `type` (e.g.
   `picker.open`, `toast.show`). The WS pump forwards it to the browser.
2. Handle the new type in the relevant client-side script under
   `web/templates/_*.html`.

---

## Security model

| Risk | Mitigation |
|---|---|
| Session hijacking | Cookie signed with `itsdangerous`, HttpOnly, SameSite=Strict |
| Cap evasion via fast clicking | Every action re-validates the mandate server-side (AP2) |
| Bypass of gate via click | Click "Review purchase" still flows through the orchestrator's gate |
| XSS via product names | Jinja autoescapes; client-side rendering of WS payloads escapes manually |
| Gate disclosure to wrong session | WS lookup checks the signed cookie; cross-session queues never share state |
| Stale gate after reload | Provider has 5-minute timeout; disconnect pushes synthetic cancel |
| Disclosure of `payment_method_id` | Never leaves `PaymentGateway`; profile view uses `agent_safe_view` |
| Concurrent click + chat interleave | Per-session `orchestrator_lock` |

---

## Test plan

Web tests live in `tests/test_web_phase7[a-f].py` and use FastAPI's
`TestClient`. Per the plan:

- 7a Foundation (21 tests): home/search/product detail, dual format, sessions, healthz
- 7b Chat + SSE (10 tests): POST/chat validation, offline path, user echo, route registration, session isolation, sidebar rendering, callback adapter
- 7c Cart (14 tests): add/remove/quantity/clear/view, idempotent add, synth notes, isolation
- 7d Gate (9 tests): provider Protocol, round-trip, WS bridge, disconnect handling, modal rendering
- 7e Account (14 tests): profile (with payment redaction), mandate (caps + revoke), orders (seeded + return), audit
- Total: 68 new web tests, full suite at ~493/493

Run: `python3 -m pytest -p no:cacheprovider -q`

---

## Out of scope

- Real auth (single-user MVP)
- Real merchants (Shopify stub adapter)
- Real Stripe live mode by default
- Native mobile apps
- Multi-merchant atomic basket
- Analytics, observability
- CDN / production deployment
