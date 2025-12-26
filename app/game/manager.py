"""
Game Manager with persistent storage and reconnection support.
"""

import random
import string
import uuid
import time
import asyncio
from typing import Dict, List, Optional, Tuple, Any
from .models import Room, Player, Round, GameState
from .persistence import game_store
import logging

logger = logging.getLogger("uvicorn.error")


class GameManager:
    """
    Manages game rooms with SQLite persistence for reconnection support.
    All state-mutating operations are persisted to the database.
    """
    
    def __init__(self):
        self.rooms: Dict[str, Room] = {}  # code -> Room
        self.player_room_map: Dict[str, str] = {}  # player_id -> room_code
        self.session_player_map: Dict[str, str] = {}  # session_token -> player_id
        
        self.CATEGORIES = [
            "Boy's Name", "Girl's Name", "Animal", "Country", "Food", 
            "Movie", "TV Show", "Color", "City", "Fruit/Vegetable",
            "Job", "Historical Figure", "Brand", "Sport", "Song Title",
            "Band/Musician", "School Subject", "Hobby", "Drink", "Car Brand"
        ]
        self.LETTERS = "ABCDEFGHIJKLMNOPRSTU"  # Exclude Q, V, W, X, Y, Z
        
        # Grace periods (in seconds)
        self.LOBBY_GRACE_PERIOD = 30  # 30s for lobby disconnects
        self.GAME_GRACE_PERIOD = 300  # 5 min for in-game disconnects
        self.SCORING_TIMEOUT = 60  # 60s to submit scores
    
    async def initialize(self):
        """Load existing rooms from database on startup."""
        await game_store.initialize()
        
        logger.info("=" * 60)
        logger.info("🎮 GAME MANAGER INITIALIZING")
        logger.info("=" * 60)
        
        # Load active rooms
        active_rooms = await game_store.get_all_active_rooms()
        
        if not active_rooms:
            logger.info("📭 No existing rooms found in database")
        else:
            logger.info(f"📦 Found {len(active_rooms)} room(s) in database")
            
        for room_data in active_rooms:
            try:
                room = Room.model_validate(room_data["data"])
                self.rooms[room.code] = room
                
                # Rebuild player maps
                for pid, player in room.players.items():
                    self.player_room_map[pid] = room.code
                    if player.session_token:
                        self.session_player_map[player.session_token] = pid
                
                player_names = [p.name for p in room.players.values()]
                connected = sum(1 for p in room.players.values() if p.is_connected)
                
                logger.info(
                    f"   ├── [{room.code}] State: {room.state.value} | "
                    f"Players: {', '.join(player_names)} | "
                    f"Connected: {connected}/{len(room.players)}"
                )
            except Exception as e:
                logger.error(f"   ├── ❌ Failed to load room {room_data['code']}: {e}")
        
        logger.info("=" * 60)
        logger.info(f"✅ GameManager ready with {len(self.rooms)} active room(s)")
        logger.info("=" * 60)
    
    async def create_room(
        self, 
        host_player_name: str,
        precise_scoring: bool = False
    ) -> Tuple[Room, Player]:
        """Create a new room with the host player."""
        code = self._generate_room_code()
        session_token = self._generate_session_token()
        
        host = Player(
            id=str(uuid.uuid4()),
            name=host_player_name,
            is_host=True,
            session_token=session_token,
            is_connected=True,
            join_order=0
        )
        
        room = Room(
            code=code,
            precise_scoring=precise_scoring,
            next_join_order=1
        )
        room.players[host.id] = host
        
        # Store in memory
        self.rooms[code] = room
        self.player_room_map[host.id] = code
        self.session_player_map[session_token] = host.id
        
        # Persist to database
        await self._persist_room(room)
        await self._persist_player(host, code)
        
        return room, host
    
    async def join_room(
        self, 
        room_code: str, 
        player_name: str
    ) -> Tuple[Optional[Room], Optional[Player]]:
        """Join an existing room."""
        room = self.rooms.get(room_code.upper())
        if not room:
            return None, None
        
        if room.state != GameState.LOBBY:
            return None, None  # Can't join game in progress
        
        if len(room.players) >= 5:
            return None, None  # Room full
        
        session_token = self._generate_session_token()
        
        player = Player(
            id=str(uuid.uuid4()),
            name=player_name,
            is_host=False,
            session_token=session_token,
            is_connected=True,
            join_order=room.next_join_order
        )
        
        room.next_join_order += 1
        room.players[player.id] = player
        
        # Store in memory
        self.player_room_map[player.id] = room_code.upper()
        self.session_player_map[session_token] = player.id
        
        # Persist
        await self._persist_room(room)
        await self._persist_player(player, room_code.upper())
        
        return room, player
    
    async def rejoin_room(
        self, 
        session_token: str
    ) -> Tuple[Optional[Room], Optional[Player]]:
        """
        Rejoin a room using session token.
        Returns (room, player) if successful, (None, None) if not found.
        """
        # First check in-memory
        player_id = self.session_player_map.get(session_token)
        
        if not player_id:
            # Check database
            player_data = await game_store.get_player_by_session(session_token)
            if not player_data:
                return None, None
            player_id = player_data["player_id"]
        
        room_code = self.player_room_map.get(player_id)
        if not room_code:
            # Try to get from database
            player_data = await game_store.get_player_by_session(session_token)
            if not player_data:
                return None, None
            room_code = player_data["room_code"]
        
        room = self.rooms.get(room_code)
        if not room:
            # Room might have been cleaned up
            return None, None
        
        player = room.players.get(player_id)
        if not player:
            return None, None
        
        # Mark as connected
        player.is_connected = True
        player.disconnect_time = None
        
        # Update maps
        self.session_player_map[session_token] = player_id
        self.player_room_map[player_id] = room_code
        
        # Persist connection status
        await game_store.mark_player_connected(player_id)
        
        return room, player
    
    def get_player_room(self, player_id: str) -> Optional[Room]:
        """Get the room a player is in."""
        code = self.player_room_map.get(player_id)
        if code:
            return self.rooms.get(code)
        return None
    
    async def mark_player_disconnected(
        self, 
        player_id: str
    ) -> Tuple[Optional[Room], Optional[Player], Optional[Player]]:
        """
        Mark a player as disconnected (not removed yet).
        Returns (room, disconnected_player, new_host) - new_host is set if host migration occurred.
        """
        room = self.get_player_room(player_id)
        if not room:
            return None, None, None
        
        player = room.players.get(player_id)
        if not player:
            return None, None, None
        
        # Mark as disconnected
        player.is_connected = False
        player.disconnect_time = time.time()
        
        # Persist
        await game_store.mark_player_disconnected(player_id)
        
        new_host = None
        
        # Check if host migration needed
        if player.is_host:
            next_host = room.get_next_host()
            if next_host:
                # Transfer host
                player.is_host = False
                next_host.is_host = True
                new_host = next_host
                
                # Persist host changes
                await self._persist_player(player, room.code)
                await self._persist_player(next_host, room.code)
                await self._persist_room(room)
        
        return room, player, new_host
    
    async def remove_player(self, player_id: str) -> Optional[Room]:
        """
        Actually remove a player from the game (after grace period).
        Returns the room if it still exists, None if room was also deleted.
        """
        room = self.get_player_room(player_id)
        if not room:
            return None
        
        player = room.players.get(player_id)
        if player:
            # Clean up session token mapping
            if player.session_token in self.session_player_map:
                del self.session_player_map[player.session_token]
            
            # Remove from room
            del room.players[player_id]
        
        # Clean up player_room_map
        if player_id in self.player_room_map:
            del self.player_room_map[player_id]
        
        # Persist deletion
        await game_store.delete_player(player_id)
        
        # If room empty, delete room
        if not room.players:
            logger.info(f"🗑️ [{room.code}] Room deleted (empty)")
            del self.rooms[room.code]
            await game_store.delete_room(room.code)
            return None
        
        # If all remaining players disconnected, keep room but don't update
        await self._persist_room(room)
        return room
    
    async def start_round(
        self, 
        room_code: str, 
        rush_seconds: int = 5,
        precise_scoring: Optional[bool] = None
    ) -> Optional[Round]:
        """Start a new round in the room."""
        room = self.rooms.get(room_code)
        if not room:
            return None
        
        # Need at least 2 connected players
        connected = room.connected_players
        if len(connected) < 2:
            return None
        
        # Update settings if provided
        room.rush_seconds = max(5, rush_seconds)
        if precise_scoring is not None:
            room.precise_scoring = precise_scoring
        
        round_num = len(room.history) + 1
        
        # Letter selection
        available_letters = [l for l in self.LETTERS if l not in room.used_letters]
        if not available_letters:
            room.used_letters = []
            available_letters = list(self.LETTERS)
        
        letter = random.choice(available_letters).upper()
        room.used_letters.append(letter)
        
        categories = random.sample(self.CATEGORIES, 5)
        
        new_round = Round(
            round_number=round_num,
            letter=letter,
            categories=categories
        )
        
        room.current_round = new_round
        room.state = GameState.PLAYING
        room.round_start_time = time.time()
        room.scoring_deadline = None
        
        # Reset player round state
        for p in room.players.values():
            p.current_answers = {}
            p.current_round_scores = {}
        
        # Persist
        await self._persist_room(room)
        
        return new_round
    
    def get_open_rooms(self) -> List[Dict]:
        """Get list of joinable rooms (LOBBY state, < 5 players)."""
        open_rooms = []
        for code, room in self.rooms.items():
            if room.state == GameState.LOBBY and len(room.players) < 5:
                host_name = "Unknown"
                if room.host_id and room.host_id in room.players:
                    host_name = room.players[room.host_id].name
                
                open_rooms.append({
                    "code": code,
                    "host_name": host_name,
                    "player_count": len(room.players)
                })
        return open_rooms
    
    async def submit_answers(
        self, 
        room_code: str, 
        player_id: str, 
        answers: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Submit answers for current round.
        Returns status info for broadcasting.
        """
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return {"all_submitted": False, "opponent_submitted": False}
        
        player = room.players.get(player_id)
        if player:
            player.current_answers = answers
            room.current_round.answers[player_id] = answers
        
        # Check if all CONNECTED players submitted
        connected_ids = set(room.connected_players.keys())
        submitted_ids = set(room.current_round.answers.keys())
        
        connected_submitted = connected_ids.intersection(submitted_ids)
        
        if len(connected_submitted) >= len(connected_ids) and len(connected_ids) > 0:
            # All connected players submitted - transition to scoring
            room.state = GameState.SCORING
            room.scoring_deadline = time.time() + self.SCORING_TIMEOUT
            
            await self._persist_room(room)
            return {"all_submitted": True, "opponent_submitted": False}
        
        await self._persist_room(room)
        return {"all_submitted": False, "opponent_submitted": True}
    
    async def submit_scores(
        self, 
        room_code: str, 
        player_id: str, 
        scores: Dict[str, Dict[str, int]]
    ) -> bool:
        """
        Submit scoring votes.
        Returns True if all (connected) players submitted and round is finalized.
        """
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return False
        
        room.current_round.scoring_votes[player_id] = scores
        
        # Check if all CONNECTED players submitted
        connected_ids = set(room.connected_players.keys())
        submitted_ids = set(room.current_round.scoring_votes.keys())
        
        connected_submitted = connected_ids.intersection(submitted_ids)
        
        if len(connected_submitted) >= len(connected_ids) and len(connected_ids) > 0:
            await self._finalize_round_scores(room)
            room.state = GameState.ROUND_RESULTS
            await self._persist_room(room)
            return True
        
        return False
    
    async def force_finalize_scoring(self, room_code: str) -> bool:
        """
        Force finalize scoring when timeout is reached.
        Only counts votes from players who actually submitted.
        """
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return False
        
        if room.state != GameState.SCORING:
            return False
        
        await self._finalize_round_scores(room)
        room.state = GameState.ROUND_RESULTS
        await self._persist_room(room)
        return True
    
    async def _finalize_round_scores(self, room: Room):
        """Calculate final scores from votes (excluding non-submitters)."""
        current_round = room.current_round
        
        # Get players who actually submitted scores
        submitted_voter_ids = set(current_round.scoring_votes.keys())
        
        # Init score storage for all players
        for pid in room.players.keys():
            current_round.scores[pid] = {}
        
        for cat in current_round.categories:
            for target_pid in room.players.keys():
                total_points = 0.0
                vote_count = 0
                
                for voter_id in submitted_voter_ids:
                    if voter_id == target_pid:
                        continue  # Don't count self-votes
                    
                    voter_votes = current_round.scoring_votes.get(voter_id, {})
                    cat_votes = voter_votes.get(cat, {})
                    
                    if target_pid in cat_votes:
                        total_points += cat_votes[target_pid]
                        vote_count += 1
                
                # Calculate average
                if vote_count > 0:
                    if room.precise_scoring:
                        final_score = total_points / vote_count
                    else:
                        final_score = float(round(total_points / vote_count))
                else:
                    final_score = 0.0
                
                current_round.scores[target_pid][cat] = final_score
        
        # Update cumulative scores
        for pid, player in room.players.items():
            round_total = sum(current_round.scores[pid].values())
            player.score += round_total
            # Sync to players table column and player_data JSON
            await self._persist_player(player, room.code)
        
        # Archive round
        room.history.append(current_round)
    
    async def end_game(self, room_code: str) -> Optional[Room]:
        """End the game and set final results state."""
        room = self.rooms.get(room_code)
        if not room:
            return None
        
        room.state = GameState.FINAL_RESULTS
        await self._persist_room(room)
        return room

    async def update_settings(
        self, 
        room_code: str, 
        rush_seconds: Optional[int] = None,
        precise_scoring: Optional[bool] = None
    ) -> Optional[Room]:
        """Update room settings and persist."""
        room = self.rooms.get(room_code)
        if not room:
            return None
            
        if rush_seconds is not None:
            room.rush_seconds = max(5, rush_seconds)
        if precise_scoring is not None:
            room.precise_scoring = precise_scoring
            
        await self._persist_room(room)
        return room
    
    async def cleanup_disconnected_players(self):
        """
        Background task to remove players who exceeded grace period.
        Should be called periodically.
        """
        now = time.time()
        rooms_to_check = list(self.rooms.values())
        
        for room in rooms_to_check:
            players_to_remove = []
            
            for player in room.players.values():
                if not player.is_connected and player.disconnect_time:
                    # Determine grace period based on game state
                    if room.state == GameState.LOBBY:
                        grace_period = self.LOBBY_GRACE_PERIOD
                    else:
                        grace_period = self.GAME_GRACE_PERIOD
                    
                    elapsed = now - player.disconnect_time
                    if elapsed > grace_period:
                        players_to_remove.append((player.id, player.name, elapsed, grace_period))
            
            for player_id, player_name, elapsed, grace in players_to_remove:
                logger.info(
                    f"🧹 [{room.code}] Removing '{player_name}' | "
                    f"Disconnected for {int(elapsed)}s (grace: {grace}s)"
                )
                await self.remove_player(player_id)
    
    async def _persist_room(self, room: Room):
        """Persist room state to database."""
        await game_store.save_room(
            room_code=room.code,
            state=room.state.value,
            room_data=room.model_dump()
        )
    
    async def _persist_player(self, player: Player, room_code: str):
        """Persist player to database."""
        await game_store.save_player(
            player_id=player.id,
            session_token=player.session_token,
            room_code=room_code,
            name=player.name,
            is_host=player.is_host,
            join_order=player.join_order,
            score=player.score,
            player_data=player.model_dump()
        )
    
    def _generate_room_code(self) -> str:
        """Generate unique 4-character room code."""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
            if code not in self.rooms:
                return code
    
    def _generate_session_token(self) -> str:
        """Generate unique session token for player reconnection."""
        return str(uuid.uuid4())
    
    def get_remaining_time(self, room_code: str, total_seconds: int = 60) -> int:
        """Get remaining time for current round."""
        room = self.rooms.get(room_code)
        if not room or not room.round_start_time:
            return total_seconds
        
        elapsed = time.time() - room.round_start_time
        remaining = max(0, total_seconds - int(elapsed))
        return remaining
    
    def get_scoring_remaining_time(self, room_code: str) -> int:
        """Get remaining time for scoring phase."""
        room = self.rooms.get(room_code)
        if not room or not room.scoring_deadline:
            return self.SCORING_TIMEOUT
        
        remaining = max(0, int(room.scoring_deadline - time.time()))
        return remaining


# Singleton instance
game_manager = GameManager()
