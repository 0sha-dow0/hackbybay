import asyncio
from collections.abc import AsyncIterator
from typing import Final

from backend.domain.determinism import Clock
from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import PipelineEvent
from backend.ports.event_sink import EventSink

_POST_TERMINAL_MESSAGE: Final[str] = (
    "cannot publish an event for an incident that already emitted its terminal event"
)
_CONTEXT_KEY_INCIDENT_ID: Final[str] = "incident_id"
_CONTEXT_KEY_STAGE: Final[str] = "stage"
_FIELD_SEQ: Final[str] = "seq"
_FIELD_AT: Final[str] = "at"
_FIRST_SEQ: Final[int] = 0


class InMemoryEventSink(EventSink):
    def __init__(self, clock: Clock) -> None:
        self._clock: Clock = clock
        self._events: dict[str, list[PipelineEvent]] = {}
        self._terminated: set[str] = set()
        self._wakeups: dict[str, asyncio.Event] = {}

    def publish(self, event: PipelineEvent) -> Result[None, DepCoverError]:
        incident_id = event.incident_id
        if incident_id in self._terminated:
            return Err(
                DepCoverError(
                    _POST_TERMINAL_MESSAGE,
                    {
                        _CONTEXT_KEY_INCIDENT_ID: incident_id,
                        _CONTEXT_KEY_STAGE: event.stage,
                    },
                )
            )
        events = self._events.setdefault(incident_id, [])
        stamped = self._stamp(event, _FIRST_SEQ + len(events))
        events.append(stamped)
        if stamped.terminal:
            self._terminated.add(incident_id)
        self._wake(incident_id)
        return Ok(None)

    def replay(
        self, incident_id: str
    ) -> Result[tuple[PipelineEvent, ...], DepCoverError]:
        events = self._events.get(incident_id)
        stored: tuple[PipelineEvent, ...] = tuple(events) if events is not None else ()
        return Ok(stored)

    def subscribe(self, incident_id: str) -> AsyncIterator[PipelineEvent]:
        existing = self._events.get(incident_id)
        start_index = len(existing) if existing is not None else 0
        return self._stream(incident_id, start_index)

    async def _stream(
        self, incident_id: str, start_index: int
    ) -> AsyncIterator[PipelineEvent]:
        cursor = start_index
        while True:
            events = self._events.get(incident_id)
            if events is not None and cursor < len(events):
                event = events[cursor]
                cursor += 1
                yield event
                if event.terminal:
                    return
                continue
            if incident_id in self._terminated:
                return
            await self._current_wakeup(incident_id).wait()

    def _stamp(self, event: PipelineEvent, seq: int) -> PipelineEvent:
        return event.model_copy(update={_FIELD_SEQ: seq, _FIELD_AT: self._clock.now()})

    def _wake(self, incident_id: str) -> None:
        waiter = self._wakeups.pop(incident_id, None)
        if waiter is not None:
            waiter.set()

    def _current_wakeup(self, incident_id: str) -> asyncio.Event:
        waiter = self._wakeups.get(incident_id)
        if waiter is None:
            waiter = asyncio.Event()
            self._wakeups[incident_id] = waiter
        return waiter


__all__ = ("InMemoryEventSink",)
