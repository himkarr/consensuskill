# ConsensusKill — System Details

Everything about how this repo works, what it is built with, and how the pieces fit together.
The original assignment brief lives in [`SPEC.md`](SPEC.md).

> **Game:** *ConsensusKill* — a real-time multiplayer "minority wins" party game
> (inspired by *Liar Game*). Every round you vote on a dilemma. **The minority side
> survives, the majority side loses a life.** So the rational move is to blend in with
> the crowd — which is exactly how you lose.

---

## 1. Tech stack (what is used)

| Layer | Choice | Version | Why |
|---|---|---|---|
| Language (backend) | Python | 3.11.15 (venv) | `uv`-managed `.venv`; ruff targets py311 |
| Web framework | FastAPI + Uvicorn | 0.115.6 / 0.34.0 | native async WebSockets, `lifespan` startup/shutdown |
| Validation | Pydantic | 2.10.4 | one schema set shared by gateway, engine, tests |
| Redis client | `redis-py` (asyncio) | 5.2.1 | Streams + Pub/Sub + SET NX from one client |
| HTTP (questions) | httpx | 0.28.1 | Supabase REST fetch with timeout |
| Lint | ruff | 0.8.4 | rules `E,F,I,UP,B`, line length 100 |
| Tests | pytest + pytest-asyncio + pytest-timeout | 8.3.4 / 0.24.0 / 2.3.1 | `asyncio_mode=auto`, per-test timeout 60s |
| Test double | fakeredis | 2.26.2 | full stack runs in-process with no real Redis |
| Frontend | React + TypeScript | 19.3 / 7.0.2 | SPA, strict TS |
| Bundler / dev server | Vite | 8.3.1 | build + `preview` with `/ws` proxy |
| Frontend tests | Vitest | 5.0.2 | pure-logic unit tests |
| QR code | `qrcode.react` | ^4.2 | projector screen join QR |
| Browser E2E | Playwright (chromium) | ^1.63 | `scripts/ui_smoke.mjs` |
| Load / smoke client | `websockets` | 17.1 | `scripts/bot.py` bot swarm |
| Message bus | Redis 8.10.2 | — | single ingest Stream + Pub/Sub fan-out |
| Persistence (optional) | Supabase Postgres | — | question bank; falls back to JSON |
| Node / npm | Node | 24.18.0 / 11.16.0 | frontend toolchain |

Config: [`pyproject.toml`](pyproject.toml) (pytest + ruff), [`requirements.txt`](requirements.txt),
[`requirements-dev.txt`](requirements-dev.txt), [`frontend/package.json`](frontend/package.json),
[`frontend/tsconfig.json`](frontend/tsconfig.json), [`frontend/vite.config.ts`](frontend/vite.config.ts).

---

## 2. Repository structure

```
consensuskill/
├── SPEC.md                  # original assignment brief
├── details.md               # this file
├── README.md                # stub (Phase 6 target)
├── pyproject.toml           # pytest + ruff config
├── requirements.txt         # runtime deps
├── requirements-dev.txt     # test/lint deps
├── .env                     # Supabase DSN (gitignored)
├── data/
│   └── questions.json       # 40-question bank {id,text,option_a,option_b}
│
├── shared/                  # code BOTH services import (no I/O of its own)
│   ├── schemas.py           # Phase/Choice/Player/RoomState + wire message builders
│   ├── keys.py              # every Redis key/channel name (one source of truth)
│   ├── constants.py         # room codes, player limits, phase durations, chat limits
│   ├── bus.py               # xadd/publish/load/save/delete helpers
│   ├── errors.py            # GameError(code, message)
│   └── questions.py         # Supabase → JSON-bank loader with cache
│
├── engine/                  # the authoritative round engine (:8001)
│   ├── rules.py             # PURE game rules — no Redis, no sockets (540 lines)
│   ├── store.py             # per-room SET NX lock + vote audit stream
│   ├── timers.py            # phase-timer loop + empty-room sweeper
│   └── engine.py            # RoundEngine: consumer, action handlers, FastAPI app
│
├── gateway/                 # stateless WebSocket front door (:8000)
│   └── app/
│       ├── main.py          # create_app(): /health, /admin/kill, /ws, CORS
│       ├── ws.py            # socket loop, validation, Pub/Sub relay
│       └── models.py        # ConnectionRegistry (the ONLY gateway state)
│
├── frontend/                # React SPA
│   ├── index.html
│   ├── vite.config.ts       # proxies /ws + /health → gateway
│   ├── scripts/
│   │   ├── ui_smoke.mjs     # Playwright browser test (21 checks)
│   │   └── ui_shots.mjs     # screenshots every screen → frontend/shots/
│   └── src/
│       ├── main.tsx         # React root
│       ├── App.tsx          # screen router: boot / home / game + notice toast
│       ├── Home.tsx         # create-or-join form, ?code= deep link
│       ├── protocol.ts      # TS mirror of the wire protocol
│       ├── logic.ts         # pure helpers (clock offset, countdown, sorting…)
│       ├── logic.test.ts    # 14 Vitest tests
│       ├── useGame.ts       # THE WebSocket session hook (reconnect, token, queue)
│       ├── styles.css       # Material 3 (dark, matte) design system
│       └── game/
│           ├── GameShell.tsx # all phase views, projector/player toggle, chat, grid
│           └── hooks.ts      # useTicker, useAutoScroll
│
├── scripts/
│   ├── dev.sh               # start/stop/status the whole local stack
│   └── bot.py               # bot swarm: plays full matches over real WebSockets
│
├── tests/                   # 64 pytest tests
│   ├── conftest.py          # make_room / vote_room fixtures
│   ├── test_rules.py        # 23 — pure rules
│   ├── test_minority.py     # 13 — compute_minority / check_winner
│   ├── test_engine.py       # 11 — engine over fakeredis
│   └── test_gateway.py      # 17 — full gateway+engine over real WS
│
└── .run/                    # created by scripts/dev.sh (pids + logs, gitignored)
```

---

## 3. Architecture

```
                    ┌────────────────────────────────────────────────┐
   browsers ──WS──► │  gateway-1 … gateway-N     (stateless: sockets │
   (projector +      │  only; no game state)       only)            │
    phones)          └───────┬──────────────────────┬───────────────┘
                            │ XADD ck:ingest        │ PSCRIBE
                            │ (client actions)      │  ck:reply:*          ──► only the
                            ▼                       │  ck:room:*:events        one socket /
                     ┌──────────────┐               │                          the whole room
                     │    Redis     │◄──────────────┴───────────────┐
                     │  Streams +   │  SET ck:room:CODE:state       │
                     │  Pub/Sub +   │  (authoritative JSON doc,     │
                     │  locks       │   single writer = engine)     │
                     └──────┬───────┘                               │
                            │ XREADGROUP ck:engine                  │
                            ▼                                       │
                   ┌──────────────────┐   applies rules + timers    │
                   │  round-engine    │─────────────────────────────┘
                   │  (authoritative  │   publishes state/chat back to the room
                   │   state machine) │
                   └──────────────────┘
```

**The one rule that keeps it consistent:** a room's entire state is *one JSON document*
(`ck:room:CODE:state`) and **only the engine ever writes it**, always under a
per-room `SET NX PX` lock. Gateways hold sockets, never state.

### Data flow for one player action

```
phone clicks "vote A"
  → gateway ws.dispatch()        validates with Pydantic, adds conn_id/player_id
  → shared.bus.send_action()     XADD ck:ingest {payload: {...}}
  → engine.run_consumer()        XREADGROUP (consumer group ck:engine)
  → engine.handle_action()       dispatch by kind
  → engine._mutate()             LOCK → load RoomState → pure rules.apply → save → UNLOCK
  → shared.bus.reply()           PUBLISH ck:reply:conn_id     (ack / error / joined)
  → shared.bus.broadcast()       PUBLISH ck:room:CODE:events  (new state)
  → gateway relay_loop()         pattern-subscribed once at startup
  → gateway route_event()        attaches per-socket `you` block via build_you()
  → phone receives `state`       renders the vote UI
```

Chat, phase transitions and kicks ride the same two channels.

### Why this scales

* Gateways are **cattle**: kill one and its clients reconnect to any replica with their
  player token (verified by the fault-tolerance run in Phase 2).
* Redis Pub/Sub does the **cross-node fan-out**: no gateway needs to know which other
  gateways hold sockets for a room.
* The engine's **consumer group** serializes every mutation of every room, so there is
  exactly one writer and no split-brain during voting.
* Timers are server-stamped (`timer_ends_at`), clients only *display* a countdown —
  they can never advance the game.

---

## 4. Redis data model (see `shared/keys.py`)

| Key / channel | Type | Purpose |
|---|---|---|
| `ck:ingest` | Stream | every client action, in order (group `ck:engine`) |
| `ck:room:{code}:state` | String (JSON) | **the** authoritative room document |
| `ck:rooms` | Set | live room codes (drives timers + sweeps) |
| `ck:token:{token}` | String | player token → room code (reconnect lookup) |
| `ck:room:{code}:votes` | Stream | durable vote audit log (maxlen 1000) |
| `ck:room:{code}:lock` | String (`SET NX PX`) | per-room mutation lock (5s TTL, bounded wait) |
| `ck:lock:engine` | String | one timer loop per instance |
| `ck:room:{code}:events` | Pub/Sub | state + chat fan-out to **all** gateways |
| `ck:reply:{conn_id}` | Pub/Sub | answer to exactly **one** socket |

Gateways subscribe **once** to the patterns `ck:reply:*` and `ck:room:*:events` at
startup, so no gateway ever re-subscribes while serving traffic (no races, no missed
first message).

---

## 5. Backend in detail

### 5.1 `shared/schemas.py` — the wire contract

* `Phase` enum: `LOBBY → QUESTION → DISCUSSION → VOTE → REVEAL → APPLY → GAME_OVER`.
* `RoomState`: the single document — players, `conns` (conn_id→player_id live-socket
  map), votes, counts, result, timer, chat log, `version` counter.
* `ClientMessage`: discriminated union on `type` (`create_room`, `join`, `reconnect`,
  `chat`, `vote`, `host_start`, `host_kick`, `kill_instance`, `sync`, `leave`) —
  validated by the gateway with `TypeAdapter(ClientMessage)`.
* Server→client builders: `instance_info`, `joined`, `state`, `chat`, `error`, `ack`.
* **`build_you()`** attaches the per-connection private block (your lives, your vote —
  and *only* after the reveal). The engine uses it for direct `sync` replies, gateways
  for broadcasts.

### 5.2 `engine/rules.py` — pure rules (no I/O)

All testable without Redis:

| Function | Job |
|---|---|
| `create_room` / `join_room` | 6-char codes from a no-confusion alphabet (`ABCDEFGHJKMNPQRSTUVWXYZ23456789`), nickname validation, host creation |
| `start_game` | needs ≥ 3 players, resets lives to 2, picks question |
| `pick_question` | walks the bank without repeats, refills when exhausted |
| `submit_vote` | phase must be `VOTE`, not past `timer_ends_at`, must be eligible, **first vote only** |
| **`compute_minority(votes)`** | resolves the split (see table below) |
| `apply_lives` / `eliminate_players` / `check_winner` | life loss, spectators, win detection |
| `advance_on_timeout` | the state machine step (pure given `now`) |
| `broadcast_state` | builds the public payload; tallies hidden until reveal |
| `chat_allowed` / `append_chat` | 1 msg/s per player, sanitize + truncate, last 50 kept |

**`compute_minority` decision table**

| Situation | `status` | Effect |
|---|---|---|
| A and B counts differ | `normal` | majority side (and abstainers) lose 1 life |
| counts equal (`2–2`, `0–0` with abstainers) | `tie` | nobody loses a life, replay |
| every eligible voter picked the same side | `unanimous` | round voided (no minority exists) |
| nobody voted at all | `unanimous` | round voided |
| one side zero **and** someone abstained | `normal` | abstainers cannot void a one-sided vote — they lose the life |
| player never voted | counted as loser | AFK players are majority-side losses |

Winning: match ends when **one** player is left alive, or when ≤ 2 remain after a
round that changed nothing (a 1v1 can never resolve → treated as a finish). Everyone
dead → `GAME_OVER` with `winners: []` (draw).

### 5.3 `engine/engine.py` — the round engine

* **Consumer** — `XREADGROUP` on `ck:ingest` (batch 20, block 1s), `XACK` per message,
  `XAUTOCLAIM` on startup to reclaim messages a crashed engine never acked.
  An empty read sleeps `ENGINE_EMPTY_POLL_DELAY` (0.05s) so a non-blocking backend
  (fakeredis) cannot busy-loop the event loop.
* **`_mutate(code, fn)`** — the *only* write path: acquire lock → `load_room` → apply a
  pure rule function → `save_room` → release lock. Raises `GameError` for client-visible
  failures, which become `{type:"error"}` replies.
* **Action handlers** — `_on_create_room` (SET NX, up to 8 code attempts), `_on_join`
  (idempotent per conn), `_on_reconnect` (token → room), `_on_chat`, `_on_vote`
  (audits accepted votes to `ck:room:{code}:votes`), `_on_host_start`, `_on_host_kick`,
  `_on_disconnect`, `_on_leave`, `_on_sync`.
* **Background tasks** — ingest consumer, phase-timer loop, empty-room sweeper
  (all started in FastAPI `lifespan`, cancelled on shutdown).
* **HTTP** — `GET /health` returns `instance_id`, `processed`, `questions`, `redis` state.

### 5.4 `engine/timers.py` — phase transitions

Every `ENGINE_TICK_SECONDS` (0.2s) the engine walks `ck:rooms`, and for each room whose
`timer_ends_at` has passed: lock → `advance_on_timeout` → save → broadcast the new state.
Because the lock is `SET NX`, two engine replicas can never double-advance a round.
`ENGINE_SWEEP_SECONDS` (60s) deletes rooms that lost every player.

Phase durations (`shared/constants.py`): QUESTION 5s · DISCUSSION 20s · VOTE 10s ·
REVEAL 8s · APPLY 5s → **≈48s per round**.

### 5.5 `gateway/` — the stateless front door

* `ws.handle_connection` — accepts, mints `conn_id`, sends `instance_info`, then loops
  on `receive_text()` → Pydantic validate → `dispatch()`. On close it emits a
  `disconnect` action (if it ever joined).
* `ws.dispatch` — copies client fields into an action dict, **adding `conn_id` and the
  server-trusted `player_id`** (the client can't spoof who it is). `kill_instance` is
  handled inline (host only) as `os._exit(1)` for the crash demo.
* `ws.relay_loop` — one `PSUBSCRIBE` reader; `route_event` looks up the target socket
  (`ck:reply:conn_id`) or all sockets in the room (`ck:room:CODE:events`) and injects
  the `you` block per connection.
* `models.ConnectionRegistry` — the only memory a gateway keeps: conn_id → socket,
  plus room → conn_ids. Nothing else, so any client can land on any replica.
* `main.create_app` — CORS, `/`, `/health`, `POST /admin/kill` (optional `ADMIN_TOKEN`
  header), `/ws`, and `embedded_engine=True` for tests / single-process dev.

### Fault tolerance (verified in Phase 2)

1. `POST /admin/kill` (host-only, or `kill_instance` from the lobby UI) → gateway dies.
2. Browsers/bots detect the closed socket, back off (500ms→8s exponential), reconnect.
3. They replay `{type:"reconnect", token}`; Redis's token index maps it to the room.
4. Game continues from the same round — lives, votes and chat intact.

---

## 6. Wire protocol (summary)

**Client → server** (JSON over WS):

```jsonc
{"type":"create_room","nickname":"Fox"}
{"type":"join","code":"ABC234","nickname":"Fox"}
{"type":"reconnect","token":"<from localStorage>"}
{"type":"chat","text":"trust me, pick A"}
{"type":"vote","choice":"A"}
{"type":"host_start"}
{"type":"host_kick","player_id":"..."}
{"type":"kill_instance"}   // host only, crashes that gateway (demo)
{"type":"sync"}
{"type":"leave"}
```

**Server → client:**

| type | meaning |
|---|---|
| `instance_info` | gateway instance id + `server_time` (clock-sync source) |
| `joined` | conn_id, player_id, token, room_code, is_host — **token must be stored** |
| `state` | full room snapshot: phase, round, question, players, timer, counts (post-reveal), `you` |
| `chat` | one chat message (also merged into `state.chat_log`) |
| `error` | `{code, message}` — stable `code` the UI switches on |
| `ack` | request/result pair (duplicate votes are acked `ok:false`, not an error) |

Vote tallies and individual choices are **omitted from `state` until the reveal**;
your own choice appears in `you.choice` only after it.

---

## 7. Frontend in detail

| File | Role |
|---|---|
| `useGame.ts` | The session. Connects to `ws(s)://host/ws`, sends `reconnect` with the stored token on every open, tracks clock offset from `instance_info`/`state`, queues the first message while still connecting, auto-reconnects with exponential backoff, clears the token only on server-side rejection (`invalid_token`/`kicked`/`room_not_found`). Exposes `status`, `session`, `state`, `chats`, `notice`, `offsetRef`, `actions`. |
| `App.tsx` | Chooses screen: `session && state` → `GameShell`; a 1.5s cold-boot "Reconnecting…" splash when a nickname is stored; otherwise `Home`. Renders the notice toast. |
| `Home.tsx` | Create/join form with a mode switch, nickname (≤16) + 6-char code validation, `?code=` deep-link prefill. |
| `game/GameShell.tsx` | Top bar (room code, phase pill, countdown, instance badge, view toggle, leave), then phase views: `LobbyView` (QR + link, start gating, kick, crash button), `QuestionView`, `VoteView` (locks after first vote, spectator mode), `RevealView` (A/B bars + casualties), `GameOverView`, `PlayerGrid`, `ChatPanel`. |
| `logic.ts` | Pure: `clockOffset`, `remainingWithOffset`, `formatCountdown`, `PHASE_LABEL`, `joinLink`, `codeFromUrl`, `displayOrder`, `resultHeadline`, `livesEmoji`, `canStart`. All unit-tested. |
| `protocol.ts` | TypeScript mirror of `shared/schemas.py`. |
| `game/hooks.ts` | `useTicker` (re-render for countdowns), `useAutoScroll` (chat). |

**Two views in one app:** hosts default to **projector** (big text, 220px QR),
everyone else to **player** (phone layout); the toggle button flips either at any time.

**Look & feel (Material 3, dark + matte):** `styles.css` is a self-contained token
system — M3 tonal surfaces (`--md-surface-container-*`), shape scale (4→20px + pill),
low-alpha elevation, emphasized easing — with **Roboto Variable** (UI) and
**Roboto Mono Variable** (codes, timers) bundled locally via `@fontsource-variable`.
Extras: a 3% SVG-noise grain over the whole page so flat surfaces read as powder-coated
metal, M3 filled/outlined pill buttons with state layers plus a global pointer-delegated
**ripple** (`src/ripple.ts`), outlined text fields with a mint focus ring, a segmented
create/join switch, copyable room code and big code (`Copied` feedback chip), a light QR
tile, phase-coloured phase pills, A/B-tinted question and vote cards, chat bubbles
(yours right-aligned, primary-tinted), and a snackbar-style notice. A/B identity colours
are `--a` (blue) and `--b` (rose) everywhere: option cards, vote buttons, bars, avatars.
Responsive at ≤860px and honours `prefers-reduced-motion`.
`frontend/scripts/ui_shots.mjs` re-shoots all 11 screens (desktop/phone, both views)
into `frontend/shots/` for review after any styling change.

**Clock skew:** the server stamps `server_time` on messages; the client keeps
`offset = server_time*1000 - Date.now()` and computes remaining time against the
*server's* clock, so local clock drift can't break countdowns.

**Storage:** `localStorage["ck.token"]` and `["ck.nickname"]` — that is what makes a
page reload rejoin the live game.

---

## 8. Tests

| Suite | Count | Covers |
|---|---|---|
| `tests/test_rules.py` | 23 | room codes, join/start/kick rules, vote validation, full round cycles, broadcast visibility, reconnect, chat sanitizing/rate-limit |
| `tests/test_minority.py` | 13 | `compute_minority` + `check_winner`: splits, ties, unanimous, no-vote, abstainers, everyone dying |
| `tests/test_engine.py` | 11 | health, create/join over the bus, full round, duplicate/late/spoofed votes, reconnect, chat limits, kick, non-host start |
| `tests/test_gateway.py` | 17 | real WS against gateway+embedded engine+fakeredis: health, ids/tokens, malformed JSON, `you` injection, host gating, kick, phases, rate limits, reconnect, kill guard |
| `frontend/src/logic.test.ts` | 14 | clock offset, countdown clamping/formatting, deep link, display order, headlines, start gating, phase labels |
| **Total** | **64 + 14** | |

Beyond pytest there are two live checks:

* **`scripts/bot.py`** — a bot swarm playing full matches over real WebSockets
  (used to prove the Phase 2 backend, incl. killing a gateway mid-game).
* **`frontend/scripts/ui_smoke.mjs`** — 21 Playwright checks driving the real browser UI:
  home render → create room → QR/code → deep-link join ×2 → start → question →
  discussion chat → vote locking → reveal bars → **reload resumes via token** →
  zero page errors.

`tests/test_gateway.py` reads messages until the *expected* state appears rather than
assuming the next frame, because a joining host's socket may still hold queued `state`
broadcasts from earlier joins.

---

## 9. Running it

### One command

```bash
scripts/dev.sh up      # redis + engine :8001 + gateway :8000 + built frontend :4173
scripts/dev.sh status
scripts/dev.sh logs
scripts/dev.sh down
```

`scripts/dev.sh` is the fix for the two mistakes that break a manual start:

1. **Use `.venv/bin/uvicorn`.** A bare `uvicorn` resolves to the system Python
   (3.12 here), which has no `fastapi`/`redis` → `ModuleNotFoundError: No module named 'redis'`.
2. **`redis-server` must be on PATH.** It is not installed via apt in this
   environment; the source-built binary (Redis 8.10.2) lives in `~/.local/bin`.

### Manual (what the script does)

```bash
# terminal 1
redis-server                     # or: ~/.local/bin/redis-server
# terminal 2
.venv/bin/uvicorn engine.engine:app  --port 8001
# terminal 3
.venv/bin/uvicorn gateway.app.main:app --port 8000
# terminal 4
cd frontend && npm run build && npm run preview     # :4173, proxies /ws → :8000
```

Then open <http://127.0.0.1:4173>.

### Quality gates

```bash
.venv/bin/ruff check .                                  # lint (backend)
.venv/bin/pytest -q -o faulthandler_timeout=60          # 64 tests
cd frontend && npm run typecheck && npm test            # tsc + 14 vitest
cd frontend && npm run build                            # tsc && vite build
cd frontend && npm run smoke                            # 21 browser checks (stack must be up)
```

### Load / resilience

```bash
python scripts/bot.py --url ws://127.0.0.1:8000/ws --players 5 --rooms 2 --strategy trap --timeout 135
python scripts/bot.py --url ws://127.0.0.1:4173/ws --players 5   # through the Vite proxy
```

Bot strategy options: `trap` (default — plays to win), `random`, `majority` (always
follows the crowd). Exit code 0 / `RESULT: PASS` means a match reached `GAME_OVER`.

---

## 10. Configuration (environment variables)

| Variable | Default | Read by |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | gateway, engine |
| `INSTANCE_ID` | `gateway-…` / `engine-…` | both (shown in UI + health) |
| `ALLOWED_ORIGINS` | `*` | gateway CORS |
| `ADMIN_TOKEN` | unset | guards `POST /admin/kill` |
| `ENGINE_BLOCK_MS` | `1000` | engine `XREADGROUP BLOCK` |
| `ENGINE_BATCH` | `20` | engine consumer batch |
| `ENGINE_EMPTY_POLL_DELAY` | `0.05` | sleep on empty stream read |
| `ENGINE_TICK_SECONDS` | `0.2` | phase-timer loop period |
| `ENGINE_SWEEP_SECONDS` | `60` | empty-room sweeper |
| `SUPABASE_URL` / `SUPABASE_KEY` | unset | question bank via REST (else JSON bank) |
| `VITE_GATEWAY_URL` | `http://127.0.0.1:8000` | Vite proxy target |
| `VITE_WS_URL` | same-origin `/ws` | explicit WS override in the browser |
| `SMOKE_URL` | `http://127.0.0.1:4173` | base URL for the Playwright smoke test |

`.env` holds the Supabase DSN and is gitignored; nothing loads it automatically
(the Supabase question loader reads `SUPABASE_URL`/`SUPABASE_KEY` from the process env).

---

## 11. Design decisions & known limitations

**Decisions**

* **Single JSON room document + single writer** instead of a relational model: phase
  transitions are one atomic `SET`, and the lock makes multi-replica engines safe.
* **Pure rules module** (`engine/rules.py`) → the entire game logic is unit-testable
  with zero services.
* **Two publish channels** (per-connection reply vs per-room events) → request/reply and
  broadcast never interfere, and gateways need only one pattern subscription.
* **Server-stamped timers** → clients cannot cheat by manipulating time.
* **First vote wins; duplicates are acked, not errored** → double-clicks are harmless.
* **Abstainers cannot void a one-sided vote** (documented rule edge case).
* **Vite preview proxies `/ws`** so the frontend and gateway share an origin in dev —
  the production path will be nginx doing the same job.

**Limitations / not yet done (phases 4–6)**

* No Docker/compose/nginx load balancer yet — the stack runs as local processes.
* No CI/CD workflow yet.
* `README.md` is still a stub; no deployment.
* Postgres/Supabase is only used for the optional question bank; **match outcomes /
  leaderboard are not persisted** (spec item still open).
* No sweeper for stale `conns` entries after an abrupt gateway crash; a player's
  `connected` flag refreshes on their next reconnect/action.
* Rooms with no host leave (players idling in a finished game) persist until the
  sweeper sees an empty roster; `leave` deletes an empty room immediately.
* Redis has persistence disabled in the local dev script (`--save ''`) — state is
  in-memory for the demo.

---

## 12. Status

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Core rules + unit tests | ✅ 36 tests |
| 2 | Distributed backend (gateway, engine, Redis) | ✅ 64 tests, bot swarm + kill/reconnect PASS |
| 3 | Frontend (React/Vite/TS, projector + player views) | ✅ typecheck, 14 vitest, build, 21/21 smoke, 11-screen M3 matte redesign |
| 4 | Docker & orchestration (nginx-lb, gateway×2, engine, redis) | ⬜ next |
| 5 | CI/CD (GitHub Actions → ghcr → VM) | ⬜ |
| 6 | README + architecture diagram + talking points | ⬜ |
