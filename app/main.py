from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.game.websocket import handle_websocket
from app.game.manager import game_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize game manager on startup, cleanup on shutdown."""
    await game_manager.initialize()
    yield
    # Cleanup on shutdown (optional)


app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await handle_websocket(websocket)

@app.get("/")
async def get():
    return FileResponse("static/index.html")
