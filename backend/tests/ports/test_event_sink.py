import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from backend.adapters.fake.fake_event_sink import InMemoryEventSink
from backend.domain.determinism import FixedClock
from backend.domain.enums import PipelineStage
from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import PipelineEvent
from backend.ports.event_sink import EventSink

_T = TypeVar("_T")

_START: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
_STEP_S: float = 1.0
_CONSTRUCTED_AT: datetime = datetime(2000, 1, 1, tzinfo=timezone.utc)
_CONSTRUCTED_SEQ: int = 0
_TIMEOUT_S: float = 2.0

_NONTERMINAL_STAGES: tuple[PipelineStage, ...] = (
    PipelineStage.RECALL,
    PipelineStage.REWRITE,
    PipelineStage.VALIDATE,
    PipelineStage.VERIFY_BUILD,
    PipelineStage.VERIFY_TEST,
    PipelineStage.VERIFY_BEHAVIORAL,
    PipelineStage.JUDGE,
)


def _clock() -> FixedClock:
    return FixedClock(_START, _STEP_S)


def _expected_at(index: int) -> datetime:
    return _START + timedelta(seconds=_STEP_S * index)


def _event(incident_id: str, stage: PipelineStage, terminal: bool) -> PipelineEvent:
    return PipelineEvent(
        incident_id=incident_id,
        stage=stage,
        seq=_CONSTRUCTED_SEQ,
        message=stage.value,
        at=_CONSTRUCTED_AT,
        terminal=terminal,
    )


def _nonterminal_events(incident_id: str, count: int) -> list[PipelineEvent]:
    return [
        _event(incident_id, _NONTERMINAL_STAGES[index], False) for index in range(count)
    ]


def _terminal_event(incident_id: str) -> PipelineEvent:
    return _event(incident_id, PipelineStage.COMPLETED, True)


def _expect_ok(result: Result[_T, DepCoverError]) -> _T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _expect_err(result: Result[_T, DepCoverError]) -> DepCoverError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


async def _collect(
    stream: AsyncIterator[PipelineEvent], out: list[PipelineEvent]
) -> None:
    async for event in stream:
        out.append(event)


async def _drain(stream: AsyncIterator[PipelineEvent]) -> list[PipelineEvent]:
    out: list[PipelineEvent] = []
    await _collect(stream, out)
    return out


async def _tick(times: int = 3) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


async def _cancel(task: "asyncio.Task[None]") -> None:
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_sink_restamps_seq_and_at_monotonically() -> None:
    sink = InMemoryEventSink(_clock())
    incident = "inc-stamp"
    for event in _nonterminal_events(incident, 3):
        _expect_ok(sink.publish(event))
    _expect_ok(sink.publish(_terminal_event(incident)))

    history = _expect_ok(sink.replay(incident))

    assert isinstance(history, tuple)
    assert len(history) == 4
    assert [event.seq for event in history] == [0, 1, 2, 3]
    assert [event.at for event in history] == [_expected_at(index) for index in range(4)]
    assert [event.terminal for event in history] == [False, False, False, True]
    assert all(event.at != _CONSTRUCTED_AT for event in history)


async def test_publish_after_terminal_is_err_and_history_unchanged() -> None:
    sink = InMemoryEventSink(_clock())
    incident = "inc-terminal-guard"
    for event in _nonterminal_events(incident, 3):
        _expect_ok(sink.publish(event))
    _expect_ok(sink.publish(_terminal_event(incident)))

    before = _expect_ok(sink.replay(incident))

    post_nonterminal = _expect_err(sink.publish(_event(incident, PipelineStage.RECALL, False)))
    assert isinstance(post_nonterminal, DepCoverError)

    post_terminal = _expect_err(sink.publish(_terminal_event(incident)))
    assert isinstance(post_terminal, DepCoverError)

    after = _expect_ok(sink.replay(incident))
    assert after == before
    assert len(after) == 4
    terminal_flags = [event.terminal for event in after]
    assert terminal_flags.count(True) == 1
    assert terminal_flags[-1] is True


async def test_replay_unknown_incident_is_ok_empty() -> None:
    sink = InMemoryEventSink(_clock())
    result = _expect_ok(sink.replay("inc-never-seen"))
    assert result == ()
    assert isinstance(result, tuple)


async def test_early_subscriber_receives_live_tail_and_completes() -> None:
    sink = InMemoryEventSink(_clock())
    incident = "inc-early"
    received: list[PipelineEvent] = []
    stream = sink.subscribe(incident)
    task: asyncio.Task[None] = asyncio.create_task(_collect(stream, received))
    try:
        await _tick()
        assert received == []

        _expect_ok(sink.publish(_nonterminal_events(incident, 1)[0]))
        await _tick()
        assert len(received) == 1
        assert received[0].seq == 0
        assert received[0].terminal is False

        for event in _nonterminal_events(incident, 3)[1:]:
            _expect_ok(sink.publish(event))
        _expect_ok(sink.publish(_terminal_event(incident)))

        await asyncio.wait_for(task, timeout=_TIMEOUT_S)

        assert task.done()
        assert [event.seq for event in received] == [0, 1, 2, 3]
        assert [event.stage for event in received] == [
            PipelineStage.RECALL,
            PipelineStage.REWRITE,
            PipelineStage.VALIDATE,
            PipelineStage.COMPLETED,
        ]
        assert received[-1].terminal is True
        assert sum(1 for event in received if event.terminal) == 1
    finally:
        await _cancel(task)


async def test_early_subscriber_terminal_only_completes() -> None:
    sink = InMemoryEventSink(_clock())
    incident = "inc-terminal-only"
    stream = sink.subscribe(incident)
    received: list[PipelineEvent] = []
    task: asyncio.Task[None] = asyncio.create_task(_collect(stream, received))
    try:
        await _tick()
        _expect_ok(sink.publish(_terminal_event(incident)))
        await asyncio.wait_for(task, timeout=_TIMEOUT_S)
        assert task.done()
        assert len(received) == 1
        assert received[0].terminal is True
        assert received[0].seq == 0
    finally:
        await _cancel(task)


async def test_late_subscriber_after_completion_yields_nothing() -> None:
    sink = InMemoryEventSink(_clock())
    incident = "inc-late"
    for event in _nonterminal_events(incident, 3):
        _expect_ok(sink.publish(event))
    _expect_ok(sink.publish(_terminal_event(incident)))

    history = _expect_ok(sink.replay(incident))
    assert [event.seq for event in history] == [0, 1, 2, 3]
    assert history[-1].terminal is True

    stream = sink.subscribe(incident)
    tail = await asyncio.wait_for(_drain(stream), timeout=_TIMEOUT_S)
    assert tail == []


async def test_concurrent_subscribers_receive_identical_full_sequence() -> None:
    sink = InMemoryEventSink(_clock())
    incident = "inc-concurrent"
    stream_a = sink.subscribe(incident)
    stream_b = sink.subscribe(incident)
    task_a: asyncio.Task[list[PipelineEvent]] = asyncio.create_task(_drain(stream_a))
    task_b: asyncio.Task[list[PipelineEvent]] = asyncio.create_task(_drain(stream_b))
    try:
        await _tick()
        for event in _nonterminal_events(incident, 3):
            _expect_ok(sink.publish(event))
        _expect_ok(sink.publish(_terminal_event(incident)))

        received_a = await asyncio.wait_for(task_a, timeout=_TIMEOUT_S)
        received_b = await asyncio.wait_for(task_b, timeout=_TIMEOUT_S)

        assert received_a == received_b
        assert [event.seq for event in received_a] == [0, 1, 2, 3]
        assert received_a[-1].terminal is True
        assert sum(1 for event in received_a if event.terminal) == 1
    finally:
        for pending in (task_a, task_b):
            if not pending.done():
                pending.cancel()


async def test_cross_incident_replay_isolation_and_independent_seq() -> None:
    sink = InMemoryEventSink(_clock())
    incident_a = "inc-a"
    incident_b = "inc-b"

    _expect_ok(sink.publish(_event(incident_a, PipelineStage.RECALL, False)))
    _expect_ok(sink.publish(_event(incident_b, PipelineStage.RECALL, False)))
    _expect_ok(sink.publish(_event(incident_a, PipelineStage.REWRITE, False)))
    _expect_ok(sink.publish(_terminal_event(incident_a)))
    _expect_ok(sink.publish(_event(incident_b, PipelineStage.REWRITE, False)))
    _expect_ok(sink.publish(_terminal_event(incident_b)))

    history_a = _expect_ok(sink.replay(incident_a))
    history_b = _expect_ok(sink.replay(incident_b))

    assert all(event.incident_id == incident_a for event in history_a)
    assert all(event.incident_id == incident_b for event in history_b)
    assert [event.seq for event in history_a] == [0, 1, 2]
    assert [event.seq for event in history_b] == [0, 1, 2]
    assert [event.stage for event in history_a] == [
        PipelineStage.RECALL,
        PipelineStage.REWRITE,
        PipelineStage.COMPLETED,
    ]
    assert [event.stage for event in history_b] == [
        PipelineStage.RECALL,
        PipelineStage.REWRITE,
        PipelineStage.COMPLETED,
    ]


async def test_cross_incident_subscribe_isolation() -> None:
    sink = InMemoryEventSink(_clock())
    incident_a = "inc-a-sub"
    incident_b = "inc-b-sub"
    received_b: list[PipelineEvent] = []
    stream_b = sink.subscribe(incident_b)
    task_b: asyncio.Task[None] = asyncio.create_task(_collect(stream_b, received_b))
    try:
        await _tick()
        for event in _nonterminal_events(incident_a, 2):
            _expect_ok(sink.publish(event))
        _expect_ok(sink.publish(_terminal_event(incident_a)))
        await _tick()

        assert received_b == []

        _expect_ok(sink.publish(_terminal_event(incident_b)))
        await asyncio.wait_for(task_b, timeout=_TIMEOUT_S)

        assert task_b.done()
        assert [event.incident_id for event in received_b] == [incident_b]
        assert received_b[0].terminal is True
    finally:
        await _cancel(task_b)


def test_structural_conformance_to_event_sink_protocol() -> None:
    sink: EventSink = InMemoryEventSink(_clock())
    assert isinstance(sink, InMemoryEventSink)
