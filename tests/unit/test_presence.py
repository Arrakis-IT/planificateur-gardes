# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Presence hub: heartbeat TTL, sweep, event fan-out, SSE generator."""

import asyncio
import json
import uuid

import pytest

from app.services import presence
from app.services.presence import PRESENCE_TTL, SWEEP_INTERVAL, PresenceHub, PresentUser

CLINIC_A, CLINIC_B = uuid.uuid4(), uuid.uuid4()
NR = PresentUser(uuid.uuid4(), "NR", "Nicolas Rousseau", "#2d4a3e")
DT = PresentUser(uuid.uuid4(), "DT", "Dominique Thill", "#5c6ba8")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def hub(clock):
    return PresenceHub(clock=clock)


def drain(connection) -> list[tuple[str, dict]]:
    events = []
    while not connection.queue.empty():
        name, data = connection.queue.get_nowait()
        events.append((name, json.loads(data)))
    return events


def test_heartbeat_announces_new_user_once(hub):
    watcher = hub.connect(CLINIC_A, DT)
    hub.heartbeat(CLINIC_A, uuid.uuid4(), NR)
    hub.heartbeat(CLINIC_A, uuid.uuid4(), NR)  # second tab of the same user
    events = drain(watcher)
    assert [name for name, _ in events] == ["user_entered"]
    assert events[0][1]["user"] == {"code": "NR", "name": "Nicolas Rousseau", "color": "#2d4a3e"}
    assert events[0][1]["present"] == [NR.as_dict()]


def test_user_left_after_ttl_and_sweep(hub, clock):
    watcher = hub.connect(CLINIC_A, DT)
    session = uuid.uuid4()
    hub.heartbeat(CLINIC_A, session, NR)
    drain(watcher)

    clock.now += PRESENCE_TTL - 1
    hub.sweep()
    assert drain(watcher) == [] and hub.present_users(CLINIC_A) == [NR]

    clock.now += 2  # TTL exceeded
    hub.sweep()
    events = drain(watcher)
    assert [name for name, _ in events] == ["user_left"]
    assert events[0][1]["present"] == []
    # Worst case seen by others: TTL + one sweep interval.
    assert PRESENCE_TTL + SWEEP_INTERVAL <= 40


def test_heartbeats_keep_user_present(hub, clock):
    session = uuid.uuid4()
    for _ in range(10):
        hub.heartbeat(CLINIC_A, session, NR)
        clock.now += 10
        hub.sweep()
    assert hub.present_users(CLINIC_A) == [NR]


def test_one_live_session_keeps_user_present(hub, clock):
    first, second = uuid.uuid4(), uuid.uuid4()
    hub.heartbeat(CLINIC_A, first, NR)
    clock.now += 20
    hub.heartbeat(CLINIC_A, second, NR)
    clock.now += 15  # first expired, second alive
    hub.sweep()
    assert hub.present_users(CLINIC_A) == [NR]


def test_closing_stream_does_not_drop_presence_immediately(hub):
    """Navigating between pages closes/reopens the stream: no flicker for others."""
    watcher = hub.connect(CLINIC_A, DT)
    hub.heartbeat(CLINIC_A, uuid.uuid4(), NR)
    page = hub.connect(CLINIC_A, NR)
    drain(watcher)
    hub.disconnect(page)
    hub.sweep()
    assert drain(watcher) == [] and hub.present_users(CLINIC_A) == [NR]


def test_events_are_scoped_per_clinic(hub):
    in_a, in_b = hub.connect(CLINIC_A, NR), hub.connect(CLINIC_B, DT)
    hub.publish(CLINIC_A, "draft_changed", {"count": 1})
    assert [n for n, _ in drain(in_a)] == ["draft_changed"]
    assert drain(in_b) == []
    hub.heartbeat(CLINIC_B, uuid.uuid4(), DT)
    assert drain(in_a) == []
    assert hub.present_users(CLINIC_A) == []


def test_slow_client_does_not_block_others(hub, monkeypatch):
    monkeypatch.setattr(presence, "QUEUE_SIZE", 2)
    slow, other = hub.connect(CLINIC_A, NR), hub.connect(CLINIC_A, DT)
    for i in range(5):
        hub.publish(CLINIC_A, "draft_changed", {"count": i})
        drain(other)
    assert slow.queue.qsize() == 2  # extra events dropped, no exception


async def test_stream_yields_snapshot_then_events_and_unregisters(hub):
    hub.heartbeat(CLINIC_A, uuid.uuid4(), NR)
    connection = hub.connect(CLINIC_A, DT)
    stream = hub.stream(connection)
    first = await anext(stream)
    assert first["event"] == "presence"
    assert json.loads(first["data"])["present"] == [NR.as_dict()]

    hub.publish(CLINIC_A, "draft_cleared", {"reason": "saved"})
    second = await anext(stream)
    assert second["event"] == "draft_cleared"
    assert json.loads(second["data"])["reason"] == "saved"

    await stream.aclose()
    assert hub.connection_count(CLINIC_A) == 0


async def test_stream_keeps_waiting_through_timeouts(hub, monkeypatch):
    monkeypatch.setattr(presence, "STREAM_TIMEOUT", 0.01)
    connection = hub.connect(CLINIC_A, DT)
    stream = hub.stream(connection)
    await anext(stream)  # snapshot

    async def publish_later():
        await asyncio.sleep(0.05)
        hub.publish(CLINIC_A, "draft_changed", {"count": 2})

    task = asyncio.create_task(publish_later())
    event = await asyncio.wait_for(anext(stream), timeout=1)
    await task
    assert event["event"] == "draft_changed"
    await stream.aclose()


async def test_sweeper_task_lifecycle(hub, clock, monkeypatch):
    monkeypatch.setattr(presence, "SWEEP_INTERVAL", 0.01)
    watcher = hub.connect(CLINIC_A, DT)
    hub.heartbeat(CLINIC_A, uuid.uuid4(), NR)
    drain(watcher)
    hub.start()
    hub.start()  # idempotent
    clock.now += PRESENCE_TTL + 1
    await asyncio.sleep(0.05)
    assert [n for n, _ in drain(watcher)] == ["user_left"]
    await hub.stop()
    await hub.stop()
