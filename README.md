# Categories Game

A minimal, polished two-player "Categories" game (Scattergories style) that runs in mobile browsers over the local network. Built with Python 3.11 (FastAPI) and Vanilla JS.

## Features
- **Real-time Gameplay**: Uses WebSockets for instant state synchronization.
- **Mobile-First Design**: Optimized for phone screens with touch-friendly UI.
- **Local Multiplayer**: Play with a friend on the same Wi-Fi.
- **In-Memory State**: No database required.

## Tech Stack
- **Backend**: Python 3.11, FastAPI, Uvicorn, WebSockets.
- **Frontend**: HTML5, CSS3, Vanilla JavaScript.
- **Manager**: `uv` (Fast Python package installer and resolver).

## How to Run

### Prerequisites
- Python 3.11+
- `uv` installed (recommended) or standard `pip`.

### Setup & Run
1.  **Clone/Open** the project folder.
2.  **Run with `uv`** (Handles venv and dependencies automatically):
    ```bash
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
    ```
    *Alternatively, manally:*
    ```bash
    uv venv
    uv pip install fastapi uvicorn websockets
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
    ```

3.  **Find your IP Address**:
    - Windows: Run `ipconfig` in terminal (look for IPv4 Address, e.g., `192.168.1.x`).
    - Mac/Linux: Run `ifconfig` or `ip a`.

## How to Play

### 1. Connect
- Make sure both phones are on the same Wi-Fi as the computer running the server.
- Open your browser and go to `http://<YOUR_COMPUTER_IP>:8000`.

### 2. Host Game (Player 1)
- Enter your name.
- Click **Host New Game**.
- Note the **Room Code** shown at the top.

### 3. Join Game (Player 2)
- Enter your name.
- Enter the **Room Code**.
- Click **Join Game**.

### 4. Play!
- **Host** starts the game.
- You have 60 seconds to fill in words starting with the **Letter** for 5 random **Categories**.
- If one player submits early, the other has only **5 seconds** left!
- **Score Results**: After each round, rate your answers (0=Invalid, 1=Duplicate, 2=Unique).
- Play 3 rounds to see the final winner.
