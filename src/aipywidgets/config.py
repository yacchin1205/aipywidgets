from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AIConfig:
    client: Any | None = None
    model: str | None = None
    base_url: str | None = None

    @classmethod
    def from_user_input(cls, *, model: str | None = None, base_url: str | None = None) -> "AIConfig":
        return cls(client=None, model=model, base_url=base_url)
