from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.game.manager import game_manager
from app.game.websocket import handle_websocket
from app.game.websocket import manager as connection_manager

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await game_manager.initialize()
    connection_manager.start_background_tasks()
    await connection_manager.restore_deadline_tasks()
    try:
        yield
    finally:
        await connection_manager.shutdown()


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await handle_websocket(websocket)


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(ROOT_DIR / "favicon.ico")


@app.get("/app-icon.png")
async def app_icon():
    return FileResponse(ROOT_DIR / "app_icon.png", media_type="image/png")


app.mount(
    "/",
    StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False),
    name="frontend",
)
