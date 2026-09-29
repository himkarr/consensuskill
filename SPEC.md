Act as a Principal Cloud & Full-Stack Architect. Help me build and deploy "MinorityRule", a real-time distributed multiplayer party game designed for a Cloud & Distributed Systems course assessment.

---

### 1. Project Overview & Rules
A real-time multiplayer dilemma game inspired by *Liar Game*:
- **Lobby**: Host creates a room (6-character alphanumeric code, QR code join). Players join on mobile with a nickname. Host starts with $\ge 3$ players. Everyone starts with 2 lives.
- **Round Cycle**:
  1. Dilemma presented (e.g., "Tea or Coffee?").
  2. Discussion Phase (20s): Real-time chat with rate-limiting; players bluff/persuade.
  3. Voting Phase (10s): Secret server-side vote (A or B).
  4. Reveal Phase: Host screen animates counts. Minority survives; majority loses 1 life.
  5. Elimination: 0 lives = Spectator mode (can watch and chat).
  6. Game ends when 1 player or team remains.
- **Rules & Edge Cases**:
  - Unanimous vote: Void round (no minority exists; no life lost).
  - Tie vote: No lives lost; replay round with a new dilemma.
  - No vote / AFK: Automatically deduct 1 life.
  - Duplicate/Late votes: Reject; only first valid vote within timer window counts.
  - Reconnects: Players can reconnect with their persistent `playerId` and retain lives.

---

### 2. Distributed Architecture Requirements
- **Frontend**: Lightweight React/Vite or Next.js SPA with two distinct views: Host/Projector view and Mobile Player view.
- **Gateway Services (2+ replicas)**: Node.js/Fastify or Go WebSocket nodes handling client connections statelessly.
- **Distributed State & Ingestion**:
  - Redis Pub/Sub for cross-node state sync and chat broadcasts.
  - Redis Streams / sorted sets for serializing and buffering votes.
- **Round Engine Worker**: A single dedicated worker/lock-managed service handling the authoritative state machine (timers, round resolution, DB writes).
- **Persistence (Supabase / PostgreSQL)**: Question bank, match outcomes, and leaderboard.
- **Reverse Proxy / Load Balancer**: Nginx configured with round-robin and WebSocket upgrade support (`sticky session` or distributed socket adapter).

---

### 3. Deliverables Needed

Please provide production-ready code, configs, and architectural files step-by-step:

#### A. Core Engine Logic & Unit Tests
Implement and write unit tests (Jest/Pytest/Vitest) for:
1. `GET /health` returning `200 OK`.
2. `join_room()` issuing a unique session/player token and preventing collisions.
3. `compute_minority(votes)` correctly resolving: standard split, ties, unanimous votes, and missed/AFK votes.
4. `submit_vote()` rejecting duplicates and votes timestamped after phase expiration.

#### B. Distributed Backend Implementation
- Gateway server code with WebSocket clustering via Redis adapter.
- Authoritative state machine engine handling phase transitions (`LOBBY` -> `QUESTION` -> `DISCUSS` -> `VOTE` -> `REVEAL` -> `GAMEOVER`).

#### C. Docker & Orchestration
- Multi-stage `Dockerfile` for the services.
- `docker-compose.yml` spinning up: `nginx-lb` (port 80/443), `gateway-1`, `gateway-2`, `round-engine`, and `redis`.

#### D. CI/CD & Cloud Deployment
- GitHub Actions workflow (`.github/workflows/deploy.yml`):
  1. Runs lint and the 4 mandatory tests.
  2. Blocks merge/push on test failure.
  3. Builds Docker images and pushes to GitHub Container Registry (`ghcr.io`).
  4. Deploys to a free-tier/cloud VM (Render, Railway, or AWS EC2 via SSH/webhook).

#### E. Assessment-Ready README & Interview Talking Points
- Concise architecture diagram in ASCII/Mermaid (`Client -> Nginx -> Gateways [1..N] <-> Redis <-> Engine`).
- Quickstart guide (`docker compose up --build`).
- A 2-3 minute presentation script explaining: distributed state sync, split-brain avoidance during voting, and horizontal scaling.

Start by delivering the core project structure, the exact `compute_minority` engine implementation, and its corresponding test suite.