from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

import ipywidgets as widgets

from .base import Field


@dataclass
class DisplayField(Field):
    content: str = ""

    def _configure_widget(self, widget):
        widget.layout.margin = "0"
        if self.full_width:
            widget.layout.width = "100%"
        return widget

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
    def render(self, form, path: str, allocation, grid):
        return self._configure_widget(widgets.HTML(f"<div>{escape(self.content)}</div>"))


@dataclass
class Headline(DisplayField):
    def render(self, form, path: str, allocation, grid):
        return self._configure_widget(widgets.HTML(f"<h4>{escape(self.content)}</h4>"))


@dataclass
class HeadlineWithLine(DisplayField):
    def render(self, form, path: str, allocation, grid):
        return self._configure_widget(
            widgets.HTML(
                "<div>"
                f"<h4 style='margin: 0 0 8px 0;'>{escape(self.content)}</h4>"
                "<div style='border-top: 1px solid #d0d7de;'></div>"
                "</div>"
            )
        )


@dataclass
class HorizontalLine(DisplayField):
    def render(self, form, path: str, allocation, grid):
        return self._configure_widget(
            widgets.HTML("<hr style='border: 0; border-top: 1px solid #d0d7de; margin: 12px 0;'>")
        )
