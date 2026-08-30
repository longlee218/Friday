from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from friday.models import InboundEvent

__all__ = ["Provider"]


@runtime_checkable
class Provider(Protocol):
    """A chat platform, as the rest of the system sees it.

    Grows as tickets need it: reply and approval arrive with the tickets that
    use them. Platform mechanics stay entirely inside implementations.
    """

    name: str

    def stream(self) -> AsyncIterator[InboundEvent]:
        """Yield normalised inbound messages as they arrive."""
        ...
