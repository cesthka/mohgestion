"""Asynchronous SQLite database manager."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Optional

import aiosqlite

log = logging.getLogger(__name__)


class Database:
    """Wrapper around an aiosqlite connection.

    The same connection is reused for the bot lifetime, with WAL mode
    enabled so concurrent reads do not block writes.
    """

    def __init__(self, path: str) -> None:
        self.path: str = path
        self._conn: Optional[aiosqlite.Connection] = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    async def connect(self) -> None:
        """Open the connection and apply the schema."""
        parent = Path(self.path).parent
        if str(parent) and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._apply_schema()
        log.info("Database connected at %s", self.path)

    async def _apply_schema(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = f.read()
        await self.conn.executescript(schema)
        await self.conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # --- Generic helpers -------------------------------------------------

    async def execute(self, query: str, params: Iterable[Any] = ()) -> None:
        await self.conn.execute(query, tuple(params))
        await self.conn.commit()

    async def executemany(self, query: str, params: Iterable[Iterable[Any]]) -> None:
        await self.conn.executemany(query, [tuple(p) for p in params])
        await self.conn.commit()

    async def fetchone(self, query: str, params: Iterable[Any] = ()) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(query, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(query, tuple(params)) as cur:
            return await cur.fetchall()

    async def fetchval(self, query: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetchone(query, params)
        if row is None:
            return None
        return row[0]

    # --- Guild config helpers -------------------------------------------

    async def ensure_guild(self, guild_id: int) -> None:
        """Insert a default config row for a guild if missing."""
        await self.execute(
            "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
            (guild_id,),
        )

    async def get_prefix(self, guild_id: int) -> str:
        from config.config import DEFAULT_PREFIX
        await self.ensure_guild(guild_id)
        val = await self.fetchval(
            "SELECT prefix FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        return val or DEFAULT_PREFIX

    async def set_prefix(self, guild_id: int, prefix: str) -> None:
        await self.ensure_guild(guild_id)
        await self.execute(
            "UPDATE guild_config SET prefix = ? WHERE guild_id = ?",
            (prefix, guild_id),
        )

    async def get_color(self, guild_id: int) -> int:
        from config.config import COLOR_DEFAULT
        await self.ensure_guild(guild_id)
        val = await self.fetchval(
            "SELECT embed_color FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        return int(val) if val is not None else COLOR_DEFAULT

    async def set_color(self, guild_id: int, color: int) -> None:
        await self.ensure_guild(guild_id)
        await self.execute(
            "UPDATE guild_config SET embed_color = ? WHERE guild_id = ?",
            (color, guild_id),
        )

    async def get_config(self, guild_id: int) -> aiosqlite.Row:
        await self.ensure_guild(guild_id)
        row = await self.fetchone(
            "SELECT * FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        return row  # type: ignore[return-value]

    async def update_config(self, guild_id: int, **fields: Any) -> None:
        """Update arbitrary fields in guild_config."""
        if not fields:
            return
        await self.ensure_guild(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [guild_id]
        await self.execute(
            f"UPDATE guild_config SET {cols} WHERE guild_id = ?",
            values,
        )

    # --- Custom messages -------------------------------------------------

    async def get_message(self, guild_id: int, key: str) -> Optional[str]:
        val = await self.fetchval(
            "SELECT value FROM custom_messages WHERE guild_id = ? AND key = ?",
            (guild_id, key),
        )
        return val

    async def set_message(self, guild_id: int, key: str, value: str) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO custom_messages (guild_id, key, value) VALUES (?, ?, ?)",
            (guild_id, key, value),
        )

    async def reset_message(self, guild_id: int, key: str) -> None:
        await self.execute(
            "DELETE FROM custom_messages WHERE guild_id = ? AND key = ?",
            (guild_id, key),
        )
