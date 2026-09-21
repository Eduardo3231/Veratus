from __future__ import annotations

import asyncio
from typing import Any


class PostgresSession:
    """OpenAI Agents SDK session backed by the same durable PostgreSQL database."""

    session_settings = None

    def __init__(self, session_id: str, database_url: str, history_limit: int = 30):
        from agents import SessionSettings

        self.session_id = session_id
        self.database_url = database_url
        self.session_settings = SessionSettings(limit=history_limit)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    @staticmethod
    def _driver():
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError(
                "Instale psycopg[binary] para usar memória PostgreSQL"
            ) from exc
        return psycopg, Jsonb

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            psycopg, _ = self._driver()
            async with await psycopg.AsyncConnection.connect(
                self.database_url
            ) as connection:
                await connection.execute(
                    """CREATE TABLE IF NOT EXISTS agent_session_items (
                        sequence BIGSERIAL PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        item_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_agent_session_items_session
                        ON agent_session_items(session_id, sequence);"""
                )
            self._schema_ready = True

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        await self._ensure_schema()
        psycopg, _ = self._driver()
        query = "SELECT item_json FROM agent_session_items WHERE session_id=%s ORDER BY sequence DESC"
        params: tuple[Any, ...] = (self.session_id,)
        if limit is not None:
            query += " LIMIT %s"
            params += (max(0, limit),)
        async with await psycopg.AsyncConnection.connect(
            self.database_url
        ) as connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return [row[0] for row in reversed(rows)]

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        await self._ensure_schema()
        psycopg, Jsonb = self._driver()
        async with (
            await psycopg.AsyncConnection.connect(self.database_url) as connection,
            connection.cursor() as cursor,
        ):
            await cursor.executemany(
                "INSERT INTO agent_session_items (session_id,item_json) VALUES (%s,%s)",
                [(self.session_id, Jsonb(item)) for item in items],
            )

    async def pop_item(self) -> dict[str, Any] | None:
        await self._ensure_schema()
        psycopg, _ = self._driver()
        async with await psycopg.AsyncConnection.connect(
            self.database_url
        ) as connection:
            cursor = await connection.execute(
                """DELETE FROM agent_session_items WHERE sequence=(
                    SELECT sequence FROM agent_session_items WHERE session_id=%s
                    ORDER BY sequence DESC LIMIT 1 FOR UPDATE SKIP LOCKED
                ) RETURNING item_json""",
                (self.session_id,),
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def clear_session(self) -> None:
        await self._ensure_schema()
        psycopg, _ = self._driver()
        async with await psycopg.AsyncConnection.connect(
            self.database_url
        ) as connection:
            await connection.execute(
                "DELETE FROM agent_session_items WHERE session_id=%s",
                (self.session_id,),
            )
