from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, List
import json
import logging
from .models import MessageType, BaseMessage, JoinGamePayload, SubmitAnswersPayload, ScorePayload, GameState
from .manager import game_manager

logger = logging.getLogger("uvicorn.error")

class ConnectionManager:
    def __init__(self):
        # player_id -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()

    def disconnect(self, player_id: str):
        if player_id in self.active_connections:
            del self.active_connections[player_id]

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, message: dict, player_ids: List[str]):
        for pid in player_ids:
            if pid in self.active_connections:
                try:
                    await self.active_connections[pid].send_json(message)
                except:
                    pass

    async def broadcast_games_list(self):
        rooms = game_manager.get_open_rooms()
        message = {
            "type": MessageType.GAMES_LIST,
            "payload": {"games": rooms}
        }
        # Broadcast to ALL connected clients (lobby and in-game, simpler)
        # OR just iterate active_connections
        for socket in self.active_connections.values():
            try:
                await socket.send_json(message)
            except:
                pass


manager = ConnectionManager()

async def handle_websocket(websocket: WebSocket):
    await manager.connect(websocket)
    player_id = None
    room_code = None
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                # Parse base message
                msg_dict = json.loads(data)
                msg_type = msg_dict.get("type")
                payload = msg_dict.get("payload", {})
                
                # --- HANDLERS ---
                
                if msg_type == MessageType.JOIN_GAME:
                    # Special case: not yet in a room
                    req = JoinGamePayload(**payload)
                    if req.room_code:
                        # Joining existing
                        room, player = game_manager.join_room(req.room_code, req.player_name)
                    else:
                        # Hosting new
                        room, player = game_manager.create_room(req.player_name)
                    
                    if room and player:
                        player_id = player.id
                        room_code = room.code
                        manager.active_connections[player_id] = websocket
                        
                        # Notify this player
                        await manager.send_personal_message({
                            "type": MessageType.LOBBY_UPDATE,
                            "payload": {
                                "room_code": room.code,
                                "is_host": player.is_host,
                                "players": [p.dict() for p in room.players.values()]
                            }
                        }, websocket)
                        
                        # Notify others in room
                        await _broadcast_room_state(room)
                    else:
                        await manager.send_personal_message({
                            "type": MessageType.ERROR,
                            "payload": {"message": "Could not join room."}
                        }, websocket)

                elif msg_type == MessageType.START_GAME:
                    if not room_code: continue
                    # Extract config
                    rush_sec = payload.get("rush_seconds", 5)
                    try:
                        rush_sec = int(rush_sec)
                    except:
                        rush_sec = 5
                        
                    new_round = game_manager.start_round(room_code, rush_seconds=rush_sec)
                    if new_round:
                        room = game_manager.rooms[room_code]
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_START,
                            "payload": {
                                **new_round.dict(),
                                "rush_seconds": room.rush_seconds
                            } 
                        })

                # --- GET GAMES ---
                elif msg_type == MessageType.GET_GAMES:
                    rooms = game_manager.get_open_rooms()
                    await manager.send_personal_message(
                        {
                            "type": MessageType.GAMES_LIST,
                            "payload": {"games": rooms}
                        },
                        websocket
                    )

                elif msg_type == MessageType.SUBMIT_ANSWERS:
                    if not room_code or not player_id: continue
                    req = SubmitAnswersPayload(**payload)
                    result = game_manager.submit_answers(room_code, player_id, req.answers)
                    
                    if result["opponent_submitted"]:
                        # Notify ONLY the OTHER player (who has not submitted)
                        room = game_manager.rooms[room_code]
                        # Find opponent
                        opponent_id = [pid for pid in room.players if pid != player_id][0]
                        
                        await manager.broadcast({
                            "type": MessageType.OPPONENT_SUBMITTED,
                            "payload": {
                                "opponent_id": player_id,
                                "rush_seconds": room.rush_seconds
                            }
                        }, [opponent_id])
                        
                    if result["all_submitted"]:
                        room = game_manager.rooms[room_code]
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_ENDED,
                            "payload": {
                                "round": room.current_round.dict(),
                                "players": {pid: p.dict() for pid, p in room.players.items()}
                            }
                        })

                elif msg_type == MessageType.SUBMIT_SCORES:
                    if not room_code or not player_id: continue
                    req = ScorePayload(**payload)
                    finished = game_manager.submit_scores(room_code, player_id, req.scores)
                    
                    if finished:
                        room = game_manager.rooms[room_code]
                        await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_RESULTS,
                            "payload": {
                                "round_scores": room.current_round.scores,
                                "cumulative_scores": {pid: p.score for pid, p in room.players.items()},
                                "is_final_round": len(room.history) >= 3 # Check if we want to enable end game
                            }
                        })

                elif msg_type == MessageType.NEXT_ROUND:
                    # Host request to start next round
                    if not room_code: continue
                    new_round = game_manager.start_round(room_code)
                    if new_round:
                         room = game_manager.rooms[room_code]
                         await _broadcast_to_room(room, {
                            "type": MessageType.ROUND_START,
                            "payload": new_round.dict()
                        })

                elif msg_type == MessageType.END_GAME:
                    # Simply broadcast game over with history
                    if not room_code: continue
                    room = game_manager.rooms[room_code]
                    room.state = GameState.FINAL_RESULTS
                    await _broadcast_to_room(room, {
                        "type": MessageType.GAME_OVER,
                        "payload": {
                            "history": [r.dict() for r in room.history],
                            "final_scores": {pid: p.score for pid, p in room.players.items()}
                        }
                    })

            except Exception as e:
                logger.error(f"WebSocket Error: {e}")
                import traceback
                traceback.print_exc()
                
    except WebSocketDisconnect:
        if player_id:
            manager.disconnect(player_id)
            game_manager.remove_player(player_id)
            # Notify remaining players
            # In a real game we might want to pause or wait, here we just maybe kill the room or notify
            # If room still exists (maybe other player is there)
            if room_code and room_code in game_manager.rooms:
                room = game_manager.rooms[room_code]
                await _broadcast_room_state(room)
            
            await manager.broadcast_games_list()

async def _broadcast_to_room(room, message):
    player_ids = list(room.players.keys())
    await manager.broadcast(message, player_ids)

async def _broadcast_room_state(room):
    await _broadcast_to_room(room, {
        "type": MessageType.LOBBY_UPDATE,
        "payload": {
            "room_code": room.code,
            "players": [p.dict() for p in room.players.values()]
        }
    })
