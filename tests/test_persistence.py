import time

import pytest

from app.game.manager import GameManager
from app.game.models import Player, Room
from app.game.persistence import GameStore


@pytest.mark.asyncio
async def test_player_upsert_persists_connection_state(tmp_path):
    store = GameStore(tmp_path / "categories.db")
    room = Room(code="TEST")
    player = Player(
        id="player-1",
        name="Alex",
        session_token="session-1",
        is_host=True,
        is_connected=False,
        disconnect_time=123.0,
    )
    room.players[player.id] = player

    await store.save_room(room.code, room.state.value, room.model_dump())
    await store.save_player(
        player.id,
        player.session_token,
        room.code,
        player.name,
        player.is_host,
        player.join_order,
        player.score,
        player.is_connected,
        player.disconnect_time,
        player.model_dump(),
    )

    saved = await store.get_player_by_session(player.session_token)
    assert saved["is_connected"] is False
    assert saved["disconnect_time"] == 123.0

    player.is_connected = True
    player.disconnect_time = None
    player.name = "Alex updated"
    await store.save_player(
        player.id,
        player.session_token,
        room.code,
        player.name,
        player.is_host,
        player.join_order,
        player.score,
        player.is_connected,
        player.disconnect_time,
        player.model_dump(),
    )

    updated = await store.get_player_by_session(player.session_token)
    assert updated["name"] == "Alex updated"
    assert updated["is_connected"] is True
    assert updated["disconnect_time"] is None


@pytest.mark.asyncio
async def test_room_delete_cascades_to_players(tmp_path):
    store = GameStore(tmp_path / "categories.db")
    room = Room(code="TEST")
    player = Player(id="player-1", name="Alex", session_token="session-1")
    room.players[player.id] = player

    await store.save_room(room.code, room.state.value, room.model_dump())
    await store.save_player(
        player.id,
        player.session_token,
        room.code,
        player.name,
        player.is_host,
        player.join_order,
        player.score,
        player.is_connected,
        player.disconnect_time,
        player.model_dump(),
    )
    await store.delete_room(room.code)

    assert await store.get_player_by_session(player.session_token) is None


@pytest.mark.asyncio
async def test_restart_restores_rooms_with_all_players_disconnected(tmp_path, monkeypatch):
    store = GameStore(tmp_path / "categories.db")
    room = Room(code="LIVE")
    host = Player(
        id="host",
        name="Host",
        session_token="host-session",
        is_host=True,
        is_connected=True,
    )
    guest = Player(
        id="guest",
        name="Guest",
        session_token="guest-session",
        is_connected=True,
        join_order=1,
    )
    room.players = {host.id: host, guest.id: guest}

    await store.save_room(room.code, room.state.value, room.model_dump())
    for player in room.players.values():
        await store.save_player(
            player.id,
            player.session_token,
            room.code,
            player.name,
            player.is_host,
            player.join_order,
            player.score,
            player.is_connected,
            player.disconnect_time,
            player.model_dump(),
        )

    monkeypatch.setattr("app.game.manager.game_store", store)
    manager = GameManager()
    before_restart = time.time()
    await manager.initialize()

    restored = manager.rooms[room.code]
    assert all(not player.is_connected for player in restored.players.values())
    assert all(
        player.disconnect_time is not None and player.disconnect_time >= before_restart
        for player in restored.players.values()
    )
    persisted = await store.get_player_by_session(host.session_token)
    assert persisted["is_connected"] is False
