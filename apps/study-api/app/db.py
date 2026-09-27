"""PostgreSQL access. Every transaction carries its own tenant identity.

`tx(ctx)` sets app.user_id and app.workspace_id with set_config(..., true), which is
transaction-local: the values vanish at commit or rollback, so a pooled connection can
never hand one request's identity to the next. No context means RLS returns no tenant rows.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class Context:
    user_id: UUID | None
    workspace_id: UUID | None


async def _reset(conn):
    # Defensive: session-level settings must never survive a checkout.
    await conn.execute('RESET ALL')
    await conn.commit()


class Database:
    def __init__(self, url, *, min_size=1, max_size=10, name='studyhub'):
        if not url:
            raise RuntimeError('DATABASE_URL is not set.')
        self.pool = AsyncConnectionPool(url, min_size=min_size, max_size=max_size, open=False, name=name,
                                        kwargs={'row_factory': dict_row, 'autocommit': False}, reset=_reset)

    async def open(self):
        await self.pool.open(wait=True, timeout=30)

    async def close(self):
        await self.pool.close()

    @asynccontextmanager
    async def tx(self, ctx: Context | None = None):
        async with self.pool.connection() as conn:
            async with conn.transaction():
                if ctx:
                    await conn.execute("SELECT set_config('app.user_id', %s, true), set_config('app.workspace_id', %s, true)",
                                       (str(ctx.user_id) if ctx.user_id else '', str(ctx.workspace_id) if ctx.workspace_id else ''))
                yield conn


async def one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def rows(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def value(conn, sql, params=()):
    row = await one(conn, sql, params)
    return next(iter(row.values())) if row else None


def vector_literal(values):
    """pgvector's text form. Non-finite values are rejected by pgvector itself."""
    return '['+','.join(repr(float(v)) for v in values)+']'
