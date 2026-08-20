import asyncio
import logging
import math
import random
import string
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .models import GameState, Player, Room, Round
from .persistence import game_store

logger = logging.getLogger("uvicorn.error")


class GameActionError(Exception):
    """A safe, client-facing rejection of a gameplay action."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class GameManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}
        self.player_room_map: Dict[str, str] = {}
        self.session_player_map: Dict[str, str] = {}
        self._room_locks: Dict[str, asyncio.Lock] = {}

        self.CATEGORIES = [
            "Boy's Name",
            "Girl's Name",
            "Animal",
            "Country",
            "Food",
            "Movie",
            "TV Show",
            "Color",
            "City",
            "Fruit/Vegetable",
            "Job",
            "Historical Figure",
            "Brand",
            "Sport",
            "Song Title",
            "Band/Musician",
            "School Subject",
            "Hobby",
            "Drink",
            "Car Brand",
        ]
        self.LETTERS = "ABCDEFGHIJKLMNOPRSTU"
        self.LOBBY_GRACE_PERIOD = 30
        self.GAME_GRACE_PERIOD = 300
        self.SCORING_TIMEOUT = 60

    async def initialize(self):
        await game_store.initialize()
        await game_store.cleanup_old_rooms()
        self.rooms.clear()
        self.player_room_map.clear()
        self.session_player_map.clear()
        self._room_locks.clear()
        logger.info("=" * 60)
        logger.info("🎮 GAME MANAGER INITIALIZING")
        logger.info("=" * 60)
        active_rooms = await game_store.get_all_active_rooms()

        if not active_rooms:
            logger.info("📭 No existing rooms found in database")
        else:
            logger.info(f"📦 Found {len(active_rooms)} room(s) in database")

        disconnected_at = time.time()
        for room_data in active_rooms:
            try:
                room = Room.model_validate(room_data["data"])
                if room.state == GameState.PLAYING and room.current_round:
                    if not room.starts_at:
                        room.starts_at = room.round_start_time or disconnected_at
                    if room.round_deadline is None:
                        room.round_deadline = room.starts_at + room.round_duration_seconds
                self.rooms[room.code] = room
                for pid, player in room.players.items():
                    # Process restarts invalidate every in-memory WebSocket.
                    player.is_connected = False
                    player.disconnect_time = disconnected_at
                    self.player_room_map[pid] = room.code
                    if player.session_token:
                        self.session_player_map[player.session_token] = pid
                    await self._persist_player(player, room.code)
                await self._persist_room(room)

                player_names = [p.name for p in room.players.values()]

                logger.info(
                    f"   ├── [{room.code}] State: {room.state.value} | "
                    f"Players: {', '.join(player_names)} | "
                    f"Connected: 0/{len(room.players)}"
                )
            except Exception as e:
                logger.error(f"   ├── ❌ Failed to load room {room_data['code']}: {e}")

        logger.info("=" * 60)
        logger.info(f"✅ GameManager ready with {len(self.rooms)} active room(s)")
        logger.info("=" * 60)

    async def create_room(
        self, host_player_name: str, precise_scoring: bool = False
    ) -> Tuple[Room, Player]:
        host_player_name = self._validate_player_name(host_player_name)
        code = self._generate_room_code()
        session_token = self._generate_session_token()

        host = Player(
            id=str(uuid.uuid4()),
            name=host_player_name,
            is_host=True,
            session_token=session_token,
            is_connected=True,
            join_order=0,
        )

        room = Room(code=code, precise_scoring=precise_scoring, next_join_order=1)
        room.players[host.id] = host
        self.rooms[code] = room
        self.player_room_map[host.id] = code
        self.session_player_map[session_token] = host.id
        await self._persist_room(room)
        await self._persist_player(host, code)

        return room, host

    async def join_room(self, room_code: str, player_name: str) -> Tuple[Room, Player]:
        player_name = self._validate_player_name(player_name)
        room = self.rooms.get(room_code.upper())
        if not room:
            raise GameActionError("ROOM_NOT_FOUND", "Room does not exist.")

        async with self._lock_for(room.code):
            if room.state != GameState.LOBBY:
                raise GameActionError("GAME_ALREADY_STARTED", "This game has already started.")
            if len(room.players) >= 5:
                raise GameActionError("ROOM_FULL", "This room is full.")

            session_token = self._generate_session_token()
            player = Player(
                id=str(uuid.uuid4()),
                name=player_name,
                is_host=False,
                session_token=session_token,
                is_connected=True,
                join_order=room.next_join_order,
            )

            room.next_join_order += 1
            room.players[player.id] = player
            self.player_room_map[player.id] = room.code
            self.session_player_map[session_token] = player.id
            await self._persist_room(room)
            await self._persist_player(player, room.code)
            return room, player

    async def rejoin_room(self, session_token: str) -> Tuple[Optional[Room], Optional[Player]]:
        player_id = self.session_player_map.get(session_token)
        if not player_id:
            player_data = await game_store.get_player_by_session(session_token)
            if not player_data:
                return None, None
            player_id = player_data["player_id"]

        room_code = self.player_room_map.get(player_id)
        if not room_code:
            player_data = await game_store.get_player_by_session(session_token)
            if not player_data:
                return None, None
            room_code = player_data["room_code"]

        room = self.rooms.get(room_code)
        if not room:
            return None, None

        player = room.players.get(player_id)
        if not player:
            return None, None

        async with self._lock_for(room.code):
            player.is_connected = True
            player.disconnect_time = None
            self.session_player_map[session_token] = player_id
            self.player_room_map[player_id] = room_code

            current_host = room.players.get(room.host_id) if room.host_id else None
            if not current_host or not current_host.is_connected:
                for candidate in room.players.values():
                    candidate.is_host = candidate.id == player.id

            for candidate in room.players.values():
                await self._persist_player(candidate, room.code)
            await self._persist_room(room)
            return room, player

    def get_player_room(self, player_id: str) -> Optional[Room]:
        code = self.player_room_map.get(player_id)
        if code:
            return self.rooms.get(code)
        return None

    async def mark_player_disconnected(
        self, player_id: str
    ) -> Tuple[Optional[Room], Optional[Player], Optional[Player]]:
        room = self.get_player_room(player_id)
        if not room:
            return None, None, None

        player = room.players.get(player_id)
        if not player:
            return None, None, None

        async with self._lock_for(room.code):
            if not player.is_connected:
                return room, player, None

            player.is_connected = False
            player.disconnect_time = time.time()

            new_host = None
            if player.is_host:
                next_host = room.get_next_host()
                if next_host:
                    player.is_host = False
                    next_host.is_host = True
                    new_host = next_host

            await self._persist_player(player, room.code)
            if new_host:
                await self._persist_player(new_host, room.code)
            await self._persist_room(room)
            return room, player, new_host

    async def remove_player(self, player_id: str) -> Optional[Room]:
        room = self.get_player_room(player_id)
        if not room:
            return None

        async with self._lock_for(room.code):
            player = room.players.get(player_id)
            if not player:
                return room

            was_host = player.is_host
            self.session_player_map.pop(player.session_token, None)
            self.player_room_map.pop(player_id, None)
            del room.players[player_id]

            if not room.players:
                logger.info(f"🗑️ [{room.code}] Room deleted (empty)")
                del self.rooms[room.code]
                self._room_locks.pop(room.code, None)
                await game_store.delete_room(room.code)
                return None

            if was_host:
                candidates = sorted(
                    room.players.values(),
                    key=lambda candidate: (
                        not candidate.is_connected,
                        candidate.join_order,
                    ),
                )
                next_host = candidates[0]
                for candidate in room.players.values():
                    candidate.is_host = candidate.id == next_host.id

            await self._persist_room(room)
            for candidate in room.players.values():
                await self._persist_player(candidate, room.code)
            await game_store.delete_player(player_id)
            return room

    async def start_round(
        self,
        room_code: str,
        actor_id: str,
        expected_state: GameState,
        rush_seconds: Optional[int] = None,
        precise_scoring: Optional[bool] = None,
    ) -> Round:
        room = self.rooms.get(room_code)
        if not room:
            raise GameActionError("ROOM_NOT_FOUND", "Room does not exist.")

        async with self._lock_for(room.code):
            self._require_host(room, actor_id)
            if room.state != expected_state:
                raise GameActionError(
                    "INVALID_STATE",
                    f"Cannot start a round while the game is {room.state.value}.",
                )

            connected = room.connected_players
            if len(connected) < 2:
                raise GameActionError(
                    "NOT_ENOUGH_PLAYERS",
                    "At least two connected players are required.",
                )

            if rush_seconds is not None:
                room.rush_seconds = max(5, min(120, rush_seconds))
            if precise_scoring is not None:
                room.precise_scoring = precise_scoring

            round_num = len(room.history) + 1
            available_letters = [
                letter for letter in self.LETTERS if letter not in room.used_letters
            ]
            if not available_letters:
                room.used_letters = []
                available_letters = list(self.LETTERS)

            letter = random.choice(available_letters).upper()
            room.used_letters.append(letter)
            categories = random.sample(self.CATEGORIES, 5)
            new_round = Round(
                round_number=round_num,
                letter=letter,
                categories=categories,
            )

            now = time.time()
            room.starts_at = now + 3
            room.round_deadline = room.starts_at + room.round_duration_seconds
            room.round_start_time = room.starts_at
            room.scoring_deadline = None
            room.current_round = new_round
            room.state = GameState.PLAYING

            for player in room.players.values():
                player.current_answers = {}
                player.current_round_scores = {}
            await self._persist_room(room)
            for player in room.players.values():
                await self._persist_player(player, room.code)
            return new_round

    def get_open_rooms(self) -> List[Dict]:
        open_rooms = []
        for code, room in self.rooms.items():
            if room.state == GameState.LOBBY and len(room.players) < 5:
                host_name = "Unknown"
                if room.host_id and room.host_id in room.players:
                    host_name = room.players[room.host_id].name

                open_rooms.append(
                    {"code": code, "host_name": host_name, "player_count": len(room.players)}
                )
        return open_rooms

    async def submit_answers(
        self, room_code: str, player_id: str, answers: Dict[str, str]
    ) -> Dict[str, Any]:
        room = self.rooms.get(room_code)
        if not room:
            raise GameActionError("ROOM_NOT_FOUND", "Room does not exist.")

        async with self._lock_for(room.code):
            if room.state != GameState.PLAYING or not room.current_round:
                raise GameActionError("INVALID_STATE", "Answers are not being accepted now.")

            player = room.players.get(player_id)
            if not player or not player.is_connected:
                raise GameActionError("PLAYER_NOT_CONNECTED", "Player is not connected.")
            if player_id in room.current_round.answers:
                raise GameActionError(
                    "ANSWERS_ALREADY_SUBMITTED", "Answers were already submitted."
                )

            now = time.time()
            if now < room.starts_at:
                raise GameActionError("ROUND_NOT_STARTED", "The round countdown is still running.")
            if room.round_deadline is not None and now >= room.round_deadline:
                round_ended = await self._expire_round_locked(room)
                return {
                    "accepted": False,
                    "error_code": "ROUND_CLOSED",
                    "error_message": "The round deadline has passed.",
                    "round_ended": round_ended,
                }

            unknown_categories = set(answers) - set(room.current_round.categories)
            if unknown_categories:
                raise GameActionError(
                    "INVALID_CATEGORY",
                    "Answers contain a category that is not in the current round.",
                )
            if any(len(answer) > 64 for answer in answers.values()):
                raise GameActionError(
                    "ANSWER_TOO_LONG", "Answers may contain at most 64 characters."
                )

            first_submission = not room.current_round.answers
            if first_submission and room.round_deadline is not None:
                room.round_deadline = min(
                    room.round_deadline,
                    now + room.rush_seconds,
                )

            clean_answers = dict(answers)
            player.current_answers = clean_answers
            room.current_round.answers[player_id] = clean_answers

            connected_ids = set(room.connected_players)
            submitted_ids = set(room.current_round.answers)
            all_submitted = bool(connected_ids) and connected_ids <= submitted_ids
            if all_submitted:
                self._begin_scoring(room, now)

            await self._persist_room(room)
            await self._persist_player(player, room.code)
            return {
                "accepted": True,
                "all_submitted": all_submitted,
                "opponent_submitted": not all_submitted,
                "first_submission": first_submission,
                "round_deadline": room.round_deadline,
                "submitted_ids": list(submitted_ids),
                "scoring_deadline": room.scoring_deadline,
            }

    async def expire_round(
        self,
        room_code: str,
        expected_deadline: Optional[float] = None,
    ) -> bool:
        room = self.rooms.get(room_code)
        if not room:
            return False

        async with self._lock_for(room.code):
            if room.state != GameState.PLAYING or not room.current_round:
                return False
            if expected_deadline is not None and room.round_deadline != expected_deadline:
                return False
            if room.round_deadline is not None and time.time() < room.round_deadline:
                return False
            return await self._expire_round_locked(room)

    async def _expire_round_locked(self, room: Room) -> bool:
        if room.state != GameState.PLAYING or not room.current_round:
            return False

        changed_players = []
        for player_id, player in room.connected_players.items():
            if player_id not in room.current_round.answers:
                empty_answers = {category: "" for category in room.current_round.categories}
                player.current_answers = empty_answers
                room.current_round.answers[player_id] = empty_answers
                changed_players.append(player)

        self._begin_scoring(room, time.time())
        await self._persist_room(room)
        for player in changed_players:
            await self._persist_player(player, room.code)
        return True

    @staticmethod
    def _begin_scoring(room: Room, now: float):
        room.state = GameState.SCORING
        if room.scoring_timeout_seconds:
            room.scoring_deadline = now + room.scoring_timeout_seconds
        else:
            room.scoring_deadline = None

    async def submit_scores(
        self, room_code: str, player_id: str, scores: Dict[str, Dict[str, int]]
    ) -> Dict[str, Any]:
        room = self.rooms.get(room_code)
        if not room:
            raise GameActionError("ROOM_NOT_FOUND", "Room does not exist.")

        async with self._lock_for(room.code):
            if room.state != GameState.SCORING or not room.current_round:
                raise GameActionError("INVALID_STATE", "Scores are not being accepted now.")

            player = room.players.get(player_id)
            if not player or not player.is_connected:
                raise GameActionError("PLAYER_NOT_CONNECTED", "Player is not connected.")

            now = time.time()
            if room.scoring_deadline is not None and now >= room.scoring_deadline:
                self._finalize_round_scores(room)
                room.state = GameState.ROUND_RESULTS
                room.scoring_deadline = None
                await self._persist_room(room)
                for room_player in room.players.values():
                    await self._persist_player(room_player, room.code)
                return {
                    "accepted": False,
                    "finished": True,
                    "timed_out": True,
                }

            if player_id in room.current_round.scoring_votes:
                raise GameActionError("SCORES_ALREADY_SUBMITTED", "Scores were already submitted.")

            valid_categories = set(room.current_round.categories)
            valid_targets = set(room.current_round.answers) & set(room.players)
            for category, target_scores in scores.items():
                if category not in valid_categories:
                    raise GameActionError(
                        "INVALID_CATEGORY",
                        "Scores contain a category that is not in the current round.",
                    )
                for target_id, score in target_scores.items():
                    if target_id not in valid_targets:
                        raise GameActionError(
                            "INVALID_TARGET",
                            "Scores contain an invalid player target.",
                        )
                    if score not in (0, 1, 2):
                        raise GameActionError(
                            "INVALID_SCORE",
                            "Scores must be 0, 1, or 2.",
                        )

            room.current_round.scoring_votes[player_id] = {
                category: dict(target_scores) for category, target_scores in scores.items()
            }
            connected_ids = set(room.connected_players)
            submitted_ids = set(room.current_round.scoring_votes)
            finished = bool(connected_ids) and connected_ids <= submitted_ids

            if finished:
                self._finalize_round_scores(room)
                room.state = GameState.ROUND_RESULTS
                room.scoring_deadline = None

            await self._persist_room(room)
            if finished:
                for room_player in room.players.values():
                    await self._persist_player(room_player, room.code)
            return {
                "accepted": True,
                "finished": finished,
                "timed_out": False,
                "submitted_ids": list(submitted_ids),
            }

    async def force_finalize_scoring(
        self,
        room_code: str,
        expected_deadline: Optional[float] = None,
    ) -> bool:
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return False

        async with self._lock_for(room.code):
            if room.state != GameState.SCORING:
                return False
            if expected_deadline is not None and room.scoring_deadline != expected_deadline:
                return False
            if room.scoring_deadline is not None and time.time() < room.scoring_deadline:
                return False

            self._finalize_round_scores(room)
            room.state = GameState.ROUND_RESULTS
            room.scoring_deadline = None
            await self._persist_room(room)
            for room_player in room.players.values():
                await self._persist_player(room_player, room.code)
            return True

    async def advance_after_roster_change(
        self,
        room_code: str,
    ) -> Optional[GameState]:
        """Advance a phase when a disconnect removes the last pending player."""
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return None

        async with self._lock_for(room.code):
            connected_ids = set(room.connected_players)
            if not connected_ids:
                return None

            if room.state == GameState.PLAYING and connected_ids <= set(room.current_round.answers):
                self._begin_scoring(room, time.time())
                await self._persist_room(room)
                return GameState.SCORING

            if room.state == GameState.SCORING and connected_ids <= set(
                room.current_round.scoring_votes
            ):
                self._finalize_round_scores(room)
                room.state = GameState.ROUND_RESULTS
                room.scoring_deadline = None
                await self._persist_room(room)
                for room_player in room.players.values():
                    await self._persist_player(room_player, room.code)
                return GameState.ROUND_RESULTS

            return None

    def _finalize_round_scores(self, room: Room):
        current_round = room.current_round
        submitted_voter_ids = set(current_round.scoring_votes.keys())

        for pid in room.players.keys():
            current_round.scores[pid] = {}

        for cat in current_round.categories:
            for target_pid in room.players.keys():
                total_points = 0.0
                vote_count = 0

                for voter_id in submitted_voter_ids:
                    if voter_id == target_pid:
                        continue
                    voter_votes = current_round.scoring_votes.get(voter_id, {})
                    cat_votes = voter_votes.get(cat, {})

                    if target_pid in cat_votes:
                        total_points += cat_votes[target_pid]
                        vote_count += 1

                if vote_count > 0:
                    if room.precise_scoring:
                        final_score = total_points / vote_count
                    else:
                        final_score = float(round(total_points / vote_count))
                else:
                    final_score = 0.0
                current_round.scores[target_pid][cat] = final_score

        for pid, player in room.players.items():
            round_total = sum(current_round.scores[pid].values())
            player.score += round_total
        room.history.append(current_round)

    async def end_game(self, room_code: str, actor_id: str) -> Room:
        room = self.rooms.get(room_code)
        if not room:
            raise GameActionError("ROOM_NOT_FOUND", "Room does not exist.")

        async with self._lock_for(room.code):
            self._require_host(room, actor_id)
            if room.state != GameState.ROUND_RESULTS:
                raise GameActionError(
                    "INVALID_STATE",
                    "The game can only end after round results.",
                )
            room.state = GameState.FINAL_RESULTS
            room.scoring_deadline = None
            await self._persist_room(room)
            return room

    async def update_settings(
        self,
        room_code: str,
        actor_id: str,
        rush_seconds: Optional[int] = None,
        precise_scoring: Optional[bool] = None,
        scoring_timeout_seconds: Optional[int] = None,
        round_duration_seconds: Optional[int] = None,
    ) -> Room:
        room = self.rooms.get(room_code)
        if not room:
            raise GameActionError("ROOM_NOT_FOUND", "Room does not exist.")

        async with self._lock_for(room.code):
            self._require_host(room, actor_id)
            if room.state != GameState.LOBBY:
                raise GameActionError(
                    "INVALID_STATE",
                    "Settings can only be updated in the lobby.",
                )

            if rush_seconds is not None:
                room.rush_seconds = max(5, min(120, rush_seconds))
            if precise_scoring is not None:
                room.precise_scoring = precise_scoring
            if scoring_timeout_seconds is not None:
                room.scoring_timeout_seconds = (
                    max(10, min(300, scoring_timeout_seconds))
                    if scoring_timeout_seconds > 0
                    else None
                )
            if round_duration_seconds is not None:
                room.round_duration_seconds = max(30, min(120, round_duration_seconds))
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

    def _lock_for(self, room_code: str) -> asyncio.Lock:
        lock = self._room_locks.get(room_code)
        if lock is None:
            lock = asyncio.Lock()
            self._room_locks[room_code] = lock
        return lock

    @staticmethod
    def _validate_player_name(player_name: str) -> str:
        clean_name = player_name.strip()
        if not clean_name or len(clean_name) > 24:
            raise GameActionError(
                "INVALID_PLAYER_NAME",
                "Player names must contain 1 to 24 nonblank characters.",
            )
        return clean_name

    @staticmethod
    def _require_host(room: Room, actor_id: str):
        player = room.players.get(actor_id)
        if not player or not player.is_connected:
            raise GameActionError("PLAYER_NOT_CONNECTED", "Player is not connected.")
        if not player.is_host:
            raise GameActionError("HOST_ONLY", "Only the host can perform this action.")

    async def _persist_room(self, room: Room):
        """Persist room state to database."""
        await game_store.save_room(
            room_code=room.code, state=room.state.value, room_data=room.model_dump()
        )

    async def _persist_player(self, player: Player, room_code: str):
        await game_store.save_player(
            player_id=player.id,
            session_token=player.session_token,
            room_code=room_code,
            name=player.name,
            is_host=player.is_host,
            join_order=player.join_order,
            score=player.score,
            is_connected=player.is_connected,
            disconnect_time=player.disconnect_time,
            player_data=player.model_dump(),
        )

    def _generate_room_code(self) -> str:
        """Generate unique 4-character room code."""
        while True:
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            if code not in self.rooms:
                return code

    def _generate_session_token(self) -> str:
        return str(uuid.uuid4())

    def get_remaining_time(self, room_code: str) -> int:
        room = self.rooms.get(room_code)
        if not room or room.round_deadline is None:
            return room.round_duration_seconds if room else 60
        return max(0, math.ceil(room.round_deadline - time.time()))

    def get_scoring_remaining_time(self, room_code: str) -> int:
        room = self.rooms.get(room_code)
        if not room or not room.scoring_deadline:
            return 0
        return max(0, math.ceil(room.scoring_deadline - time.time()))


# Singleton instance
game_manager = GameManager()
