from collections.abc import AsyncIterator
from typing import Protocol

from backend.domain.errors import DepCoverError, Result
from backend.domain.models import PipelineEvent


class EventSink(Protocol):
    def publish(self, event: PipelineEvent) -> Result[None, DepCoverError]: ...

    def subscribe(self, incident_id: str) -> AsyncIterator[PipelineEvent]: ...

    def replay(
        self, incident_id: str
    ) -> Result[tuple[PipelineEvent, ...], DepCoverError]: ...


__all__ = ("EventSink",)
