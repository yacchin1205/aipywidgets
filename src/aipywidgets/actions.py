from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    placement: str = "footer"
    style: str = "secondary"
    requires_confirmation: bool = False
