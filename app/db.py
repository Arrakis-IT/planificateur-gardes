# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Async SQLAlchemy engine and session management."""

import asyncio
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

DB_CHECK_TIMEOUT_SECONDS = 3.0


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (created lazily on first use)."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory bound to the process-wide engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, rolled back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Return True if the database answers `SELECT 1` within the timeout."""
    try:
        async with asyncio.timeout(DB_CHECK_TIMEOUT_SECONDS):
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Log only the exception type: driver messages may include connection details.
        logger.warning("Database health check failed: %s", type(exc).__name__)
        return False
    return True


async def dispose_engine() -> None:
    """Close all pooled connections (called on application shutdown)."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
