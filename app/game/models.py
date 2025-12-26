import enum
from typing import List, Dict, Optional
from pydantic import BaseModel
import uuid

class MessageType(str, enum.Enum):
    # CLIENT -> SERVER
    JOIN_GAME = "JOIN_GAME"
    REJOIN_GAME = "REJOIN_GAME"  # Reconnection with session token
    LEAVE_GAME = "LEAVE_GAME"  # Intentional leave (vs disconnect)
    START_GAME = "START_GAME"
    SUBMIT_ANSWERS = "SUBMIT_ANSWERS"
    SUBMIT_SCORES = "SUBMIT_SCORES"
    NEXT_ROUND = "NEXT_ROUND"
    END_GAME = "END_GAME"
    GET_GAMES = "GET_GAMES"  # Client requests list
    
    # SERVER -> CLIENT
    LOBBY_UPDATE = "LOBBY_UPDATE"
    GAMES_LIST = "GAMES_LIST"  # Server sends list
    ROUND_START = "ROUND_START"
    OPPONENT_SUBMITTED = "OPPONENT_SUBMITTED"
    ROUND_ENDED = "ROUND_ENDED"  # Transition to scoring
    SCORING_UPDATE = "SCORING_UPDATE"
    ROUND_RESULTS = "ROUND_RESULTS"
    GAME_OVER = "GAME_OVER"
    ERROR = "ERROR"
    
    # Settings sync
    UPDATE_SETTINGS = "UPDATE_SETTINGS"
    
    # Reconnection & session messages
    RECONNECTED = "RECONNECTED"  # Successful reconnection response
    SESSION_HIJACKED = "SESSION_HIJACKED"  # Another tab took over session
    
    # Player status messages
    PLAYER_DISCONNECTED = "PLAYER_DISCONNECTED"
    PLAYER_RECONNECTED = "PLAYER_RECONNECTED"
    HOST_CHANGED = "HOST_CHANGED"
    
    # Timeout messages
    SCORING_TIMEOUT = "SCORING_TIMEOUT"  # Warn scoring about to auto-complete

class GameState(str, enum.Enum):
    LOBBY = "LOBBY"
    PLAYING = "PLAYING"
    SCORING = "SCORING"
    ROUND_RESULTS = "ROUND_RESULTS"
    FINAL_RESULTS = "FINAL_RESULTS"

# --- WebSocket Payloads ---

class BaseMessage(BaseModel):
    type: MessageType
    payload: Optional[Dict] = None

class JoinGamePayload(BaseModel):
    room_code: Optional[str] = None  # If None, host a new game
    player_name: str

class RejoinGamePayload(BaseModel):
    session_token: str

class SubmitAnswersPayload(BaseModel):
    answers: Dict[str, str]  # category -> answer

class ScorePayload(BaseModel):
    # category -> {player_id -> score}
    scores: Dict[str, Dict[str, int]]

# --- Internal Game Models ---

class Player(BaseModel):
    id: str
    name: str
    score: float = 0  # Changed to float for precise scoring option
    is_host: bool = False
    
    # Session & connection state
    session_token: str = ""  # Unique token for reconnection
    is_connected: bool = True  # False when WebSocket disconnected
    join_order: int = 0  # For deterministic host migration
    disconnect_time: Optional[float] = None  # Timestamp when disconnected
    
    # Per-round state
    current_answers: Dict[str, str] = {}
    current_round_scores: Dict[str, float] = {}  # Changed to float

class Round(BaseModel):
    round_number: int
    letter: str
    categories: List[str]
    # Storage for history
    # player_id -> {category -> answer_text}
    answers: Dict[str, Dict[str, str]] = {}
    # player_id -> {category -> score}
    scores: Dict[str, Dict[str, float]] = {}  # Changed to float
    # player_id -> {category -> {target_pid -> score}}
    scoring_votes: Dict[str, Dict[str, Dict[str, int]]] = {}
    
class Room(BaseModel):
    code: str
    players: Dict[str, Player] = {}  # id -> Player
    state: GameState = GameState.LOBBY
    
    current_round: Optional[Round] = None
    history: List[Round] = []
    used_letters: List[str] = []
    rush_seconds: int = 5
    
    # Timer management
    round_start_time: float = 0
    scoring_deadline: Optional[float] = None  # Auto-submit deadline
    
    # Lobby settings
    precise_scoring: bool = False  # Optional float scoring
    
    # Track join order for host migration
    next_join_order: int = 0
    
    @property
    def host_id(self) -> Optional[str]:
        for p in self.players.values():
            if p.is_host:
                return p.id
        return None
    
    @property
    def connected_players(self) -> Dict[str, "Player"]:
        """Return only currently connected players."""
        return {pid: p for pid, p in self.players.items() if p.is_connected}
    
    def get_next_host(self) -> Optional["Player"]:
        """Find next connected player by join order for host migration."""
        connected = [p for p in self.players.values() if p.is_connected and not p.is_host]
        if not connected:
            return None
        # Sort by join order, pick first
        connected.sort(key=lambda p: p.join_order)
        return connected[0]

