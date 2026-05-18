from __future__ import annotations

from dataclasses import dataclass, field

import ipywidgets as widgets

from .base import Field


@dataclass
class Text(Field):
    def empty_value(self) -> str:
        return self.default or ""

    def make_widget(self):
        return widgets.Text(description=self.label or self.id, value=self.default or "")


@dataclass
class Textarea(Field):
    def empty_value(self) -> str:
        return self.default or ""

    def make_widget(self):
        return widgets.Textarea(description=self.label or self.id, value=self.default or "")


@dataclass
class Int(Field):
    default: int = 0

    def make_widget(self):
        return widgets.IntText(description=self.label or self.id, value=self.default)


@dataclass
class Float(Field):
    default: float = 0.0

    def make_widget(self):
        return widgets.FloatText(description=self.label or self.id, value=self.default)


@dataclass
class Checkbox(Field):
    default: bool = False

    def make_widget(self):
        return widgets.Checkbox(description=self.label or self.id, value=self.default)


@dataclass
class Select(Field):
    options: list[str] = field(default_factory=list)

    def make_widget(self):
        return widgets.Dropdown(description=self.label or self.id, options=self.options, value=self.default)


@dataclass
class Tags(Field):
    default: list[str] = field(default_factory=list)

    def make_widget(self):
        return widgets.TagsInput(
            description=self.label or self.id,
            value=list(self.default),
            layout=widgets.Layout(width="300px"),
        )


@dataclass
class File(Field):
    def make_widget(self):
        return widgets.FileUpload(description=self.label or self.id, multiple=False)

    def empty_value(self):
        return None
