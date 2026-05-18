from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ipywidgets as widgets

from ..field_path import PathPart


@dataclass
class Field:
    id: str
    label: str | None = None
    default: Any = None
    required: bool = False
    full_width: bool = False
    validator: Callable[[Any, dict[str, Any]], str | None] | None = None

    def make_widget(self):
        return widgets.Text(description=self.label or self.id, value=self.default or "")

    def render(self, form, path: str):
        return form._render_leaf_field(self, path)

    def stores_value(self) -> bool:
        return True

    def empty_value(self) -> Any:
        return self.default

    def validate(self, value: Any, values: dict[str, Any]) -> str | None:
        if self.required and self._is_empty(value):
            return "Required"
        if self.validator is not None:
            return self.validator(value, values)
        return None

    def _is_empty(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return value is False
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, (list, dict, tuple, set)):
            return len(value) == 0
        return False

    def validate_path(self, form, path: str) -> dict[str, str]:
        error = self.validate(form.get_value(path), form.get_values())
        if error is None:
            return {}
        return {path: error}

    def validate_tree(self, form, path: str) -> dict[str, str]:
        return self.validate_path(form, path)

    def validate_schema(self, validate_fields: Callable[[list["Field"], str], None], owner: str) -> None:
        return None

    def field_at_parts(self, parts: list[PathPart]) -> "Field":
        if parts:
            raise ValueError(f"Path does not resolve inside field {self.id!r}: {parts!r}")
        return self
