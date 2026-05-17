from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Field:
    id: str
    label: str | None = None
    default: Any = None
    required: bool = False

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.Text(description=self.label or self.id, value=self.default or "")

    def empty_value(self) -> Any:
        return self.default


@dataclass
class Text(Field):
    def empty_value(self) -> str:
        return self.default or ""

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.Text(description=self.label or self.id, value=self.default or "")


@dataclass
class Textarea(Field):
    def empty_value(self) -> str:
        return self.default or ""

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.Textarea(description=self.label or self.id, value=self.default or "")


@dataclass
class Int(Field):
    default: int = 0

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.IntText(description=self.label or self.id, value=self.default)


@dataclass
class Float(Field):
    default: float = 0.0

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.FloatText(description=self.label or self.id, value=self.default)


@dataclass
class Checkbox(Field):
    default: bool = False

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.Checkbox(description=self.label or self.id, value=self.default)


@dataclass
class Select(Field):
    options: list[str] = field(default_factory=list)

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.Dropdown(description=self.label or self.id, options=self.options, value=self.default)


@dataclass
class Tags(Field):
    default: list[str] = field(default_factory=list)

    def make_widget(self):
        import ipywidgets as widgets

        return widgets.TagsInput(
            description=self.label or self.id,
            value=list(self.default),
            layout=widgets.Layout(width="300px"),
        )


@dataclass
class File(Field):
    def make_widget(self):
        import ipywidgets as widgets

        return widgets.FileUpload(description=self.label or self.id, multiple=False)

    def empty_value(self) -> Any:
        return None


@dataclass
class Object(Field):
    fields: list[Field] = field(default_factory=list)
    id: str = ""

    def __init__(
        self,
        id: str = "",
        *,
        label: str | None = None,
        fields: list[Field] | None = None,
        default: dict[str, Any] | None = None,
        required: bool = False,
    ) -> None:
        super().__init__(id=id, label=label, default=default, required=required)
        self.fields = fields or []

    def empty_value(self) -> dict[str, Any]:
        if self.default is not None:
            return deepcopy(self.default)
        return {child.id: child.empty_value() for child in self.fields}

    def make_widget(self):
        import ipywidgets as widgets

        children = [child.make_widget() for child in self.fields]
        if self.label or self.id:
            return widgets.VBox([widgets.HTML(f"<strong>{self.label or self.id}</strong>"), *children])
        return widgets.VBox(children)


@dataclass
class Array(Field):
    item: Field | None = None
    default: list[Any] = field(default_factory=list)

    def empty_value(self) -> list[Any]:
        return deepcopy(self.default)

    def make_widget(self):
        import ipywidgets as widgets

        label = widgets.HTML(f"<strong>{self.label or self.id}</strong>")
        note = widgets.HTML("<em>Array editing UI is not implemented yet.</em>")
        return widgets.VBox([label, note])
