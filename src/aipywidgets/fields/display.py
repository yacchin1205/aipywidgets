from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

import ipywidgets as widgets

from .base import Field


@dataclass
class DisplayField(Field):
    content: str = ""

    def stores_value(self) -> bool:
        return False

    def empty_value(self) -> Any:
        return None

    def validate_path(self, form, path: str) -> dict[str, str]:
        return {}

    def validate_tree(self, form, path: str) -> dict[str, str]:
        return {}


@dataclass
class Expression(DisplayField):
    def render(self, form, path: str):
        widget = widgets.HTML(f"<div>{escape(self.content)}</div>")
        if self.full_width:
            widget.layout.width = "100%"
        return widget


@dataclass
class Headline(DisplayField):
    def render(self, form, path: str):
        widget = widgets.HTML(f"<h4>{escape(self.content)}</h4>")
        if self.full_width:
            widget.layout.width = "100%"
        return widget


@dataclass
class HeadlineWithLine(DisplayField):
    def render(self, form, path: str):
        widget = widgets.HTML(
            "<div style='display: flex; align-items: center; gap: 12px;'>"
            f"<h4 style='margin: 0;'>{escape(self.content)}</h4>"
            "<div style='flex: 1; border-top: 1px solid #d0d7de;'></div>"
            "</div>"
        )
        if self.full_width:
            widget.layout.width = "100%"
        return widget


@dataclass
class HorizontalLine(DisplayField):
    def render(self, form, path: str):
        widget = widgets.HTML("<hr style='border: 0; border-top: 1px solid #d0d7de; margin: 12px 0;'>")
        if self.full_width:
            widget.layout.width = "100%"
        return widget
