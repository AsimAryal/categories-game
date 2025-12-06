import random
import string
import uuid
import time
from typing import Dict, List, Optional, Tuple
from .models import Room, Player, Round, GameState

class GameManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {} # code -> Room
        self.player_room_map: Dict[str, str] = {} # player_id -> room_code
        self.CATEGORIES = [
            "Boy's Name", "Girl's Name", "Animal", "Country", "Food", 
            "Movie", "TV Show", "Color", "City", "Fruit/Vegetable",
            "Job", "Historical Figure", "Brand", "Sport", "Song Title",
            "Band/Musician", "School Subject", "Hobby", "Drink", "Car Brand"
        ]
        self.LETTERS = "ABCDEFGHIJKLMNOPRstuvw" # Exclude Q, X, Y, Z often tough

    def create_room(self, host_player_name: str) -> Tuple[Room, Player]:
        code = self._generate_room_code()
        host = Player(id=str(uuid.uuid4()), name=host_player_name, is_host=True)
        room = Room(code=code)
        room.players[host.id] = host
        self.rooms[code] = room
        self.player_room_map[host.id] = code
        return room, host

    def join_room(self, room_code: str, player_name: str) -> Tuple[Optional[Room], Optional[Player]]:
        room = self.rooms.get(room_code.upper())
        if not room:
            return None, None
        
        if len(room.players) >= 2:
            return None, None # Room full
            
        player = Player(id=str(uuid.uuid4()), name=player_name, is_host=False)
        room.players[player.id] = player
        self.player_room_map[player.id] = room_code
        return room, player

    def get_player_room(self, player_id: str) -> Optional[Room]:
        code = self.player_room_map.get(player_id)
        if code:
            return self.rooms.get(code)
        return None

    def remove_player(self, player_id: str):
        room = self.get_player_room(player_id)
        if room:
            if player_id in room.players:
                del room.players[player_id]
            del self.player_room_map[player_id]
            # If room empty, delete room
            if not room.players:
                del self.rooms[room.code]

    def start_round(self, room_code: str, rush_seconds: int = 5) -> Optional[Round]:
        room = self.rooms.get(room_code)
        if not room or len(room.players) != 2:
            return None
        
        # Update settings
        room.rush_seconds = max(5, rush_seconds) # Enforce min 5s
        
        round_num = len(room.history) + 1
        
        # Letter selection logic:
        # Filter out used letters
        available_letters = [l for l in self.LETTERS if l not in room.used_letters]
        
        if not available_letters:
            # All letters used, reset pool
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
        
        # Reset player round state
        for p in room.players.values():
            p.current_answers = {}
            p.current_round_scores = {}
            
        return new_round

    def submit_answers(self, room_code: str, player_id: str, answers: Dict[str, str]) -> Dict:
        """
        Returns status info: 
        {
            "all_submitted": bool,
            "opponent_submitted": bool
        }
        """
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return {"all_submitted": False}
            
        player = room.players.get(player_id)
        if player:
            player.current_answers = answers
            room.current_round.answers[player_id] = answers
            
        # Check if all active players submitted
        # valid_players = [p for p in room.players if not p.is_host] # If host plays? Host is a player.
        # We wait for ALL players in the room to submit.
        active_ids = set(room.players.keys())
        submitted_ids = set(room.current_round.answers.keys())
        
        # Intersection: How many CURRENT players have submitted?
        # If a player left, we shouldn't wait for them.
        current_submitted_count = len(active_ids.intersection(submitted_ids))
        
        if current_submitted_count >= len(active_ids) and len(active_ids) > 0:
            # Transition to SCORING
            room.state = GameState.SCORING
            return {"all_submitted": True, "opponent_submitted": False}
        
        return {"all_submitted": False, "opponent_submitted": True}

    def submit_scores(self, room_code: str, player_id: str, scores: Dict[str, Dict[str, int]]) -> bool:
        """
        scores structure: category -> { target_player_id -> score }
        Returns True if both have submitted scores and round is finalized.
        """
        room = self.rooms.get(room_code)
        if not room or not room.current_round:
            return False
            
        # Store individual scoring judgements if needed, but for simplicity
        # we will wait for both to submit 'scores' payload then reconcile.
        # Actually, simpler:
        # Each player validates both.
        # We store what 'player_id' thinks.
        
        # Let's trust the clients to send their own scores for the OTHER player?
        # Specification says: "Each client sends its scoring decisions... server reconciles"
        # Let's store raw scoring votes.
        
        if not hasattr(room.current_round, 'scoring_votes'):
            room.current_round.scoring_votes = {} # player_id -> scores_payload
            
        room.current_round.scoring_votes[player_id] = scores
        
        active_ids = set(room.players.keys())
        submitted_ids = set(room.current_round.scoring_votes.keys())
        current_submitted_count = len(active_ids.intersection(submitted_ids))
        
        if current_submitted_count >= len(active_ids) and len(active_ids) > 0:
            self._finalize_round_scores(room)
            room.state = GameState.ROUND_RESULTS
            return True
            
        return False

    def _finalize_round_scores(self, room: Room):
        # Reconcile logic: Take minimum score for each cell
        # Support N players (robustness)
        
        # Init score storage
        for pid in room.players.keys():
            room.current_round.scores[pid] = {}
            
        for cat in room.current_round.categories:
            # For each target player 'target_pid', calculate consensus score
            for target_pid in room.players.keys():
                votes = []
                for voter_id in room.players.keys():
                    # Check what voter_id voted for target_pid in category cat
                    # Structure: vote_payload[cat][target_pid]
                    voter_votes_payload = room.current_round.scoring_votes.get(voter_id, {})
                    cat_votes = voter_votes_payload.get(cat, {})
                    score = cat_votes.get(target_pid, 0)
                    votes.append(score)
                
                # If single player, min is just their own vote. If multiplayer, strict min prevents cheating/conflict.
                final_score = min(votes) if votes else 0
                room.current_round.scores[target_pid][cat] = final_score
                
        # Update accumulators
        for pid, player in room.players.items():
            round_total = sum(room.current_round.scores[pid].values())
            player.score += round_total
            
        # Archive round
        room.history.append(room.current_round)

    def _generate_room_code(self) -> str:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

# Create strict singleton or just instantiate in main
game_manager = GameManager()
