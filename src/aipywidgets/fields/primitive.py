from __future__ import annotations

from dataclasses import dataclass, field

import ipywidgets as widgets

from .base import Field


@dataclass
class Text(Field):
    def empty_value(self) -> str:
        return self.default or ""

    def make_control(self):
        return widgets.Text(value=self.default or "")


@dataclass
class Textarea(Field):
    def empty_value(self) -> str:
        return self.default or ""

    def make_control(self):
        return widgets.Textarea(value=self.default or "")


@dataclass
class Int(Field):
    default: int = 0

    def make_control(self):
        return widgets.IntText(value=self.default)


@dataclass
class Float(Field):
    default: float = 0.0

    def make_control(self):
        return widgets.FloatText(value=self.default)


@dataclass
class Checkbox(Field):
    default: bool = False

    def make_control(self):
        return widgets.Checkbox(value=self.default, indent=False)

    def render(self, form, path: str, allocation, grid):
        input_widget = self.make_control()
        input_widget.layout.width = "auto"
        input_widget.layout.margin = "0"
        error_widget = widgets.HTML("")
        form._sync_widget_value(input_widget, path)
        form._widgets[path] = input_widget
        if hasattr(input_widget, "observe"):
            input_widget.observe(lambda change, p=path: form._on_widget_change(p, change), names="value")
        form._register_field(path, self, error_widget)
        label = widgets.HTML(self.make_label_widget().value)
        control_row = widgets.HBox(
            [input_widget, label],
            layout=widgets.Layout(align_items="center"),
        )
        control_shell = widgets.VBox([control_row, error_widget], layout=widgets.Layout(overflow="visible"))
        control_shell.add_class("aipy-field-shell")
        control_shell.add_class(form._anchor_dom_class(path))
        form._assist_anchors[path] = control_shell
        return control_shell


@dataclass
class Select(Field):
    options: list[str] = field(default_factory=list)

    def make_control(self):
        return widgets.Dropdown(options=self.options, value=self.default)


@dataclass
class Tags(Field):
    default: list[str] = field(default_factory=list)

    def make_control(self):
        return widgets.TagsInput(
            value=list(self.default),
            layout=widgets.Layout(width="300px"),
        )


@dataclass
class File(Field):
    def make_control(self):
        return widgets.FileUpload(multiple=False)

    def empty_value(self):
        return None
