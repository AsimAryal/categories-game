import enum
from typing import List, Dict, Optional
from pydantic import BaseModel
import uuid

class MessageType(str, enum.Enum):
    # CLIENT -> SERVER
    JOIN_GAME = "JOIN_GAME"
    START_GAME = "START_GAME"
    SUBMIT_ANSWERS = "SUBMIT_ANSWERS"
    SUBMIT_SCORES = "SUBMIT_SCORES"
    NEXT_ROUND = "NEXT_ROUND"
    END_GAME = "END_GAME"
    # SERVER -> CLIENT
    LOBBY_UPDATE = "LOBBY_UPDATE"
    ROUND_START = "ROUND_START"
    OPPONENT_SUBMITTED = "OPPONENT_SUBMITTED"
    ROUND_ENDED = "ROUND_ENDED"  # Transition to scoring
    SCORING_UPDATE = "SCORING_UPDATE" # Not explicitly needed if we just send ROUND_ENDED with data? actually we need a separate "RESULTS" state
    ROUND_RESULTS = "ROUND_RESULTS"
    GAME_OVER = "GAME_OVER"
    ERROR = "ERROR"

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
    room_code: Optional[str] = None # If None, host a new game
    player_name: str

class SubmitAnswersPayload(BaseModel):
    answers: Dict[str, str] # category -> answer

class ScorePayload(BaseModel):
    # category -> {player_id -> score}
    scores: Dict[str, Dict[str, int]]

# --- Internal Game Models ---

class Player(BaseModel):
    id: str
    name: str
    score: int = 0
    is_host: bool = False
    
    # Per-round state
    current_answers: Dict[str, str] = {}
    current_round_scores: Dict[str, int] = {} 

class Round(BaseModel):
    round_number: int
    letter: str
    categories: List[str]
    # Storage for history
    # player_id -> {category -> answer_text}
    answers: Dict[str, Dict[str, str]] = {}
    # player_id -> {category -> score}
    scores: Dict[str, Dict[str, int]] = {}
    # player_id -> {category -> {target_pid -> score}}
    scoring_votes: Dict[str, Dict[str, Dict[str, int]]] = {}
    
class Room(BaseModel):
    code: str
    players: Dict[str, Player] = {} # id -> Player
    state: GameState = GameState.LOBBY
    
    current_round: Optional[Round] = None
    history: List[Round] = []
    used_letters: List[str] = []
    rush_seconds: int = 5
    
    # Timer management (server side validation mainly)
    round_start_time: float = 0
    
    @property
    def host_id(self) -> Optional[str]:
        for p in self.players.values():
            if p.is_host:
                return p.id
        return None
