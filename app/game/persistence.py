"""
SQLite-based persistence for game state.
Enables player reconnection and server restart recovery.
"""

import aiosqlite
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger("uvicorn.error")

# Database file location - in project root for easy access
DB_PATH = Path(__file__).parent.parent.parent / "game_data.db"


class GameStore:
    """
    Async SQLite store for persisting game state.
    Uses JSON serialization for complex nested structures.
    """
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._initialized = False
    
    async def initialize(self):
        """Create tables if they don't exist."""
        if self._initialized:
            return
            
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrent access
            await db.execute("PRAGMA journal_mode=WAL")
            
            # Rooms table - stores serialized room state
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rooms (
                    code TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    data TEXT NOT NULL
                )
            """)
            
            # Players table - for session token lookups
            await db.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    player_id TEXT PRIMARY KEY,
                    session_token TEXT UNIQUE NOT NULL,
                    room_code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    is_host INTEGER NOT NULL DEFAULT 0,
                    is_connected INTEGER NOT NULL DEFAULT 1,
                    join_order INTEGER NOT NULL DEFAULT 0,
                    disconnect_time REAL,
                    score REAL NOT NULL DEFAULT 0,
                    data TEXT,
                    FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
                )
            """)
            
            # Index for fast session token lookups
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_token 
                ON players(session_token)
            """)
            
            # Index for room lookups
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_room_code 
                ON players(room_code)
            """)
            
            await db.commit()
            
        self._initialized = True
        logger.info(f"GameStore initialized at {self.db_path}")
    
    async def save_room(self, room_code: str, state: str, room_data: Dict[str, Any]):
        """
        Save or update a room's state.
        room_data should be the full room dict (from room.model_dump()).
        """
        await self.initialize()
        now = time.time()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO rooms (code, state, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at,
                    data = excluded.data
            """, (room_code, state, now, now, json.dumps(room_data)))
            await db.commit()
    
    async def load_room(self, room_code: str) -> Optional[Dict[str, Any]]:
        """Load a room's data by code."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT data FROM rooms WHERE code = ?", (room_code,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row["data"])
        return None
    
    async def delete_room(self, room_code: str):
        """Delete a room and all associated players."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM players WHERE room_code = ?", (room_code,))
            await db.execute("DELETE FROM rooms WHERE code = ?", (room_code,))
            await db.commit()
    
    async def save_player(
        self,
        player_id: str,
        session_token: str,
        room_code: str,
        name: str,
        is_host: bool,
        join_order: int,
        score: float = 0,
        player_data: Optional[Dict] = None
    ):
        """Save or update a player record."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO players (
                    player_id, session_token, room_code, name, 
                    is_host, is_connected, join_order, score, data
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    name = excluded.name,
                    is_host = excluded.is_host,
                    score = excluded.score,
                    data = excluded.data
            """, (
                player_id, session_token, room_code, name,
                1 if is_host else 0, join_order, score,
                json.dumps(player_data) if player_data else None
            ))
            await db.commit()
    
    async def get_player_by_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        """Look up a player by their session token."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM players WHERE session_token = ?", (session_token,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "player_id": row["player_id"],
                        "session_token": row["session_token"],
                        "room_code": row["room_code"],
                        "name": row["name"],
                        "is_host": bool(row["is_host"]),
                        "is_connected": bool(row["is_connected"]),
                        "join_order": row["join_order"],
                        "disconnect_time": row["disconnect_time"],
                        "score": row["score"],
                        "data": json.loads(row["data"]) if row["data"] else None
                    }
        return None
    
    async def get_player_by_id(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Look up a player by their player ID."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "player_id": row["player_id"],
                        "session_token": row["session_token"],
                        "room_code": row["room_code"],
                        "name": row["name"],
                        "is_host": bool(row["is_host"]),
                        "is_connected": bool(row["is_connected"]),
                        "join_order": row["join_order"],
                        "disconnect_time": row["disconnect_time"],
                        "score": row["score"],
                        "data": json.loads(row["data"]) if row["data"] else None
                    }
        return None
    
    async def mark_player_disconnected(self, player_id: str):
        """Mark a player as disconnected with timestamp."""
        await self.initialize()
        now = time.time()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE players 
                SET is_connected = 0, disconnect_time = ?
                WHERE player_id = ?
            """, (now, player_id))
            await db.commit()
    
    async def mark_player_connected(self, player_id: str):
        """Mark a player as connected (clear disconnect time)."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE players 
                SET is_connected = 1, disconnect_time = NULL
                WHERE player_id = ?
            """, (player_id,))
            await db.commit()
    
    async def update_player_host(self, player_id: str, is_host: bool):
        """Update a player's host status."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE players SET is_host = ? WHERE player_id = ?
            """, (1 if is_host else 0, player_id))
            await db.commit()
    
    async def delete_player(self, player_id: str):
        """Remove a player from the database."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM players WHERE player_id = ?", (player_id,))
            await db.commit()
    
    async def get_room_players(self, room_code: str) -> List[Dict[str, Any]]:
        """Get all players in a room."""
        await self.initialize()
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM players WHERE room_code = ? ORDER BY join_order",
                (room_code,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [{
                    "player_id": row["player_id"],
                    "session_token": row["session_token"],
                    "room_code": row["room_code"],
                    "name": row["name"],
                    "is_host": bool(row["is_host"]),
                    "is_connected": bool(row["is_connected"]),
                    "join_order": row["join_order"],
                    "disconnect_time": row["disconnect_time"],
                    "score": row["score"]
                } for row in rows]
    
    async def get_all_active_rooms(self) -> List[Dict[str, Any]]:
        """Get all rooms that haven't expired (for server restart recovery)."""
        await self.initialize()
        
        # Consider rooms active if updated in last 24 hours
        cutoff = time.time() - (24 * 60 * 60)
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT code, state, data FROM rooms WHERE updated_at > ?",
                (cutoff,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [{
                    "code": row["code"],
                    "state": row["state"],
                    "data": json.loads(row["data"])
                } for row in rows]
    
    async def cleanup_old_rooms(self, max_age_hours: int = 24):
        """Remove rooms older than max_age_hours."""
        await self.initialize()
        cutoff = time.time() - (max_age_hours * 60 * 60)
        
        async with aiosqlite.connect(self.db_path) as db:
            # First get room codes to delete
            async with db.execute(
                "SELECT code FROM rooms WHERE updated_at < ?", (cutoff,)
            ) as cursor:
                rows = await cursor.fetchall()
                codes = [row[0] for row in rows]
            
            if codes:
                # Delete players first (foreign key)
                placeholders = ",".join("?" * len(codes))
                await db.execute(
                    f"DELETE FROM players WHERE room_code IN ({placeholders})",
                    codes
                )
                await db.execute(
                    f"DELETE FROM rooms WHERE code IN ({placeholders})",
                    codes
                )
                await db.commit()
                logger.info(f"Cleaned up {len(codes)} old rooms")
        
        return len(codes) if codes else 0
    
    async def get_disconnected_players(
        self, 
        grace_seconds: float
    ) -> List[Dict[str, Any]]:
        """
        Get players who have been disconnected longer than grace period.
        Used by cleanup task.
        """
        await self.initialize()
        cutoff = time.time() - grace_seconds
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT p.*, r.state as room_state
                FROM players p
                JOIN rooms r ON p.room_code = r.code
                WHERE p.is_connected = 0 
                AND p.disconnect_time IS NOT NULL 
                AND p.disconnect_time < ?
            """, (cutoff,)) as cursor:
                rows = await cursor.fetchall()
                return [{
                    "player_id": row["player_id"],
                    "room_code": row["room_code"],
                    "name": row["name"],
                    "is_host": bool(row["is_host"]),
                    "room_state": row["room_state"]
                } for row in rows]


# Singleton instance
game_store = GameStore()
