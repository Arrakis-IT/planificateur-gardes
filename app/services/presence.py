# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Per-clinic presence and live events (Server-Sent Events), in memory.

Presence: every open page sends a heartbeat (POST /sse/heartbeat) every 10 s; a user is
present while one of their sessions pinged within the last PRESENCE_TTL (30 s). A sweeper
runs every SWEEP_INTERVAL (10 s) and broadcasts `user_left` for expired users, so a closed
browser disappears for the others after at most 30 + 10 = 40 s. Navigating between pages
does not flicker: presence is per session, not per SSE connection.

Events (`user_entered`, `user_left`, `draft_changed`, `draft_cleared`) are pushed to the
queue of every SSE connection of the clinic. Everything lives in this process: the app runs
a single uvicorn worker. Running several workers would need a shared channel (e.g.
PostgreSQL LISTEN/NOTIFY) instead of these in-memory queues.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PRESENCE_TTL = 30.0
SWEEP_INTERVAL = 10.0
QUEUE_SIZE = 100
STREAM_TIMEOUT = 15.0  # how long the stream waits for an event before re-checking


@dataclass(frozen=True)
class PresentUser:
    """What other clinic users see about someone online."""

    user_id: uuid.UUID
    code: str
    name: str
    color: str

    def as_dict(self) -> dict[str, str]:
        """JSON-friendly representation (no ids sent to browsers)."""
        return {"code": self.code, "name": self.name, "color": self.color}


@dataclass(eq=False)  # identity semantics: connections live in sets
class Connection:
    """One open SSE stream."""

    clinic_id: uuid.UUID
    user: PresentUser
    queue: asyncio.Queue[tuple[str, str]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE)
    )


class PresenceHub:
    """Presence registry and event fan-out for all clinics."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # clinic → session id → (user, last heartbeat)
        self._seen: dict[uuid.UUID, dict[uuid.UUID, tuple[PresentUser, float]]] = {}
        self._present: dict[uuid.UUID, dict[uuid.UUID, PresentUser]] = {}
        self._connections: dict[uuid.UUID, set[Connection]] = {}
        self._sweeper: asyncio.Task[None] | None = None

    # --- Presence ---------------------------------------------------------------------

    def heartbeat(self, clinic_id: uuid.UUID, session_id: uuid.UUID, user: PresentUser) -> None:
        """Record a ping; announces the user immediately if they were not present."""
        self._seen.setdefault(clinic_id, {})[session_id] = (user, self._clock())
        present = self._present.setdefault(clinic_id, {})
        if user.user_id not in present:
            present[user.user_id] = user
            self.publish(clinic_id, "user_entered", {"user": user.as_dict()})

    def present_users(self, clinic_id: uuid.UUID) -> list[PresentUser]:
        """Users currently present in the clinic, sorted by code."""
        return sorted(self._present.get(clinic_id, {}).values(), key=lambda u: u.code)

    def sweep(self) -> None:
        """Expire sessions without heartbeat for PRESENCE_TTL; announce users who left."""
        now = self._clock()
        emptied: list[uuid.UUID] = []
        for clinic_id, sessions in self._seen.items():
            # Collect first, delete after: never mutate a dict while iterating over it.
            expired = [sid for sid, (_, last) in sessions.items() if now - last > PRESENCE_TTL]
            for session_id in expired:
                del sessions[session_id]
            still_here = {user.user_id for user, _ in sessions.values()}
            present = self._present.get(clinic_id, {})
            for user_id in [uid for uid in present if uid not in still_here]:
                user = present.pop(user_id)
                self.publish(clinic_id, "user_left", {"user": user.as_dict()})
            if not sessions:
                emptied.append(clinic_id)
        for clinic_id in emptied:
            del self._seen[clinic_id]

    async def run_sweeper(self) -> None:
        """Background loop (started in the app lifespan)."""
        while True:
            await asyncio.sleep(SWEEP_INTERVAL)
            try:
                self.sweep()
            except Exception:  # pragma: no cover - never let the loop die
                logger.exception("Presence sweep failed")

    def start(self) -> None:
        """Start the sweeper task (idempotent)."""
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self.run_sweeper())

    async def stop(self) -> None:
        """Cancel the sweeper task."""
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None

    # --- Events ------------------------------------------------------------------------

    def publish(self, clinic_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
        """Send an event to every open stream of the clinic (with the presence list)."""
        payload = {**data, "present": [u.as_dict() for u in self.present_users(clinic_id)]}
        message = (event, json.dumps(payload, ensure_ascii=False))
        for connection in self._connections.get(clinic_id, ()):
            try:
                connection.queue.put_nowait(message)
            except asyncio.QueueFull:
                # A stuck client must not block the others; it will resync on reconnect.
                logger.warning("Dropping SSE event for a slow client")

    def connect(self, clinic_id: uuid.UUID, user: PresentUser) -> Connection:
        """Register an SSE stream."""
        connection = Connection(clinic_id, user)
        self._connections.setdefault(clinic_id, set()).add(connection)
        return connection

    def disconnect(self, connection: Connection) -> None:
        """Unregister an SSE stream (presence itself expires through the TTL)."""
        connections = self._connections.get(connection.clinic_id)
        if connections is not None:
            connections.discard(connection)
            if not connections:
                del self._connections[connection.clinic_id]

    def reset(self) -> None:
        """Forget all presence and connections (tests)."""
        self._seen.clear()
        self._present.clear()
        self._connections.clear()

    def connection_count(self, clinic_id: uuid.UUID) -> int:
        """Number of open streams for a clinic (tests, diagnostics)."""
        return len(self._connections.get(clinic_id, ()))

    async def stream(self, connection: Connection) -> AsyncIterator[dict[str, str]]:
        """SSE event generator for one connection; unregisters it when the client leaves."""
        try:
            snapshot = {"present": [u.as_dict() for u in self.present_users(connection.clinic_id)]}
            yield {"event": "presence", "data": json.dumps(snapshot, ensure_ascii=False)}
            while True:
                try:
                    event, data = await asyncio.wait_for(
                        connection.queue.get(), timeout=STREAM_TIMEOUT
                    )
                except TimeoutError:
                    continue  # sse-starlette sends its own keep-alive pings
                yield {"event": event, "data": data}
        finally:
            self.disconnect(connection)


hub = PresenceHub()
