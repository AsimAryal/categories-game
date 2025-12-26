"""
WebSocket handler with reconnection and session management.
Rich logging for game events.
"""

from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, List, Optional
import json
import logging
import asyncio
from datetime import datetime
from .models import (
    MessageType, BaseMessage, JoinGamePayload, RejoinGamePayload,
    SubmitAnswersPayload, ScorePayload, GameState
)
from .manager import game_manager

logger = logging.getLogger("uvicorn.error")


# ============================================================================
# LOGGING HELPERS
# ============================================================================

def log_game_event(room_code: str, event: str, details: str = "", level: str = "info"):
    """Log a game event with consistent formatting."""
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


def log_connection(event: str, ip: str, player_name: str = "", room_code: str = "", details: str = ""):
    """Log connection-related events."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    room_tag = f"[{room_code}]" if room_code else "[---]"
    player_part = f"Player: '{player_name}' | " if player_name else ""
    message = f"🔌 {timestamp} {room_tag} {event} | {player_part}IP: {ip}"
    if details:
        message += f" | {details}"
    logger.info(message)


def log_action(room_code: str, player_name: str, action: str, details: str = ""):
    """Log player actions within a game."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = f"⚡ {timestamp} [{room_code}] {action} | Player: '{player_name}'"
    if details:
        message += f" | {details}"
    logger.info(message)


def get_client_ip(websocket: WebSocket) -> str:
    """Extract client IP from WebSocket."""
    try:
        # Try to get forwarded IP first (for reverse proxy setups)
        forwarded = websocket.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        # Fall back to direct connection
        if websocket.client:
            return websocket.client.host
        return "unknown"
    except Exception:
        return "unknown"


# ============================================================================
# CONNECTION MANAGER
# ============================================================================

class ConnectionManager:
    """
    Manages WebSocket connections with session-aware tracking.
    Supports session hijacking detection for duplicate tabs.
    """
    
    def __init__(self):
        # player_id -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        # session_token -> player_id (for hijack detection)
        self.session_connections: Dict[str, str] = {}
        # player_id -> IP address
        self.player_ips: Dict[str, str] = {}
        # Background task handle
        self._cleanup_task: Optional[asyncio.Task] = None
        self._scoring_timeout_tasks: Dict[str, asyncio.Task] = {}
    
    async def connect(self, websocket: WebSocket):
        """Accept new WebSocket connection."""
        await websocket.accept()
    
    def register_player(
        self, 
        player_id: str, 
        session_token: str, 
        websocket: WebSocket,
        ip: str
    ) -> Optional[WebSocket]:
        """
        Register a player's WebSocket.
        Returns old WebSocket if session was hijacked (same session, new connection).
        """
        old_socket = None
        
        # Check for session hijack (same session token connecting again)
        if session_token in self.session_connections:
            old_player_id = self.session_connections[session_token]
            if old_player_id in self.active_connections:
                old_socket = self.active_connections[old_player_id]
                # Remove old connection
                del self.active_connections[old_player_id]
        
        # Register new connection
        self.active_connections[player_id] = websocket
        self.session_connections[session_token] = player_id
        self.player_ips[player_id] = ip
        
        return old_socket
    
    def disconnect(self, player_id: str):
        """Remove a player's WebSocket connection."""
        if player_id in self.active_connections:
            del self.active_connections[player_id]
    
    def get_player_ip(self, player_id: str) -> str:
        """Get stored IP for a player."""
        return self.player_ips.get(player_id, "unknown")
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """Send message to a specific WebSocket."""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to send message: {e}")
    
    async def send_to_player(self, message: dict, player_id: str):
        """Send message to a player by ID."""
        if player_id in self.active_connections:
            try:
                await self.active_connections[player_id].send_json(message)
            except Exception:
                pass
    
    async def broadcast(self, message: dict, player_ids: List[str]):
        """Broadcast message to multiple players."""
        for pid in player_ids:
            await self.send_to_player(message, pid)
    
    async def broadcast_games_list(self):
        """Broadcast updated games list to all connected clients."""
        rooms = game_manager.get_open_rooms()
        message = {
            "type": MessageType.GAMES_LIST.value,
            "payload": {"games": rooms}
        }
        # Broadcast to all connected clients
        for socket in self.active_connections.values():
            try:
                await socket.send_json(message)
            except Exception:
                pass
    
    def start_background_tasks(self):
        """Start background cleanup tasks."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            log_game_event("", "🧹 Background cleanup task started", "Interval: 30s")
    
    async def _cleanup_loop(self):
        """Periodic cleanup of disconnected players."""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                await game_manager.cleanup_disconnected_players()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    def schedule_scoring_timeout(self, room_code: str, timeout_seconds: int):
        """Schedule a scoring timeout for a room."""
        # Cancel existing timeout for this room
        if room_code in self._scoring_timeout_tasks:
            self._scoring_timeout_tasks[room_code].cancel()
        
        async def timeout_handler():
            await asyncio.sleep(timeout_seconds)
            await self._handle_scoring_timeout(room_code)
        
        self._scoring_timeout_tasks[room_code] = asyncio.create_task(timeout_handler())
        log_game_event(room_code, "⏱️ Scoring timeout scheduled", f"{timeout_seconds}s")
    
    async def _handle_scoring_timeout(self, room_code: str):
        """Handle scoring timeout - force finalize and broadcast results."""
        room = game_manager.rooms.get(room_code)
        if not room or room.state != GameState.SCORING:
            return
        
        # Find who didn't submit
        submitted = set(room.current_round.scoring_votes.keys())
        not_submitted = [
            room.players[pid].name 
            for pid in room.connected_players.keys() 
            if pid not in submitted
        ]
        
        log_game_event(
            room_code, 
            "⏰ SCORING TIMEOUT", 
            f"Missing votes from: {', '.join(not_submitted) if not_submitted else 'none'}",
            level="warning"
        )
        
        success = await game_manager.force_finalize_scoring(room_code)
        if success:
            room = game_manager.rooms.get(room_code)
            await _broadcast_to_room(room, {
                "type": MessageType.ROUND_RESULTS.value,
                "payload": {
                    "round_scores": {
                        pid: {cat: float(score) for cat, score in scores.items()}
                        for pid, scores in room.current_round.scores.items()
                    },
                    "cumulative_scores": {
                        pid: float(p.score) for pid, p in room.players.items()
                    },
                    "is_final_round": len(room.history) >= 3,
                    "timeout": True
                }
            })
    
    def cancel_scoring_timeout(self, room_code: str):
        """Cancel a scheduled scoring timeout."""
        if room_code in self._scoring_timeout_tasks:
            self._scoring_timeout_tasks[room_code].cancel()
            del self._scoring_timeout_tasks[room_code]


manager = ConnectionManager()


# ============================================================================
# MAIN WEBSOCKET HANDLER
# ============================================================================

async def handle_websocket(websocket: WebSocket):
    """Main WebSocket handler with reconnection support."""
    await manager.connect(websocket)
    manager.start_background_tasks()
    
    client_ip = get_client_ip(websocket)
    player_id = None
    room_code = None
    session_token = None
    player_name = None
    
    # Note: We don't log player name here - it's unknown until JOIN_GAME/REJOIN_GAME
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg_dict = json.loads(data)
                msg_type = msg_dict.get("type")
                payload = msg_dict.get("payload", {})
                
                # --- REJOIN GAME (Reconnection) ---
                if msg_type == MessageType.REJOIN_GAME.value:
                    req = RejoinGamePayload(**payload)
                    session_token = req.session_token
                    
                    room, player = await game_manager.rejoin_room(session_token)
                    
                    if room and player:
                        player_id = player.id
                        room_code = room.code
                        player_name = player.name
                        
                        # Check for session hijack
                        old_socket = manager.register_player(player_id, session_token, websocket, client_ip)
                        if old_socket:
                            log_connection(
                                "SESSION HIJACKED", client_ip, player_name, room_code, 
                                "Closing old connection"
                            )
                            try:
                                await manager.send_personal_message({
                                    "type": MessageType.SESSION_HIJACKED.value,
                                    "payload": {"message": "Session opened in another tab"}
                                }, old_socket)
                                await old_socket.close()
                            except Exception:
                                pass
                        
                        log_connection(
                            "♻️ RECONNECTED", client_ip, player_name, room_code,
                            f"State: {room.state.value}"
                        )
                        
                        # Build full state response based on current game state
                        state_payload = await _build_reconnect_state(room, player)
                        
                        await manager.send_personal_message({
                            "type": MessageType.RECONNECTED.value,
                            "payload": state_payload
                        }, websocket)
                        
                        # Notify other players
                        other_ids = [pid for pid in room.players.keys() if pid != player_id]
                        await manager.broadcast({
                            "type": MessageType.PLAYER_RECONNECTED.value,
                            "payload": {
                                "player_id": player_id,
                                "player_name": player.name
                            }
                        }, other_ids)
                    else:
                        log_connection(
                            "❌ RECONNECT FAILED", client_ip, "", "",
                            "Session expired or room gone"
                        )
                        await manager.send_personal_message({
                            "type": MessageType.ERROR.value,
                            "payload": {
                                "message": "Could not reconnect. Session expired.",
                                "code": "SESSION_EXPIRED"
                            }
                        }, websocket)
                
                # --- JOIN/HOST GAME ---
                elif msg_type == MessageType.JOIN_GAME.value:
                    req = JoinGamePayload(**payload)
                    player_name = req.player_name
                    
                    # Get lobby settings if hosting
                    precise_scoring = payload.get("precise_scoring", False)
                    
                    if req.room_code:
                        # Joining existing room
                        room, player = await game_manager.join_room(req.room_code, req.player_name)
                        if room and player:
                            log_connection(
                                "➡️ JOINED ROOM", client_ip, player_name, room.code,
                                f"Players: {len(room.players)}/5"
                            )
                    else:
                        # Hosting new room
                        room, player = await game_manager.create_room(
                            req.player_name,
                            precise_scoring=precise_scoring
                        )
                        if room and player:
                            log_game_event(
                                room.code, 
                                "🏠 ROOM CREATED",
                                f"Host: '{player_name}' | IP: {client_ip}"
                            )
                    
                    if room and player:
                        player_id = player.id
                        room_code = room.code
                        session_token = player.session_token
                        
                        manager.register_player(player_id, session_token, websocket, client_ip)
                        
                        # Send lobby update to this player (includes session token)
                        await manager.send_personal_message({
                            "type": MessageType.LOBBY_UPDATE.value,
                            "payload": {
                                "room_code": room.code,
                                "is_host": player.is_host,
                                "session_token": player.session_token,
                                "players": [_player_to_dict(p) for p in room.players.values()],
                                "settings": {
                                    "precise_scoring": room.precise_scoring
                                }
                            }
                        }, websocket)
                        
                        # Notify others in room
                        await _broadcast_room_state(room)
                        
                        # Update games list for lobby browsers
                        await manager.broadcast_games_list()
                    else:
                        log_connection(
                            "❌ JOIN FAILED", client_ip, player_name, req.room_code or "NEW",
                            "Room full or doesn't exist"
                        )
                        await manager.send_personal_message({
                            "type": MessageType.ERROR.value,
                            "payload": {"message": "Could not join room. Room may be full or not exist."}
                        }, websocket)
                
                # --- GET GAMES LIST ---
                elif msg_type == MessageType.GET_GAMES.value:
                    rooms = game_manager.get_open_rooms()
                    await manager.send_personal_message({
                        "type": MessageType.GAMES_LIST.value,
                        "payload": {"games": rooms}
                    }, websocket)
                
                # --- START GAME ---
                elif msg_type == MessageType.START_GAME.value:
                    if not room_code:
                        continue
                    
                    rush_sec = payload.get("rush_seconds", 5)
                    precise = payload.get("precise_scoring")
                    
                    try:
                        rush_sec = int(rush_sec)
                    except (ValueError, TypeError):
                        rush_sec = 5
                    
                    new_round = await game_manager.start_round(
                        room_code, 
                        rush_seconds=rush_sec,
                        precise_scoring=precise
                    )
                    
                    if new_round:
                        room = game_manager.rooms[room_code]
                        player_names = [p.name for p in room.players.values()]
                        
                        log_game_event(
                            room_code,
                            f"🎬 ROUND {new_round.round_number} STARTED",
                            f"Letter: {new_round.letter} | Players: {', '.join(player_names)} | Rush: {rush_sec}s"
                        )
                        
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_START.value,
                            "payload": {
                                **new_round.model_dump(),
                                "rush_seconds": room.rush_seconds,
                                "server_time": room.round_start_time
                            }
                        })
                        await manager.broadcast_games_list()
                
                # --- SUBMIT ANSWERS ---
                elif msg_type == MessageType.SUBMIT_ANSWERS.value:
                    if not room_code or not player_id:
                        continue
                    
                    req = SubmitAnswersPayload(**payload)
                    
                    room = game_manager.rooms.get(room_code)
                    submitted_before = set(room.current_round.answers.keys()) if room and room.current_round else set()
                    is_first = len(submitted_before) == 0
                    
                    result = await game_manager.submit_answers(room_code, player_id, req.answers)
                    
                    # Count non-empty answers
                    non_empty = sum(1 for v in req.answers.values() if v.strip())
                    
                    log_action(
                        room_code, player_name,
                        "📝 SUBMITTED ANSWERS" + (" (FIRST! 🏆)" if is_first else ""),
                        f"Filled: {non_empty}/5"
                    )
                    
                    if result.get("opponent_submitted"):
                        room = game_manager.rooms[room_code]
                        submitted_ids = set(room.current_round.answers.keys())
                        target_ids = [
                            pid for pid in room.connected_players.keys() 
                            if pid not in submitted_ids
                        ]
                        
                        if target_ids:
                            waiting_names = [room.players[pid].name for pid in target_ids]
                            log_game_event(
                                room_code,
                                "⏳ RUSH MODE TRIGGERED",
                                f"Waiting for: {', '.join(waiting_names)}"
                            )
                            
                            await manager.broadcast({
                                "type": MessageType.OPPONENT_SUBMITTED.value,
                                "payload": {
                                    "opponent_id": player_id,
                                    "rush_seconds": room.rush_seconds
                                }
                            }, target_ids)
                    
                    if result.get("all_submitted"):
                        room = game_manager.rooms[room_code]
                        
                        log_game_event(
                            room_code,
                            "✅ ALL ANSWERS SUBMITTED",
                            "Moving to scoring phase"
                        )
                        
                        # Schedule scoring timeout
                        manager.schedule_scoring_timeout(room_code, game_manager.SCORING_TIMEOUT)
                        
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_ENDED.value,
                            "payload": {
                                "round": room.current_round.model_dump(),
                                "players": {pid: _player_to_dict(p) for pid, p in room.players.items()},
                                "scoring_deadline": room.scoring_deadline
                            }
                        })
                
                # --- SUBMIT SCORES ---
                elif msg_type == MessageType.SUBMIT_SCORES.value:
                    if not room_code or not player_id:
                        continue
                    
                    req = ScorePayload(**payload)
                    
                    log_action(room_code, player_name, "🗳️ SUBMITTED SCORES")
                    
                    finished = await game_manager.submit_scores(room_code, player_id, req.scores)
                    
                    if finished:
                        manager.cancel_scoring_timeout(room_code)
                        room = game_manager.rooms[room_code]
                        
                        # Calculate round winner
                        round_totals = {}
                        for pid, scores in room.current_round.scores.items():
                            round_totals[pid] = sum(scores.values())
                        
                        if round_totals:
                            winner_id = max(round_totals, key=round_totals.get)
                            winner_name = room.players[winner_id].name
                            winner_score = round_totals[winner_id]
                            
                            log_game_event(
                                room_code,
                                f"🏁 ROUND {room.current_round.round_number} COMPLETE",
                                f"Winner: '{winner_name}' (+{winner_score:.1f})"
                            )
                        
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_RESULTS.value,
                            "payload": {
                                "round_scores": {
                                    pid: {cat: float(score) for cat, score in scores.items()}
                                    for pid, scores in room.current_round.scores.items()
                                },
                                "cumulative_scores": {
                                    pid: float(p.score) for pid, p in room.players.items()
                                },
                                "is_final_round": len(room.history) >= 3
                            }
                        })
                
                # --- NEXT ROUND ---
                elif msg_type == MessageType.NEXT_ROUND.value:
                    if not room_code:
                        continue
                    
                    log_action(room_code, player_name, "▶️ STARTED NEXT ROUND")
                    
                    new_round = await game_manager.start_round(room_code)
                    if new_round:
                        room = game_manager.rooms[room_code]
                        
                        log_game_event(
                            room_code,
                            f"🎬 ROUND {new_round.round_number} STARTED",
                            f"Letter: {new_round.letter}"
                        )
                        
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_START.value,
                            "payload": {
                                **new_round.model_dump(),
                                "rush_seconds": room.rush_seconds,
                                "server_time": room.round_start_time
                            }
                        })
                
                # --- END GAME ---
                elif msg_type == MessageType.END_GAME.value:
                    if not room_code:
                        continue
                    
                    room = await game_manager.end_game(room_code)
                    if room:
                        # Determine overall winner
                        if room.players:
                            winner_id = max(room.players, key=lambda pid: room.players[pid].score)
                            winner = room.players[winner_id]
                            
                            log_game_event(
                                room_code,
                                "🏆 GAME OVER",
                                f"Winner: '{winner.name}' with {winner.score:.1f} points!"
                            )
                        
                        await _broadcast_to_room(room, {
                            "type": MessageType.GAME_OVER.value,
                            "payload": {
                                "history": [r.model_dump() for r in room.history],
                                "final_scores": {
                                    pid: float(p.score) for pid, p in room.players.items()
                                }
                            }
                        })
                
                # --- LEAVE GAME (intentional exit) ---
                elif msg_type == MessageType.LEAVE_GAME.value:
                    if player_id and room_code:
                        log_connection("🚪 LEFT GAME", client_ip, player_name, room_code)
                        
                        room = game_manager.rooms.get(room_code)
                        was_host = room and room.players.get(player_id, {}).is_host if room else False
                        
                        # Immediately remove (no grace period for intentional leave)
                        remaining_room = await game_manager.remove_player(player_id)
                        manager.disconnect(player_id)
                        
                        if remaining_room:
                            # Check if host migration needed
                            if was_host:
                                new_host = remaining_room.get_next_host()
                                if new_host:
                                    new_host.is_host = True
                                    log_game_event(
                                        room_code,
                                        "👑 HOST MIGRATED",
                                        f"'{player_name}' left → '{new_host.name}'"
                                    )
                                    await manager.broadcast({
                                        "type": MessageType.HOST_CHANGED.value,
                                        "payload": {
                                            "new_host_id": new_host.id,
                                            "new_host_name": new_host.name
                                        }
                                    }, list(remaining_room.connected_players.keys()))
                            
                            # Notify remaining players
                            await manager.broadcast({
                                "type": MessageType.PLAYER_DISCONNECTED.value,
                                "payload": {
                                    "player_id": player_id,
                                    "player_name": player_name,
                                    "left_intentionally": True
                                }
                            }, list(remaining_room.connected_players.keys()))
                            
                            await _broadcast_room_state(remaining_room)
                        
                        await manager.broadcast_games_list()
                        
                        # Reset local state
                        player_id = None
                        room_code = None
                        session_token = None
                        player_name = None
                
            except Exception as e:
                logger.error(f"❌ WebSocket Error [{room_code or 'NO_ROOM'}]: {e}")
                import traceback
                traceback.print_exc()
    
    except WebSocketDisconnect:
        if player_id:
            manager.disconnect(player_id)
            
            # Mark as disconnected (don't remove yet - grace period)
            room, disconnected, new_host = await game_manager.mark_player_disconnected(player_id)
            
            if room and disconnected:
                log_connection(
                    "❌ DISCONNECTED", client_ip, player_name or disconnected.name, room.code,
                    f"State: {room.state.value}"
                )
                
                # Notify others about disconnect
                other_ids = [pid for pid in room.connected_players.keys() if pid != player_id]
                
                await manager.broadcast({
                    "type": MessageType.PLAYER_DISCONNECTED.value,
                    "payload": {
                        "player_id": player_id,
                        "player_name": disconnected.name
                    }
                }, other_ids)
                
                # Notify about host change
                if new_host:
                    log_game_event(
                        room.code,
                        "👑 HOST MIGRATED",
                        f"'{disconnected.name}' → '{new_host.name}'"
                    )
                    
                    await manager.broadcast({
                        "type": MessageType.HOST_CHANGED.value,
                        "payload": {
                            "new_host_id": new_host.id,
                            "new_host_name": new_host.name
                        }
                    }, list(room.connected_players.keys()))
                
                # Update room state for remaining players
                await _broadcast_room_state(room)
            
            await manager.broadcast_games_list()
        else:
            log_connection("❌ DISCONNECTED (no room)", client_ip)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _build_reconnect_state(room, player) -> dict:
    """Build full state payload for reconnection."""
    base_state = {
        "room_code": room.code,
        "game_state": room.state.value,
        "is_host": player.is_host,
        "player_id": player.id,
        "session_token": player.session_token,
        "players": [_player_to_dict(p) for p in room.players.values()],
        "settings": {
            "rush_seconds": room.rush_seconds,
            "precise_scoring": room.precise_scoring
        }
    }
    
    # Include state-specific data
    if room.state == GameState.LOBBY:
        pass  # Base state is enough
    
    elif room.state == GameState.PLAYING:
        if room.current_round:
            base_state["round"] = room.current_round.model_dump()
            base_state["remaining_time"] = game_manager.get_remaining_time(room.code)
            # Include player's own submitted answers if any
            if player.id in room.current_round.answers:
                base_state["my_answers"] = room.current_round.answers[player.id]
    
    elif room.state == GameState.SCORING:
        if room.current_round:
            base_state["round"] = room.current_round.model_dump()
            base_state["scoring_remaining"] = game_manager.get_scoring_remaining_time(room.code)
            # Include if player already submitted scores
            base_state["scores_submitted"] = player.id in room.current_round.scoring_votes
    
    elif room.state == GameState.ROUND_RESULTS:
        if room.current_round:
            base_state["round_scores"] = {
                pid: {cat: float(score) for cat, score in scores.items()}
                for pid, scores in room.current_round.scores.items()
            }
            base_state["cumulative_scores"] = {
                pid: float(p.score) for pid, p in room.players.items()
            }
            base_state["is_final_round"] = len(room.history) >= 3
    
    elif room.state == GameState.FINAL_RESULTS:
        base_state["history"] = [r.model_dump() for r in room.history]
        base_state["final_scores"] = {
            pid: float(p.score) for pid, p in room.players.items()
        }
    
    return base_state


def _player_to_dict(player) -> dict:
    """Convert player to serializable dict for frontend."""
    return {
        "id": player.id,
        "name": player.name,
        "score": float(player.score),
        "is_host": player.is_host,
        "is_connected": player.is_connected
    }


async def _broadcast_to_room(room, message):
    """Broadcast message to all connected players in a room."""
    player_ids = list(room.connected_players.keys())
    await manager.broadcast(message, player_ids)


async def _broadcast_room_state(room):
    """Broadcast current lobby/room state to all players."""
    await _broadcast_to_room(room, {
        "type": MessageType.LOBBY_UPDATE.value,
        "payload": {
            "room_code": room.code,
            "players": [_player_to_dict(p) for p in room.players.values()],
            "settings": {
                "precise_scoring": room.precise_scoring
            }
        }
    })
