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
├── README.md                # assessment-ready README (rules, diagram, script, Q&A)
├── pyproject.toml           # pytest + ruff config
├── requirements.txt         # runtime deps
├── requirements-dev.txt     # test/lint deps
├── .env                     # Supabase DSN (gitignored)
├── .env.example             # every env var, documented
├── Dockerfile               # backend image (gateway + engine; one build)
├── docker-compose.yml       # nginx edge + gateway×2 + engine + redis
├── frontend/Dockerfile      # node build stage → nginx image (SPA + LB)
├── .dockerignore
├── render.yaml              # Render blueprint (free tier: 1 web service)
├── vercel.json              # Vercel static deploy of the SPA
├── .github/workflows/
│   ├── ci.yml               # 4 jobs: backend, frontend, browser smoke, compose e2e
│   └── deploy.yml           # CD: gate -> ghcr.io push -> VM (SSH) / Render (hook)
├── data/
│   └── questions.json       # 40-question bank {id,text,option_a,option_b}
│
├── deploy/
│   ├── render-entry.sh      # Render start: embedded engine + hosted-Redis tuning
│   └── vm-deploy.sh         # VM pull + compose up (run by deploy.yml over SSH)
├── docker/
│   └── nginx/default.conf   # SPA + /ws proxy, upstream = gateway replicas
├── supabase/
│   └── questions.sql        # optional question-bank table + RLS read policy
│
├── shared/                  # code BOTH services import (no I/O of its own)
│   ├── schemas.py           # Phase/Choice/Player/RoomState + wire message builders
│   ├── keys.py              # every Redis key/channel name (one source of truth)
│   ├── constants.py         # room codes, player limits, phase durations, chat limits
│   ├── bus.py               # xadd (maxlen) + wake publish, load/save/delete helpers
│   ├── errors.py            # GameError(code, message)
│   └── questions.py         # Supabase → JSON-bank loader with cache
│
├── engine/                  # the authoritative round engine (:8001)
│   ├── rules.py             # PURE game rules — no Redis, no sockets (540 lines)
│   ├── store.py             # per-room SET NX lock + vote audit stream
│   ├── timers.py            # deadline-cached timer loop + empty-room sweeper
│   └── engine.py            # RoundEngine: consumer+wake, handlers, FastAPI app
│
├── gateway/                 # stateless WebSocket front door (:8000)
│   └── app/
│       ├── main.py          # create_app(): /health, /admin/kill, /ws, CORS, embed
│       ├── ws.py            # socket loop, validation, Pub/Sub relay
│       └── models.py        # ConnectionRegistry (the ONLY gateway state)
│
├── frontend/                # React SPA
│   ├── Dockerfile           # npm ci + tsc + vite build → nginx:1.27-alpine
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
│   ├── bot.py               # bot swarm: plays full matches over real WebSockets
│   ├── seed_supabase.py     # load data/questions.json into Supabase
│   └── smoke_upstash.py     # verify a hosted Redis supports every command we use
│
├── tests/                   # 76 pytest tests
│   ├── conftest.py          # make_room / vote_room fixtures
│   ├── test_rules.py        # 23 — pure rules
│   ├── test_minority.py     # 13 — compute_minority / check_winner
│   ├── test_engine.py       # 11 — engine over fakeredis
│   ├── test_gateway.py      # 17 — full gateway+engine over real WS
│   └── test_wake_and_timers.py # 12 — wake channel + deadline-cached loop
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

* **Consumer** — `XREADGROUP` on `ck:ingest` (batch 20). `ENGINE_BLOCK_MS` decides the
  shape: `1000` blocks up to 1s (local Redis); `0`/negative → **no `BLOCK` argument at
  all** (`parse_block_ms`, because Redis reads `BLOCK 0` as "block forever" and Upstash
  forbids blocking reads). On an empty read the consumer sleeps on an `asyncio.Event`
  until the gateway's wake ping arrives, with `ENGINE_EMPTY_POLL_DELAY` as the
  safety-net poll (30s on the free tier, 0.05s in tests where fakeredis ignores BLOCK).
* **Wake subscriber** — `run_wake()` `SUBSCRIBE`s to `ck:wake` (the channel
  `bus.send_action` pings on every action) and sets that event. Best-effort: if Pub/Sub
  fails the task logs a warning and the safety-net poll takes over.
* **`_mutate(code, fn)`** — the *only* write path: acquire lock → `load_room` → apply a
  pure rule function → `save_room` → release lock. Raises `GameError` for client-visible
  failures, which become `{type:"error"}` replies. Every save also feeds the timer
  loop's in-memory deadline index (`_observe_deadline`).
* **Action handlers** — `_on_create_room` (SET NX, up to 8 code attempts), `_on_join`
  (idempotent per conn), `_on_reconnect` (token → room), `_on_chat`, `_on_vote`
  (audits accepted votes to `ck:room:{code}:votes`), `_on_host_start`, `_on_host_kick`,
  `_on_disconnect`, `_on_leave` (deleting the last room also forgets its deadline),
  `_on_sync`.
* **Background tasks** — ingest consumer, wake subscriber, phase-timer loop,
  empty-room sweeper (all started in FastAPI `lifespan`, cancelled on shutdown).
* **HTTP** — `GET /health` returns `instance_id`, `processed`, `questions`, `redis` state.
* **Single-process mode** — `EMBEDDED_ENGINE=1` (or `create_app(embedded_engine=True)`)
  runs all of the above inside the gateway process: used by the test suite and by the
  free-tier deploy (one Render web service instead of two).

### 5.4 `engine/timers.py` — phase transitions

`advance_room()` locks one room, and if `timer_ends_at` has passed applies
`advance_on_timeout` → save → broadcast. It returns an `AdvanceOutcome`
(`ADVANCED`/`UNCHANGED`/`GONE`/`BUSY`) plus the post-attempt document, so the caller
refreshes its cache without a second read. Because the lock is `SET NX`, two engine
replicas can never double-advance a round (`BUSY` just backs off 0.5s).

The **live loop** (`run_timer_loop`) keeps an in-memory `DeadlineIndex`
(`room code → timer_ends_at`) that the engine updates on every save. It sleeps until
the next cached deadline (or until the engine's wake event fires) and only touches
Redis on real transitions, plus one `rescan` every `ENGINE_RESCAN_SECONDS` (30s; 60s
while no rooms exist) to reconcile with Redis. A 0.2s heartbeat is used only to retry a
`BUSY` lock. This is what keeps a hosted Redis inside its monthly command budget —
see §12. `ENGINE_SWEEP_SECONDS` (300s) deletes rooms that lost every player;
`sweep`/`tick` stay scan-based for tests, which drive them with a synthetic clock.

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
  header), `/ws`, and engine embedding (`create_app(embedded_engine=True)` or the
  `EMBEDDED_ENGINE=1` env) for tests and single-process deploys.

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
| `tests/test_wake_and_timers.py` | 12 | wake ping per action, consumer processes an action with polling disabled, timer loop issues **zero** Redis commands while idle, deadline-driven advance, engine deadline cache follows create/leave/start, `advance_room` outcomes (ADVANCED/UNCHANGED/GONE/BUSY), `DeadlineIndex`, `parse_block_ms` |
| `frontend/src/logic.test.ts` | 14 | clock offset, countdown clamping/formatting, deep link, display order, headlines, start gating, phase labels |
| **Total** | **76 + 14** | |

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

Or the full containerised stack (Docker required):

```bash
docker compose up --build          # nginx :8080 → gateway×2 → engine → redis
docker compose up --build --scale gateway=3
docker compose down
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
.venv/bin/pytest -q -o faulthandler_timeout=60          # 76 tests
cd frontend && npm run typecheck && npm test            # tsc + 14 vitest
cd frontend && npm run build                            # tsc && vite build
cd frontend && npm run smoke                            # 21 browser checks (stack must be up)
sh -n deploy/render-entry.sh && sh -n scripts/dev.sh    # shell scripts parse
REDIS_URL=redis://localhost:6379/0 \
  .venv/bin/python scripts/smoke_upstash.py             # 12/12 command shapes (local Redis)
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

All of these are documented (with defaults) in `.env.example`; nothing is loaded from
it automatically — export them or pass them to your process manager.

| Variable | Default | Read by |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | gateway, engine (Upstash: `rediss://…`) |
| `INSTANCE_ID` | `gateway-…` / `engine-…` | both (shown in UI + health) |
| `ALLOWED_ORIGINS` | `*` | gateway CORS |
| `ADMIN_TOKEN` | unset | guards `POST /admin/kill` |
| `EMBEDDED_ENGINE` | unset | run the engine inside the gateway process |
| `ENGINE_BLOCK_MS` | `1000` | `XREADGROUP BLOCK` ms (`0` = no BLOCK, free tier) |
| `ENGINE_BATCH` | `20` | engine consumer batch |
| `ENGINE_EMPTY_POLL_DELAY` | `0.05` | safety-net poll after an empty read (30 on Render) |
| `ENGINE_TICK_SECONDS` | `0.2` | BUSY retry / post-transition re-check sleep |
| `ENGINE_RESCAN_SECONDS` | `30` | deadline-cache rescan cadence |
| `ENGINE_IDLE_TICK_SECONDS` | `60` | rescan cadence while no rooms exist |
| `ENGINE_SWEEP_SECONDS` | `300` | empty-room sweeper |
| `SUPABASE_DB_URL` | unset | question bank via direct Postgres (preferred; else REST, else JSON) |
| `SUPABASE_URL` / `SUPABASE_KEY` | unset | question bank via REST (else JSON bank) |
| `SUPABASE_SERVICE_KEY` | unset | `scripts/seed_supabase.py` only (keep out of git) |
| `VITE_GATEWAY_URL` | `http://127.0.0.1:8000` | Vite proxy target (dev/preview) |
| `VITE_WS_URL` | same-origin `/ws` | explicit WS override (needed on Vercel) |
| `SMOKE_URL` | `http://127.0.0.1:4173` | base URL for the Playwright smoke test |

`.env` holds the Supabase DSN and is gitignored; `scripts/dev.sh` sources it
automatically for local services. The question loader reads `SUPABASE_DB_URL`
(direct Postgres) or `SUPABASE_URL`/`SUPABASE_KEY` (REST) from the process
env and always falls back to `data/questions.json` on any failure.

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
  and nginx does exactly the same job (plus load balancing) in the container stack.
* **Wake channel + deadline-cached timer loop** (Phase 4): `send_action` XADDs
  (with `MAXLEN ~10000`) and pings `ck:wake`; the engine sleeps on that ping instead of
  polling `XREADGROUP`, and the timer loop only touches Redis when a cached deadline
  passes. A free Upstash (500K cmds/month) stays far under budget while action latency
  stays instant — the poll is only a 30s safety net.
* **One backend image, one deployable process**: gateway and engine are the same
  image with different commands; `EMBEDDED_ENGINE=1` collapses them into one process
  for free-tier single-service hosts, while compose runs them separately.
* **nginx is both the static host and the WebSocket load balancer**, so the static
  site (Vercel) and the self-hosted compose stack share one `/ws` path convention.

**Limitations / not yet done**

* **Docker is not available in this dev environment** (WSL without the Docker Desktop
  integration), so `Dockerfile`, `frontend/Dockerfile` and `docker-compose.yml` were
  validated by YAML parsing, `sh -n`, and the CI `stack-e2e` job (image builds, compose
  up, nginx `-t`, browser smoke through the LB) — not by a local `docker compose up`.
* Postgres/Supabase is only used for the optional question bank; **match outcomes /
  leaderboard are not persisted** (spec item still open).
* **Render free tier sleeps** after ~15 min without HTTP traffic: sockets drop and the
  in-memory room set is lost from the process (Redis keeps documents, but a sleeping
  service can't advance timers) until the next cold start (~1 min). Acceptable for a
  demo; the client reconnects automatically.
* **Vercel does not proxy WebSockets**, so the static deployment must set
  `VITE_WS_URL=wss://<render-host>/ws` at build time; everything else is same-origin.
* No sweeper for stale `conns` entries after an abrupt gateway crash; a player's
  `connected` flag refreshes on their next reconnect/action.
* Rooms with no host leave (players idling in a finished game) persist until the
  sweeper sees an empty roster; `leave` deletes an empty room immediately.
* Redis has persistence disabled in the local dev script (`--save ''`) and in compose —
  state is in-memory for the demo (Upstash persists by default on the free tier).

---

## 12. Packaging & deployment (Phase 4)

### Three topologies, one codebase

| Topology | Command | Frontend | Backend | Redis |
|---|---|---|---|---|
| Local processes | `scripts/dev.sh up` | Vite preview :4173 (proxies `/ws`) | engine :8001 + gateway :8000 | local `redis-server` |
| Containers | `docker compose up --build` | nginx :8080 (SPA + `/ws` LB) | gateway **×2** (scale flag) + engine | `redis:7-alpine`, no volume |
| Free-tier cloud | see below | Vercel (static) | Render: **one** web service, `EMBEDDED_ENGINE=1` | Upstash free |

**Container topology** — `frontend/Dockerfile` builds the SPA (its build stage runs
`tsc --noEmit && vite build`, so a broken type fails the image build) and serves it from
nginx using `docker/nginx/default.conf`, which is simultaneously the SPA server and the
WebSocket load balancer: `location /ws` proxies to the `gateway` upstream (Docker DNS
returns one A record per replica). The backend `Dockerfile` is shared by the gateway and
engine services; compose overrides only the command.

**Free-tier budget math (why Phase 4 changed the engine)** — Upstash free = 500K
commands/month, and it forbids blocking reads. The Phase 2 code would have spent it:

| Naive loop | Rate | Monthly |
|---|---|---|
| `XREADGROUP BLOCK 1000` poll | 86,400 reads/day | ~2.6M ❌ |
| timer scan (`SMEMBERS` + per-room ops every 0.2s) | 432,000+/day | ~13M ❌ |

| With wake + deadline index | Rate | Monthly |
|---|---|---|
| consumer safety poll (`ENGINE_EMPTY_POLL_DELAY=30`) | 2,880/day | ~86K |
| deadline rescan (30s with rooms; 60s idle) + empty-room sweep (300s) | ~4,600/day | ~140K |
| per client action (XADD + wake PUBLISH + XREADGROUP + XACK) | ~4–6/action | traffic |

Idle ≈ **225K/month (~45% of budget)**; the rest is headroom for actual play. With
`ENGINE_BLOCK_MS=0` the consumer never sends `BLOCK`, which is also the only shape
Upstash accepts (`BLOCK 0` would mean "block forever" in Redis). Verify against the real
thing with `scripts/smoke_upstash.py` (12 checks: `XADD MAXLEN`, no-BLOCK
`XREADGROUP`, `XAUTOCLAIM`, `SET NX PX`, wake Pub/Sub…).

### Free-tier provider matrix (no credit card)

| Piece | Provider | Free tier | Why |
|---|---|---|---|
| Static SPA | Vercel | hobby, static only | CDN + preview deploys; **no WS proxy** → set `VITE_WS_URL=wss://<render>/ws` |
| Backend (gateway+engine) | Render | 750 instance-hours/month | Docker support, `/health` checks, WebSockets; **exactly one service fits** (2×730 > 750) → embedded engine |
| Redis | Upstash | 256 MB, 500K cmds/month | TLS `rediss://`, no cold start, Pub/Sub + Streams |
| Question bank (optional) | Supabase | Postgres + REST | `supabase/questions.sql` + `scripts/seed_supabase.py`; app falls back to the JSON bank |
| Git + CI | GitHub | Actions free (public repos) | `.github/workflows/ci.yml` |

`render.yaml` (blueprint) pins the cost-critical env vars; `deploy/render-entry.sh` is
the start command and sets the same values again (so the Dockerfile's default CMD is
correct even without it). Behaviour to expect on the free tier: Render **sleeps after
~15 min idle** (~1 min cold start) — active sockets drop and the client reconnects with
its stored token; rooms live in Redis, but timers only advance while the service is
awake.

### CI (`.github/workflows/ci.yml`)

1. **backend** — ruff, config-file parse (`docker-compose.yml`, `render.yaml`,
   `vercel.json`), 76 pytest (fakeredis, no services needed).
2. **frontend** — `npm ci`, typecheck, 14 vitest, production build.
3. **browser-smoke** — real Redis service container + engine + gateway + preview, then
   the 21-check Playwright suite.
4. **stack-e2e** — `docker compose config`, builds **both** images, `nginx -t` inside
   the web image (against a resolvable dummy `gateway` upstream), `compose up --wait`,
   then the browser smoke test **through the nginx load balancer**.

### CD (`.github/workflows/deploy.yml`)

Runs on every push to `main` (and `workflow_dispatch`), four jobs, each gating the
next — a red test stops everything before anything is published:

1. **gate** — `ruff check`, the 76-test pytest suite (which contains the four
   assessment-mandated tests: `GET /health`, unique join tokens, `compute_minority`
   splits/ties/unanimous/AFK, duplicate/late `submit_vote`) + frontend
   typecheck/vitest. **Failure blocks the deploy.**
2. **build + push ghcr.io** — `ghcr.io/<owner>/<repo>/backend:<sha|latest>` (gateway +
   engine image) and `…/web:<sha|latest>` (SPA + nginx), authenticated with the
   workflow's `GITHUB_TOKEN` (`permissions: packages: write`).
3. **deploy-vm** (if `SSH_HOST` is set) — `rsync`s the repo to the VM and runs
   `deploy/vm-deploy.sh`, which logs into ghcr (if `GHCR_TOKEN` is set), `compose pull`s
   the SHA-tagged images, `up -d --no-build --wait` and curls `:8080/health`. The
   compose file takes `BACKEND_IMAGE` / `WEB_IMAGE` overrides, so CI images and local
   `--build` use the same file.
4. **deploy-render** (if `RENDER_DEPLOY_HOOK` is set) — `POST`s the Render deploy hook;
   Render rebuilds from `render.yaml` (source build, independent of ghcr).

Repository secrets (all optional; unset → the job logs a skip notice instead of
failing): `SSH_HOST`, `SSH_USER`, `SSH_PORT?`, `SSH_PRIVATE_KEY`, `GHCR_TOKEN?`,
`RENDER_DEPLOY_HOOK?`. To make failures block *merges*, mark the `ci` workflow's
checks as required in GitHub branch protection (repo setting, not a file).

### First deploy, step by step

```bash
# 1. GitHub: push the repo -> all four CI jobs must be green.
# 2. Upstash (console.upstash.com): create a Redis DB -> copy the rediss:// URL
#    (optionally run: REDIS_URL=rediss://… python scripts/smoke_upstash.py)
# 3. Render (dashboard): New + Blueprint -> repo -> paste the Upstash URL into
#    REDIS_URL -> deploy; watch /health go green.
# 4. Vercel (vercel.com): import the repo (vercel.json is picked up) -> set
#    VITE_WS_URL=wss://<your-render-service>.onrender.com/ws -> deploy.
# 5. Optional Supabase (question bank): easiest via Postgres -
#    SUPABASE_DB_URL=postgresql://… python scripts/seed_supabase.py
#    (creates + seeds the table; IPv4-only hosts use the pooler form,
#    see .env.example) then add SUPABASE_DB_URL to the Render environment.
#    REST alternative: run supabase/questions.sql, seed with
#    SUPABASE_URL=… SUPABASE_SERVICE_KEY=…, set SUPABASE_URL + SUPABASE_KEY.
```

---

## 13. Status

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Core rules + unit tests | ✅ 36 tests |
| 2 | Distributed backend (gateway, engine, Redis) | ✅ 64 tests, bot swarm + kill/reconnect PASS |
| 3 | Frontend (React/Vite/TS, projector + player views) | ✅ typecheck, 14 vitest, build, 21/21 smoke, 11-screen M3 matte redesign |
| 4 | Docker & orchestration + free-tier deploy configs | ✅ compose/nginx/Render/Vercel/Upstash/Supabase, CI (4 jobs), 76 tests, 21/21 smoke; **local `docker compose up` unverified — no Docker in this env (CI stack-e2e covers it)** |
| 5 | CI/CD (GitHub Actions → ghcr → VM) | ✅ `deploy.yml`: gate (lint + 76 tests) → ghcr push → VM via SSH (`deploy/vm-deploy.sh`) / Render webhook; **runs only once the repo is on GitHub with secrets — workflow YAML validated, not yet executed** |
| 6 | README + architecture diagram + talking points | ✅ README: rules, Mermaid + ASCII diagrams, quickstart, 2–3 min presentation script, interview Q&A |
