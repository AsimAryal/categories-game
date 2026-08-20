import time
from unittest.mock import AsyncMock

import pytest

from app.game.manager import GameActionError, GameManager
from app.game.models import GameState


@pytest.fixture
def game_manager(monkeypatch):
    manager = GameManager()
    for method_name in ("save_room", "save_player", "delete_room", "delete_player"):
        monkeypatch.setattr(
            f"app.game.manager.game_store.{method_name}",
            AsyncMock(),
        )
    return manager


async def create_two_player_room(manager: GameManager):
    room, host = await manager.create_room("Host")
    room, guest = await manager.join_room(room.code, "Guest")
    return room, host, guest


@pytest.mark.asyncio
async def test_only_host_can_start_or_change_rules(game_manager):
    room, host, guest = await create_two_player_room(game_manager)

    with pytest.raises(GameActionError, match="Only the host"):
        await game_manager.start_round(
            room.code,
            guest.id,
            expected_state=GameState.LOBBY,
        )

    with pytest.raises(GameActionError, match="Only the host"):
        await game_manager.update_settings(
            room.code,
            guest.id,
            round_duration_seconds=90,
        )

    updated = await game_manager.update_settings(
        room.code,
        host.id,
        rush_seconds=10,
        round_duration_seconds=90,
        scoring_timeout_seconds=0,
    )
    assert updated.rush_seconds == 10
    assert updated.round_duration_seconds == 90
    assert updated.scoring_timeout_seconds is None


@pytest.mark.asyncio
async def test_round_uses_countdown_and_first_submission_shortens_deadline(game_manager):
    room, host, guest = await create_two_player_room(game_manager)
    current_round = await game_manager.start_round(
        room.code,
        host.id,
        expected_state=GameState.LOBBY,
    )

    assert 2.5 <= room.starts_at - time.time() <= 3.1
    assert room.round_deadline == pytest.approx(
        room.starts_at + room.round_duration_seconds,
    )

    room.starts_at = time.time() - 1
    original_deadline = time.time() + 60
    room.round_deadline = original_deadline
    host_answers = {category: "Answer" for category in current_round.categories}
    result = await game_manager.submit_answers(room.code, host.id, host_answers)

    assert result["first_submission"] is True
    assert result["all_submitted"] is False
    assert result["round_deadline"] < original_deadline
    assert 4 <= result["round_deadline"] - time.time() <= 5.1

    with pytest.raises(GameActionError, match="already submitted"):
        await game_manager.submit_answers(room.code, host.id, host_answers)

    guest_answers = {category: "Another" for category in current_round.categories}
    final_result = await game_manager.submit_answers(room.code, guest.id, guest_answers)
    assert final_result["all_submitted"] is True
    assert room.state == GameState.SCORING


@pytest.mark.asyncio
async def test_expired_round_auto_submits_blanks_and_enters_scoring(game_manager):
    room, host, guest = await create_two_player_room(game_manager)
    current_round = await game_manager.start_round(
        room.code,
        host.id,
        expected_state=GameState.LOBBY,
    )
    room.starts_at = time.time() - 10
    room.round_deadline = time.time() - 1
    expected_deadline = room.round_deadline

    assert await game_manager.expire_round(room.code, expected_deadline) is True
    assert room.state == GameState.SCORING
    assert set(current_round.answers) == {host.id, guest.id}
    assert all(
        answer == ""
        for player_answers in current_round.answers.values()
        for answer in player_answers.values()
    )
    assert await game_manager.expire_round(room.code, expected_deadline) is False


@pytest.mark.asyncio
async def test_scoring_finalizes_once_and_preserves_history(game_manager):
    room, host, guest = await create_two_player_room(game_manager)
    current_round = await game_manager.start_round(
        room.code,
        host.id,
        expected_state=GameState.LOBBY,
    )
    room.starts_at = time.time() - 1
    room.round_deadline = time.time() + 60
    await game_manager.submit_answers(
        room.code,
        host.id,
        {category: "Host answer" for category in current_round.categories},
    )
    await game_manager.submit_answers(
        room.code,
        guest.id,
        {category: "Guest answer" for category in current_round.categories},
    )

    host_votes = {category: {guest.id: 2} for category in current_round.categories}
    guest_votes = {category: {host.id: 1} for category in current_round.categories}
    first = await game_manager.submit_scores(room.code, host.id, host_votes)
    second = await game_manager.submit_scores(room.code, guest.id, guest_votes)

    assert first["finished"] is False
    assert second["finished"] is True
    assert room.state == GameState.ROUND_RESULTS
    assert len(room.history) == 1
    assert host.score == 5
    assert guest.score == 10

    with pytest.raises(GameActionError, match="not being accepted"):
        await game_manager.submit_scores(room.code, guest.id, guest_votes)
    assert len(room.history) == 1


@pytest.mark.asyncio
async def test_disconnect_of_last_pending_scorer_advances_results(game_manager):
    room, host, guest = await create_two_player_room(game_manager)
    current_round = await game_manager.start_round(
        room.code,
        host.id,
        expected_state=GameState.LOBBY,
    )
    room.starts_at = time.time() - 1
    room.round_deadline = time.time() + 60
    for player in (host, guest):
        await game_manager.submit_answers(
            room.code,
            player.id,
            {category: player.name for category in current_round.categories},
        )

    await game_manager.submit_scores(
        room.code,
        host.id,
        {category: {guest.id: 2} for category in current_round.categories},
    )
    await game_manager.mark_player_disconnected(guest.id)

    advanced = await game_manager.advance_after_roster_change(room.code)
    assert advanced == GameState.ROUND_RESULTS
    assert room.state == GameState.ROUND_RESULTS
    assert len(room.history) == 1


@pytest.mark.asyncio
async def test_invalid_state_transitions_are_rejected(game_manager):
    room, host, _ = await create_two_player_room(game_manager)

    with pytest.raises(GameActionError, match="only end after round results"):
        await game_manager.end_game(room.code, host.id)

    await game_manager.start_round(
        room.code,
        host.id,
        expected_state=GameState.LOBBY,
    )
    with pytest.raises(GameActionError, match="Cannot start a round"):
        await game_manager.start_round(
            room.code,
            host.id,
            expected_state=GameState.LOBBY,
        )
