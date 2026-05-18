from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
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

    def make_control(self):
        return widgets.Text(value=self.default or "")

    def make_label_widget(self):
        label = self.label or self.id
        if not label:
            return widgets.HTML("")
        required = " <span style='color: #b00020;'>*</span>" if self.required else ""
        return widgets.HTML(f"<div>{escape(label)}{required}</div>")

    def make_widget(self):
        return self.make_control()

    def render(self, form, path: str, allocation, grid):
        input_widget = self.make_control()
        if self.full_width:
            input_widget.layout.width = "100%"
        input_widget.layout.margin = "0"
        label_widget = self.make_label_widget()
        error_widget = widgets.HTML("")
        form._sync_widget_value(input_widget, path)
        form._widgets[path] = input_widget
        if hasattr(input_widget, "observe"):
            input_widget.observe(lambda change, p=path: form._on_widget_change(p, change), names="value")
        form._register_field(path, self, error_widget)
        if allocation.span >= grid.inline_threshold_units:
            label_span = min(grid.label_units, max(1, allocation.span - 1))
            control_span = allocation.span - label_span
        else:
            label_span = allocation.span
            control_span = allocation.span

        label_cell = widgets.Box(
            [label_widget],
            layout=widgets.Layout(
                width="auto",
                min_width="0",
                flex=f"{label_span} {label_span} 0",
                overflow="visible",
            ),
        )
        control_cell = widgets.VBox(
            [input_widget, error_widget],
            layout=widgets.Layout(
                width="auto",
                min_width="0",
                flex=f"{control_span} {control_span} 0",
                overflow="visible",
            ),
        )
        row = widgets.HBox(
            [label_cell, control_cell],
            layout=widgets.Layout(
                width="100%",
                align_items="flex-start",
                overflow="visible",
            ),
        )
        row.add_class("aipy-form-row")
        row.add_class("aipy-field-shell")
        row.add_class(form._anchor_dom_class(path))
        form._assist_anchors[path] = row
        return row

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

    def _validate_default_value(self, value: Any, path: str) -> None:
        return None

    def field_at_parts(self, parts: list[PathPart]) -> "Field":
        if parts:
            raise ValueError(f"Path does not resolve inside field {self.id!r}: {parts!r}")
        return self
