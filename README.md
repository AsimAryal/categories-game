# Categories

A mobile-first, real-time party game inspired by Scattergories. Two to five players join with a room code, race to answer five categories with the same letter, judge each other's answers, and keep playing until the host calls the final score.

## What is included

- Installable PWA designed for phone screens, safe areas, dark mode, and touch
- Real-time FastAPI WebSocket game server
- Server-authoritative round, rush, and scoring timers
- Reconnection after a phone sleeps, changes network, or reloads
- SQLite room/session recovery after a server restart
- Room-code access with host-only game controls
- Preact + TypeScript frontend built to static assets

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20+ and npm (build-time only)

## Production-style local run

Install dependencies and build the frontend:

```bash
uv sync
cd frontend
npm ci
npm run build
cd ..
```

Start the single FastAPI process:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Open `http://localhost:8000` on the host or `http://<HOST-LAN-IP>:8000` from another device on the same network.

The frontend must be built before FastAPI starts because the server serves `frontend/dist/`. Node is not used at runtime after that build.

## Development

Run the backend:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal, run Vite:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/ws` to FastAPI.

Useful checks:

```bash
uv run pytest
cd frontend
npm run typecheck
npm test
npm run build
```

## Raspberry Pi deployment

1. Install Python 3.11+, `uv`, Node.js 20+, and npm.
2. Clone or update the repository.
3. Run `uv sync`.
4. Run `cd frontend && npm ci && npm run build && cd ..`.
5. Ensure the account running the server can write to the repository directory. SQLite stores live state in `game_data.db` beside this README.
6. Start Uvicorn with exactly one worker:

   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
   ```

Do not use multiple Uvicorn workers. Active rooms and WebSocket connections are process-local; multiple workers would split players between isolated game managers.

For upgrades, stop the process, pull the new code, rerun `uv sync` and `npm ci && npm run build`, then restart Uvicorn. Existing room JSON uses backwards-compatible defaults, but avoid deploying during a game when possible.

### Cloudflare Tunnel

The app uses the current page origin for `/ws`, so no public hostname is hardcoded. Point a named tunnel ingress at the local server:

```yaml
ingress:
  - hostname: categories.example.com
    service: http://localhost:8000
  - service: http_status:404
```

Cloudflare Tunnel supports WebSockets automatically. Keep the Uvicorn process on one worker and ensure no proxy rule strips the `/ws` upgrade. HTTPS supplied by Cloudflare is required for service workers and PWA installation outside `localhost`.

For a temporary test tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

Do not commit Cloudflare credentials or tunnel tokens.

## Persistence and recovery

- Runtime state is stored in `game_data.db` using SQLite WAL mode.
- Rooms updated within the last 24 hours can be restored after restart.
- All restored players begin disconnected and reclaim their seat using the session token in their browser.
- Disconnected lobby seats and old rooms are cleaned up automatically.
- `game_data.db` is intentionally ignored by Git. Back it up separately if retaining recent sessions matters.

## How to play

1. Enter your name and host a room.
2. Share the four-character code or use the phone share sheet.
3. Once at least two players are connected, the host starts the round.
4. Enter one answer for each category using the shown letter.
5. When the first player submits, the rush timer starts for everyone else.
6. Judge other players' answers: `0` invalid, `1` duplicate, `2` unique.
7. After at least three rounds, the host can finish the game.

## Troubleshooting

- **Blank or 404 page:** run `cd frontend && npm run build`.
- **Phones cannot connect:** allow TCP port 8000 through the host firewall and use the host's LAN IP.
- **PWA install is unavailable on a LAN IP:** use the HTTPS Cloudflare hostname; plain HTTP installation only works on `localhost`.
- **Players split across rooms unexpectedly:** confirm Uvicorn is running with one worker and only one app process points at the database.
- **Reconnect does not restore a seat:** the room may be older than 24 hours, the seat may have passed its disconnect grace period, or browser storage was cleared.

## Repository layout

- `app/main.py` — FastAPI entry point and built-frontend serving
- `app/game/` — protocol models, game manager, WebSocket routing, persistence
- `frontend/src/` — Preact UI and typed WebSocket client
- `frontend/public/` — source PWA assets
- `frontend/dist/` — generated production bundle (not committed)
- `tests/` — backend regression tests
