"""Adapter interface."""
from __future__ import annotations

from typing import Iterable, Protocol


class Adapter(Protocol):
    name: str

    def collect(self) -> Iterable[dict]:
        """Yield UsageEvent dicts in the unified schema."""
        ...
