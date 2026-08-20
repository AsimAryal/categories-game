import enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

PlayerName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=24),
]
AnswerText = Annotated[str, StringConstraints(max_length=64)]
ScoreValue = Literal[0, 1, 2]


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
    model_config = ConfigDict(extra="forbid")

    type: MessageType
    payload: Dict[str, Any] = Field(default_factory=dict)


class JoinGamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_code: Optional[str] = None
    player_name: PlayerName
    precise_scoring: bool = False


class RejoinGamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]


class SubmitAnswersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: Dict[str, AnswerText] = Field(default_factory=dict)


class ScorePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: Dict[str, Dict[str, ScoreValue]] = Field(default_factory=dict)


class StartGamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rush_seconds: Optional[int] = Field(default=None, ge=5, le=120)
    precise_scoring: Optional[bool] = None


class UpdateSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rush_seconds: Optional[int] = Field(default=None, ge=5, le=120)
    precise_scoring: Optional[bool] = None
    scoring_timeout_seconds: Optional[int] = Field(default=None, ge=0, le=300)
    round_duration_seconds: Optional[int] = Field(default=None, ge=30, le=120)

    @field_validator("scoring_timeout_seconds")
    @classmethod
    def validate_scoring_timeout(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and 0 < value < 10:
            raise ValueError("scoring timeout must be zero or at least 10 seconds")
        return value


class EmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Player(BaseModel):
    id: str
    name: str
    score: float = 0
    is_host: bool = False
    session_token: str = ""
    is_connected: bool = True
    join_order: int = 0
    disconnect_time: Optional[float] = None
    current_answers: Dict[str, str] = Field(default_factory=dict)
    current_round_scores: Dict[str, float] = Field(default_factory=dict)


class Round(BaseModel):
    round_number: int
    letter: str
    categories: List[str]
    answers: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    scores: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    scoring_votes: Dict[str, Dict[str, Dict[str, int]]] = Field(default_factory=dict)


class Room(BaseModel):
    code: str
    players: Dict[str, Player] = Field(default_factory=dict)
    state: GameState = GameState.LOBBY
    current_round: Optional[Round] = None
    history: List[Round] = Field(default_factory=list)
    used_letters: List[str] = Field(default_factory=list)
    rush_seconds: int = 5
    starts_at: float = 0
    round_deadline: Optional[float] = None
    # Kept for compatibility with room JSON written by older server versions.
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
