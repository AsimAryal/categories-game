import enum
from typing import List, Dict, Optional
from pydantic import BaseModel


class MessageType(str, enum.Enum):
    JOIN_GAME = "JOIN_GAME"
    REJOIN_GAME = "REJOIN_GAME"
    LEAVE_GAME = "LEAVE_GAME"
    START_GAME = "START_GAME"
    SUBMIT_ANSWERS = "SUBMIT_ANSWERS"
    SUBMIT_SCORES = "SUBMIT_SCORES"
    NEXT_ROUND = "NEXT_ROUND"
    END_GAME = "END_GAME"
    GET_GAMES = "GET_GAMES"
    LOBBY_UPDATE = "LOBBY_UPDATE"
    GAMES_LIST = "GAMES_LIST"
    ROUND_START = "ROUND_START"
    OPPONENT_SUBMITTED = "OPPONENT_SUBMITTED"
    ROUND_ENDED = "ROUND_ENDED"
    SCORING_UPDATE = "SCORING_UPDATE"
    ROUND_RESULTS = "ROUND_RESULTS"
    GAME_OVER = "GAME_OVER"
    ERROR = "ERROR"
    UPDATE_SETTINGS = "UPDATE_SETTINGS"
    RECONNECTED = "RECONNECTED"
    SESSION_HIJACKED = "SESSION_HIJACKED"
    PLAYER_DISCONNECTED = "PLAYER_DISCONNECTED"
    PLAYER_RECONNECTED = "PLAYER_RECONNECTED"
    HOST_CHANGED = "HOST_CHANGED"
    SCORING_TIMEOUT = "SCORING_TIMEOUT"

class GameState(str, enum.Enum):
    LOBBY = "LOBBY"
    PLAYING = "PLAYING"
    SCORING = "SCORING"
    ROUND_RESULTS = "ROUND_RESULTS"
    FINAL_RESULTS = "FINAL_RESULTS"


class BaseMessage(BaseModel):
    type: MessageType
    payload: Optional[Dict] = None


class JoinGamePayload(BaseModel):
    room_code: Optional[str] = None
    player_name: str


class RejoinGamePayload(BaseModel):
    session_token: str


class SubmitAnswersPayload(BaseModel):
    answers: Dict[str, str]


class ScorePayload(BaseModel):
    scores: Dict[str, Dict[str, int]]


class Player(BaseModel):
    id: str
    name: str
    score: float = 0
    is_host: bool = False
    session_token: str = ""
    is_connected: bool = True
    join_order: int = 0
    disconnect_time: Optional[float] = None
    current_answers: Dict[str, str] = {}
    current_round_scores: Dict[str, float] = {}


class Round(BaseModel):
    round_number: int
    letter: str
    categories: List[str]
    answers: Dict[str, Dict[str, str]] = {}
    scores: Dict[str, Dict[str, float]] = {}
    scoring_votes: Dict[str, Dict[str, Dict[str, int]]] = {}


class Room(BaseModel):
    code: str
    players: Dict[str, Player] = {}
    state: GameState = GameState.LOBBY
    current_round: Optional[Round] = None
    history: List[Round] = []
    used_letters: List[str] = []
    rush_seconds: int = 5
    round_start_time: float = 0
    scoring_deadline: Optional[float] = None
    precise_scoring: bool = False
    scoring_timeout_seconds: Optional[int] = None
    round_duration_seconds: int = 60
    next_join_order: int = 0

    @property
    def host_id(self) -> Optional[str]:
        for p in self.players.values():
            if p.is_host:
                return p.id
        return None

    @property
    def connected_players(self) -> Dict[str, "Player"]:
        return {pid: p for pid, p in self.players.items() if p.is_connected}

    def get_next_host(self) -> Optional["Player"]:
        connected = [p for p in self.players.values() if p.is_connected and not p.is_host]
        if not connected:
            return None
        connected.sort(key=lambda p: p.join_order)
        return connected[0]

