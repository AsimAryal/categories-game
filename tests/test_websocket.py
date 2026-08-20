import time
from unittest.mock import AsyncMock, Mock

import pytest

from app.game import websocket as websocket_module
from app.game.manager import GameManager
from app.game.models import BaseMessage, MessageType


@pytest.fixture
def game_manager(monkeypatch):
    manager = GameManager()
    for method_name in ("save_room", "save_player", "delete_room", "delete_player"):
        monkeypatch.setattr(
            f"app.game.manager.game_store.{method_name}",
            AsyncMock(),
        )
    monkeypatch.setattr(websocket_module, "game_manager", manager)
    return manager


@pytest.mark.asyncio
async def test_submission_update_reaches_submitter_and_restores_on_reconnect(
    game_manager,
    monkeypatch,
):
    room, host = await game_manager.create_room("Host")
    room, guest = await game_manager.join_room(room.code, "Guest")
    await game_manager.update_settings(room.code, host.id, rush_seconds=15)
    current_round = await game_manager.start_round(
        room.code,
        host.id,
        expected_state=room.state,
    )
    room.starts_at = time.time() - 1
    room.round_deadline = time.time() + 60

    host_socket = object()
    guest_socket = object()
    connection_manager = websocket_module.ConnectionManager()
    connection_manager.active_connections = {
        host.id: host_socket,
        guest.id: guest_socket,
    }
    connection_manager.broadcast = AsyncMock()
    connection_manager.schedule_round_timeout = Mock()
    monkeypatch.setattr(websocket_module, "manager", connection_manager)

    answers = {category: "Answer" for category in current_round.categories}
    await websocket_module._dispatch_message(
        host_socket,
        "127.0.0.1",
        websocket_module.ConnectionContext(
            player_id=host.id,
            room_code=room.code,
            session_token=host.session_token,
            player_name=host.name,
        ),
        BaseMessage(
            type=MessageType.SUBMIT_ANSWERS,
            payload={"answers": answers},
        ),
    )

    connection_manager.broadcast.assert_awaited_once()
    message, recipients = connection_manager.broadcast.await_args.args
    assert set(recipients) == {host.id, guest.id}
    assert message["type"] == MessageType.OPPONENT_SUBMITTED.value
    assert message["payload"]["submitted_by"] == host.id
    assert message["payload"]["submitted_ids"] == [host.id]
    assert message["payload"]["rush_active"] is True
    assert message["payload"]["rush_seconds"] == 15

    reconnect_state = websocket_module._build_reconnect_state(room, guest)
    assert reconnect_state["rush_active"] is True
    assert reconnect_state["submitted_ids"] == [host.id]
    assert reconnect_state["round_deadline"] == message["payload"]["round_deadline"]
