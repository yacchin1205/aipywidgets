from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AIConfig:
    client: Any
    model: str

    def __post_init__(self) -> None:
        if self.client is None:
            raise RuntimeError(
                "AIConfig.client is required. Create a Responses API compatible client and pass it explicitly."
            )
        if not self.model:
            raise RuntimeError("AIConfig.model is required")

    def get_client(self) -> Any:
        return self.client
