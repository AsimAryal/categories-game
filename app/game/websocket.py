import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .manager import GameActionError, game_manager
from .models import (
    BaseMessage,
    EmptyPayload,
    GameState,
    JoinGamePayload,
    MessageType,
    RejoinGamePayload,
    ScorePayload,
    StartGamePayload,
    SubmitAnswersPayload,
    UpdateSettingsPayload,
)
from .persistence import game_store

logger = logging.getLogger("uvicorn.error")


def log_game_event(
    room_code: str,
    event: str,
    details: str = "",
    level: str = "info",
):
    timestamp = datetime.now().strftime("%H:%M:%S")
    room_tag = f"[{room_code}]" if room_code else "[LOBBY]"
    message = f"🎮 {timestamp} {room_tag} {event}"
    if details:
        message += f" | {details}"

    if level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        logger.info(message)


def log_connection(
    event: str,
    ip: str,
    player_name: str = "",
    room_code: str = "",
    details: str = "",
):
    timestamp = datetime.now().strftime("%H:%M:%S")
    room_tag = f"[{room_code}]" if room_code else "[---]"
    player_part = f"Player: '{player_name}' | " if player_name else ""
    message = f"🔌 {timestamp} {room_tag} {event} | {player_part}IP: {ip}"
    if details:
        message += f" | {details}"
    logger.info(message)


def log_action(room_code: str, player_name: str, action: str, details: str = ""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = f"⚡ {timestamp} [{room_code}] {action} | Player: '{player_name}'"
    if details:
        message += f" | {details}"
    logger.info(message)


def get_client_ip(websocket: WebSocket) -> str:
    try:
        forwarded = websocket.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return websocket.client.host if websocket.client else "unknown"
    except Exception:
        return "unknown"


@dataclass
class ConnectionContext:
    player_id: Optional[str] = None
    room_code: Optional[str] = None
    session_token: Optional[str] = None
    player_name: Optional[str] = None

    def clear(self):
        self.player_id = None
        self.room_code = None
        self.session_token = None
        self.player_name = None


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.session_connections: Dict[str, str] = {}
        self.player_ips: Dict[str, str] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._round_timeout_tasks: Dict[str, asyncio.Task] = {}
        self._scoring_timeout_tasks: Dict[str, asyncio.Task] = {}
        self._restore_lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()

    def register_player(
        self,
        player_id: str,
        session_token: str,
        websocket: WebSocket,
        ip: str,
    ) -> Optional[WebSocket]:
        old_socket = self.active_connections.get(player_id)
        old_player_id = self.session_connections.get(session_token)
        if old_player_id and old_player_id != player_id:
            old_socket = self.active_connections.get(old_player_id) or old_socket
            self.active_connections.pop(old_player_id, None)
            self.player_ips.pop(old_player_id, None)

        self.active_connections[player_id] = websocket
        self.session_connections[session_token] = player_id
        self.player_ips[player_id] = ip
        return old_socket if old_socket is not websocket else None

    def disconnect(self, player_id: str, websocket: WebSocket) -> bool:
        """Unregister only if this socket is still the player's active session."""
        if self.active_connections.get(player_id) is not websocket:
            return False

        del self.active_connections[player_id]
        self.player_ips.pop(player_id, None)
        for token, mapped_player_id in list(self.session_connections.items()):
            if mapped_player_id == player_id:
                del self.session_connections[token]
        return True

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        try:
            await websocket.send_json(message)
        except Exception:
            pass

    async def send_to_player(self, message: dict, player_id: str):
        websocket = self.active_connections.get(player_id)
        if websocket:
            await self.send_personal_message(message, websocket)

    async def broadcast(self, message: dict, player_ids: List[str]):
        for player_id in player_ids:
            await self.send_to_player(message, player_id)

    async def broadcast_games_list(self):
        message = {
            "type": MessageType.GAMES_LIST.value,
            "payload": {"games": game_manager.get_open_rooms()},
        }
        for websocket in list(self.active_connections.values()):
            await self.send_personal_message(message, websocket)

    def start_background_tasks(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self):
        tasks = [
            task
            for task in ([self._cleanup_task] if self._cleanup_task is not None else [])
            + list(self._round_timeout_tasks.values())
            + list(self._scoring_timeout_tasks.values())
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_task = None
        self._round_timeout_tasks.clear()
        self._scoring_timeout_tasks.clear()

    async def _cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                await game_manager.cleanup_disconnected_players()
                await game_store.cleanup_old_rooms(exclude_room_codes=list(game_manager.rooms))
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Background cleanup failed")

    def schedule_round_timeout(self, room_code: str, deadline: float):
        self.cancel_round_timeout(room_code)

        async def timeout_handler():
            try:
                await asyncio.sleep(max(0.0, deadline - time.time()))
                await self._handle_round_timeout(room_code, deadline)
            except asyncio.CancelledError:
                return
            finally:
                current = asyncio.current_task()
                if self._round_timeout_tasks.get(room_code) is current:
                    del self._round_timeout_tasks[room_code]

        self._round_timeout_tasks[room_code] = asyncio.create_task(timeout_handler())

    def cancel_round_timeout(self, room_code: str):
        task = self._round_timeout_tasks.pop(room_code, None)
        if task and task is not asyncio.current_task():
            task.cancel()

    def schedule_scoring_timeout(self, room_code: str, deadline: float):
        self.cancel_scoring_timeout(room_code)

        async def timeout_handler():
            try:
                await asyncio.sleep(max(0.0, deadline - time.time()))
                await self._handle_scoring_timeout(room_code, deadline)
            except asyncio.CancelledError:
                return
            finally:
                current = asyncio.current_task()
                if self._scoring_timeout_tasks.get(room_code) is current:
                    del self._scoring_timeout_tasks[room_code]

        self._scoring_timeout_tasks[room_code] = asyncio.create_task(timeout_handler())

    def cancel_scoring_timeout(self, room_code: str):
        task = self._scoring_timeout_tasks.pop(room_code, None)
        if task and task is not asyncio.current_task():
            task.cancel()

    async def restore_deadline_tasks(self):
        """Restore persisted absolute deadlines after startup or first connection."""
        async with self._restore_lock:
            for room_code in list(game_manager.rooms):
                room = game_manager.rooms.get(room_code)
                if not room:
                    continue

                if room.state == GameState.PLAYING and room.round_deadline is not None:
                    if room.round_deadline <= time.time():
                        await self._handle_round_timeout(
                            room_code,
                            room.round_deadline,
                        )
                    elif room_code not in self._round_timeout_tasks:
                        self.schedule_round_timeout(room_code, room.round_deadline)

                room = game_manager.rooms.get(room_code)
                if room and room.state == GameState.SCORING and room.scoring_deadline is not None:
                    if room.scoring_deadline <= time.time():
                        await self._handle_scoring_timeout(
                            room_code,
                            room.scoring_deadline,
                        )
                    elif room_code not in self._scoring_timeout_tasks:
                        self.schedule_scoring_timeout(
                            room_code,
                            room.scoring_deadline,
                        )

    async def _handle_round_timeout(self, room_code: str, deadline: float):
        ended = await game_manager.expire_round(room_code, deadline)
        if not ended:
            return

        room = game_manager.rooms.get(room_code)
        if not room:
            return
        log_game_event(room_code, "⏰ ROUND DEADLINE REACHED", level="warning")
        await _announce_round_ended(room)

    async def _handle_scoring_timeout(self, room_code: str, deadline: float):
        room = game_manager.rooms.get(room_code)
        if not room or not room.current_round:
            return

        submitted_ids = set(room.current_round.scoring_votes)
        missing_names = [
            player.name
            for player_id, player in room.connected_players.items()
            if player_id not in submitted_ids
        ]
        finalized = await game_manager.force_finalize_scoring(room_code, deadline)
        if not finalized:
            return

        room = game_manager.rooms.get(room_code)
        if not room:
            return
        log_game_event(
            room_code,
            "⏰ SCORING DEADLINE REACHED",
            f"Missing votes from: {', '.join(missing_names) or 'none'}",
            level="warning",
        )
        await _announce_round_results(room, timeout=True)


manager = ConnectionManager()


async def handle_websocket(websocket: WebSocket):
    await manager.connect(websocket)
    manager.start_background_tasks()
    await manager.restore_deadline_tasks()

    context = ConnectionContext()
    client_ip = get_client_ip(websocket)

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = BaseMessage.model_validate(json.loads(raw_message))
            except (json.JSONDecodeError, ValidationError, TypeError):
                await _send_error(
                    websocket,
                    "INVALID_MESSAGE",
                    "Message must contain a valid type and payload.",
                )
                continue

            try:
                await _dispatch_message(websocket, client_ip, context, message)
            except ValidationError:
                await _send_error(
                    websocket,
                    "INVALID_PAYLOAD",
                    f"Invalid payload for {message.type.value}.",
                )
            except GameActionError as error:
                await _send_error(websocket, error.code, error.message)
            except Exception:
                logger.exception(
                    "WebSocket action failed [%s]",
                    context.room_code or "NO_ROOM",
                )
                await _send_error(
                    websocket,
                    "INTERNAL_ERROR",
                    "The server could not process that action.",
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "WebSocket connection failed [%s]",
            context.room_code or "NO_ROOM",
        )
    finally:
        if context.player_id and manager.disconnect(context.player_id, websocket):
            await _handle_disconnect(context, client_ip)


async def _dispatch_message(
    websocket: WebSocket,
    client_ip: str,
    context: ConnectionContext,
    message: BaseMessage,
):
    payload = message.payload

    if message.type == MessageType.REJOIN_GAME:
        if context.player_id:
            raise GameActionError(
                "ALREADY_JOINED",
                "Leave the current game before joining again.",
            )
        request = RejoinGamePayload.model_validate(payload)
        room, player = await game_manager.rejoin_room(request.session_token)
        if not room or not player:
            raise GameActionError(
                "SESSION_EXPIRED",
                "Could not reconnect. The session has expired.",
            )

        context.player_id = player.id
        context.room_code = room.code
        context.session_token = player.session_token
        context.player_name = player.name
        old_socket = manager.register_player(
            player.id,
            player.session_token,
            websocket,
            client_ip,
        )
        if old_socket:
            await manager.send_personal_message(
                {
                    "type": MessageType.SESSION_HIJACKED.value,
                    "payload": {"message": "Session opened in another tab."},
                },
                old_socket,
            )
            try:
                await old_socket.close()
            except Exception:
                pass

        advanced_state = await game_manager.advance_after_roster_change(room.code)
        log_connection(
            "♻️ RECONNECTED",
            client_ip,
            player.name,
            room.code,
            f"State: {room.state.value}",
        )
        await manager.send_personal_message(
            {
                "type": MessageType.RECONNECTED.value,
                "payload": _build_reconnect_state(room, player),
            },
            websocket,
        )

        reconnect_payload = {
            "player_id": player.id,
            "player_name": player.name,
            "connected_count": len(room.connected_players),
        }
        if room.state == GameState.PLAYING and room.current_round:
            connected_ids = set(room.connected_players)
            reconnect_payload["submitted_count"] = len(
                connected_ids & set(room.current_round.answers)
            )
            reconnect_payload["submitted_ids"] = list(room.current_round.answers)
            reconnect_payload["round_deadline"] = room.round_deadline
        await manager.broadcast(
            {
                "type": MessageType.PLAYER_RECONNECTED.value,
                "payload": reconnect_payload,
            },
            [player_id for player_id in room.connected_players if player_id != player.id],
        )
        await _announce_roster_advance(room, advanced_state)
        return

    if message.type == MessageType.JOIN_GAME:
        if context.player_id:
            raise GameActionError(
                "ALREADY_JOINED",
                "Leave the current game before joining again.",
            )
        request = JoinGamePayload.model_validate(payload)
        requested_code = request.room_code.strip() if request.room_code else ""
        if requested_code:
            room, player = await game_manager.join_room(
                requested_code,
                request.player_name,
            )
        else:
            room, player = await game_manager.create_room(
                request.player_name,
                precise_scoring=request.precise_scoring,
            )

        context.player_id = player.id
        context.room_code = room.code
        context.session_token = player.session_token
        context.player_name = player.name
        manager.register_player(
            player.id,
            player.session_token,
            websocket,
            client_ip,
        )
        log_connection(
            "➡️ JOINED ROOM",
            client_ip,
            player.name,
            room.code,
            f"Players: {len(room.players)}/5",
        )
        await manager.send_personal_message(
            {
                "type": MessageType.LOBBY_UPDATE.value,
                "payload": {
                    "room_code": room.code,
                    "player_id": player.id,
                    "is_host": player.is_host,
                    "session_token": player.session_token,
                    "players": [
                        _player_to_dict(room_player) for room_player in room.players.values()
                    ],
                    "settings": _settings_to_dict(room),
                },
            },
            websocket,
        )
        await _broadcast_room_state(room, exclude_player_id=player.id)
        await manager.broadcast_games_list()
        return

    if message.type == MessageType.GET_GAMES:
        EmptyPayload.model_validate(payload)
        await manager.send_personal_message(
            {
                "type": MessageType.GAMES_LIST.value,
                "payload": {"games": game_manager.get_open_rooms()},
            },
            websocket,
        )
        return

    room, player_id = _require_joined(context, websocket)

    if message.type == MessageType.START_GAME:
        request = StartGamePayload.model_validate(payload)
        new_round = await game_manager.start_round(
            room.code,
            player_id,
            expected_state=GameState.LOBBY,
            rush_seconds=request.rush_seconds,
            precise_scoring=request.precise_scoring,
        )
        room = game_manager.rooms[room.code]
        manager.schedule_round_timeout(room.code, room.round_deadline)
        log_game_event(
            room.code,
            f"🎬 ROUND {new_round.round_number} STARTED",
            f"Letter: {new_round.letter} | Duration: {room.round_duration_seconds}s",
        )
        await _broadcast_to_room(room, _round_start_message(room))
        await manager.broadcast_games_list()
        return

    if message.type == MessageType.SUBMIT_ANSWERS:
        request = SubmitAnswersPayload.model_validate(payload)
        result = await game_manager.submit_answers(
            room.code,
            player_id,
            request.answers,
        )
        room = game_manager.rooms[room.code]

        if not result["accepted"]:
            await _send_error(
                websocket,
                result["error_code"],
                result["error_message"],
            )
            if result.get("round_ended"):
                manager.cancel_round_timeout(room.code)
                await _announce_round_ended(room)
            return

        log_action(
            room.code,
            context.player_name or "",
            "📝 SUBMITTED ANSWERS",
            f"Filled: {sum(bool(value.strip()) for value in request.answers.values())}/5",
        )
        if result["all_submitted"]:
            manager.cancel_round_timeout(room.code)
            await _announce_round_ended(room)
            return

        if result["first_submission"]:
            manager.schedule_round_timeout(room.code, result["round_deadline"])
        submitted_ids = result["submitted_ids"]
        target_ids = [
            connected_id
            for connected_id in room.connected_players
            if connected_id not in submitted_ids
        ]
        await manager.broadcast(
            {
                "type": MessageType.OPPONENT_SUBMITTED.value,
                "payload": {
                    "opponent_id": player_id,
                    "rush_seconds": room.rush_seconds,
                    "round_deadline": room.round_deadline,
                    "submitted_ids": submitted_ids,
                },
            },
            target_ids,
        )
        return

    if message.type == MessageType.SUBMIT_SCORES:
        request = ScorePayload.model_validate(payload)
        result = await game_manager.submit_scores(
            room.code,
            player_id,
            request.scores,
        )
        room = game_manager.rooms[room.code]

        if result["timed_out"]:
            manager.cancel_scoring_timeout(room.code)
            await _send_error(
                websocket,
                "SCORING_CLOSED",
                "The scoring deadline has passed.",
            )
            await _announce_round_results(room, timeout=True)
            return

        log_action(room.code, context.player_name or "", "🗳️ SUBMITTED SCORES")
        if result["finished"]:
            manager.cancel_scoring_timeout(room.code)
            await _announce_round_results(room)
            return

        await manager.broadcast(
            {
                "type": MessageType.SCORING_UPDATE.value,
                "payload": {
                    "player_id": player_id,
                    "player_name": context.player_name,
                    "submitted_ids": result["submitted_ids"],
                    "total_players": len(room.connected_players),
                },
            },
            [connected_id for connected_id in room.connected_players if connected_id != player_id],
        )
        return

    if message.type == MessageType.NEXT_ROUND:
        EmptyPayload.model_validate(payload)
        new_round = await game_manager.start_round(
            room.code,
            player_id,
            expected_state=GameState.ROUND_RESULTS,
        )
        room = game_manager.rooms[room.code]
        manager.cancel_scoring_timeout(room.code)
        manager.schedule_round_timeout(room.code, room.round_deadline)
        log_action(room.code, context.player_name or "", "▶️ STARTED NEXT ROUND")
        await _broadcast_to_room(room, _round_start_message(room))
        return

    if message.type == MessageType.END_GAME:
        EmptyPayload.model_validate(payload)
        room = await game_manager.end_game(room.code, player_id)
        manager.cancel_round_timeout(room.code)
        manager.cancel_scoring_timeout(room.code)
        await _broadcast_to_room(
            room,
            {
                "type": MessageType.GAME_OVER.value,
                "payload": {
                    "history": [completed_round.model_dump() for completed_round in room.history],
                    "final_scores": {
                        room_player_id: float(room_player.score)
                        for room_player_id, room_player in room.players.items()
                    },
                },
            },
        )
        return

    if message.type == MessageType.UPDATE_SETTINGS:
        request = UpdateSettingsPayload.model_validate(payload)
        room = await game_manager.update_settings(
            room.code,
            player_id,
            rush_seconds=request.rush_seconds,
            precise_scoring=request.precise_scoring,
            scoring_timeout_seconds=request.scoring_timeout_seconds,
            round_duration_seconds=request.round_duration_seconds,
        )
        await _broadcast_room_state(room)
        return

    if message.type == MessageType.LEAVE_GAME:
        EmptyPayload.model_validate(payload)
        old_host_id = room.host_id
        leaving_player = room.players.get(player_id)
        leaving_name = leaving_player.name if leaving_player else context.player_name or ""
        remaining_room = await game_manager.remove_player(player_id)
        manager.disconnect(player_id, websocket)
        context.clear()
        advanced_state = (
            await game_manager.advance_after_roster_change(remaining_room.code)
            if remaining_room
            else None
        )

        if not remaining_room:
            manager.cancel_round_timeout(room.code)
            manager.cancel_scoring_timeout(room.code)
        else:
            if old_host_id == player_id and remaining_room.host_id:
                new_host = remaining_room.players[remaining_room.host_id]
                await manager.broadcast(
                    {
                        "type": MessageType.HOST_CHANGED.value,
                        "payload": {
                            "new_host_id": new_host.id,
                            "new_host_name": new_host.name,
                        },
                    },
                    list(remaining_room.connected_players),
                )
            await manager.broadcast(
                {
                    "type": MessageType.PLAYER_DISCONNECTED.value,
                    "payload": {
                        "player_id": player_id,
                        "player_name": leaving_name,
                        "left_intentionally": True,
                        "connected_count": len(remaining_room.connected_players),
                    },
                },
                list(remaining_room.connected_players),
            )
            await _announce_roster_advance(remaining_room, advanced_state)
            await _broadcast_room_state(remaining_room)
        await manager.broadcast_games_list()
        return

    raise GameActionError("UNSUPPORTED_ACTION", "That message type is not a client action.")


def _require_joined(context: ConnectionContext, websocket: WebSocket):
    if not context.player_id or not context.room_code:
        raise GameActionError("NOT_JOINED", "Join or reconnect to a game first.")
    if manager.active_connections.get(context.player_id) is not websocket:
        raise GameActionError(
            "SESSION_REPLACED",
            "This session was replaced by a newer connection.",
        )
    room = game_manager.rooms.get(context.room_code)
    if not room or context.player_id not in room.players:
        raise GameActionError("SESSION_EXPIRED", "The current game session has expired.")
    return room, context.player_id


async def _handle_disconnect(context: ConnectionContext, client_ip: str):
    player_id = context.player_id
    if not player_id:
        return

    room, disconnected, new_host = await game_manager.mark_player_disconnected(player_id)
    if not room or not disconnected:
        return
    advanced_state = await game_manager.advance_after_roster_change(room.code)

    log_connection(
        "❌ DISCONNECTED",
        client_ip,
        disconnected.name,
        room.code,
        f"State: {room.state.value}",
    )
    other_ids = [
        connected_id for connected_id in room.connected_players if connected_id != player_id
    ]
    disconnect_payload = {
        "player_id": player_id,
        "player_name": disconnected.name,
        "connected_count": len(room.connected_players),
    }
    if room.state == GameState.PLAYING and room.current_round:
        connected_ids = set(room.connected_players)
        disconnect_payload["submitted_count"] = len(connected_ids & set(room.current_round.answers))
        disconnect_payload["submitted_ids"] = list(room.current_round.answers)
        disconnect_payload["round_deadline"] = room.round_deadline
    await manager.broadcast(
        {
            "type": MessageType.PLAYER_DISCONNECTED.value,
            "payload": disconnect_payload,
        },
        other_ids,
    )

    if new_host:
        await manager.broadcast(
            {
                "type": MessageType.HOST_CHANGED.value,
                "payload": {
                    "new_host_id": new_host.id,
                    "new_host_name": new_host.name,
                },
            },
            list(room.connected_players),
        )
    await _announce_roster_advance(room, advanced_state)
    await _broadcast_room_state(room)
    await manager.broadcast_games_list()


async def _announce_roster_advance(
    room,
    advanced_state: Optional[GameState],
):
    if advanced_state == GameState.SCORING:
        manager.cancel_round_timeout(room.code)
        await _announce_round_ended(room)
    elif advanced_state == GameState.ROUND_RESULTS:
        manager.cancel_scoring_timeout(room.code)
        await _announce_round_results(room)


async def _send_error(
    websocket: WebSocket,
    code: str,
    message: str,
):
    await manager.send_personal_message(
        {
            "type": MessageType.ERROR.value,
            "payload": {"code": code, "message": message},
        },
        websocket,
    )


def _round_start_message(room) -> dict:
    return {
        "type": MessageType.ROUND_START.value,
        "payload": {
            **room.current_round.model_dump(),
            "rush_seconds": room.rush_seconds,
            "round_duration_seconds": room.round_duration_seconds,
            "server_time": time.time(),
            "starts_at": room.starts_at,
            "round_deadline": room.round_deadline,
            "total_players": len(room.connected_players),
        },
    }


async def _announce_round_ended(room):
    if room.scoring_deadline is not None:
        manager.schedule_scoring_timeout(room.code, room.scoring_deadline)
    await _broadcast_to_room(
        room,
        {
            "type": MessageType.ROUND_ENDED.value,
            "payload": {
                "round": room.current_round.model_dump(),
                "players": {
                    player_id: _player_to_dict(player) for player_id, player in room.players.items()
                },
                "scoring_deadline": room.scoring_deadline,
                "scoring_timeout_seconds": room.scoring_timeout_seconds,
            },
        },
    )


async def _announce_round_results(room, timeout: bool = False):
    payload = {
        "round_scores": {
            player_id: {category: float(score) for category, score in category_scores.items()}
            for player_id, category_scores in room.current_round.scores.items()
        },
        "cumulative_scores": {
            player_id: float(player.score) for player_id, player in room.players.items()
        },
        "is_final_round": len(room.history) >= 3,
    }
    if timeout:
        payload["timeout"] = True
    await _broadcast_to_room(
        room,
        {
            "type": MessageType.ROUND_RESULTS.value,
            "payload": payload,
        },
    )


def _build_reconnect_state(room, player) -> dict:
    base_state = {
        "room_code": room.code,
        "game_state": room.state.value,
        "is_host": player.is_host,
        "player_id": player.id,
        "session_token": player.session_token,
        "players": [_player_to_dict(room_player) for room_player in room.players.values()],
        "settings": _settings_to_dict(room),
        "starts_at": room.starts_at or None,
        "round_deadline": room.round_deadline,
        "scoring_deadline": room.scoring_deadline,
    }

    if room.state == GameState.PLAYING and room.current_round:
        connected_ids = set(room.connected_players)
        submitted_ids = list(room.current_round.answers)
        base_state.update(
            {
                "round": room.current_round.model_dump(),
                "remaining_time": game_manager.get_remaining_time(room.code),
                "round_duration_seconds": room.round_duration_seconds,
                "total_players": len(connected_ids),
                "submitted_count": len(connected_ids & set(room.current_round.answers)),
                "submitted_ids": submitted_ids,
            }
        )
        if player.id in room.current_round.answers:
            base_state["my_answers"] = room.current_round.answers[player.id]

    elif room.state == GameState.SCORING and room.current_round:
        base_state.update(
            {
                "round": room.current_round.model_dump(),
                "scoring_remaining": game_manager.get_scoring_remaining_time(room.code),
                "scores_submitted": (player.id in room.current_round.scoring_votes),
                "scoring_timeout_seconds": room.scoring_timeout_seconds,
                "submitted_ids": list(room.current_round.scoring_votes),
                "answer_submitted_ids": list(room.current_round.answers),
            }
        )

    elif room.state == GameState.ROUND_RESULTS and room.current_round:
        base_state.update(
            {
                "round_scores": {
                    player_id: {
                        category: float(score) for category, score in category_scores.items()
                    }
                    for player_id, category_scores in room.current_round.scores.items()
                },
                "cumulative_scores": {
                    player_id: float(room_player.score)
                    for player_id, room_player in room.players.items()
                },
                "is_final_round": len(room.history) >= 3,
            }
        )

    elif room.state == GameState.FINAL_RESULTS:
        base_state.update(
            {
                "history": [completed_round.model_dump() for completed_round in room.history],
                "final_scores": {
                    player_id: float(room_player.score)
                    for player_id, room_player in room.players.items()
                },
            }
        )

    return base_state


def _player_to_dict(player) -> dict:
    return {
        "id": player.id,
        "name": player.name,
        "score": float(player.score),
        "is_host": player.is_host,
        "is_connected": player.is_connected,
    }


def _settings_to_dict(room) -> dict:
    return {
        "precise_scoring": room.precise_scoring,
        "rush_seconds": room.rush_seconds,
        "scoring_timeout_seconds": room.scoring_timeout_seconds,
        "round_duration_seconds": room.round_duration_seconds,
    }


async def _broadcast_to_room(room, message):
    await manager.broadcast(message, list(room.connected_players))


async def _broadcast_room_state(room, exclude_player_id: Optional[str] = None):
    player_ids = [
        player_id for player_id in room.connected_players if player_id != exclude_player_id
    ]
    await manager.broadcast(
        {
            "type": MessageType.LOBBY_UPDATE.value,
            "payload": {
                "room_code": room.code,
                "players": [_player_to_dict(player) for player in room.players.values()],
                "settings": _settings_to_dict(room),
            },
        },
        player_ids,
    )
