from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import ipywidgets as widgets

from ..field_path import PathPart
from .base import Field


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
        full_width: bool = False,
        validator: Callable[[Any, dict[str, Any]], str | None] | None = None,
    ) -> None:
        super().__init__(
            id=id,
            label=label,
            default=default,
            required=required,
            full_width=full_width,
            validator=validator,
        )
        self.fields = fields or []

    def empty_value(self) -> dict[str, Any]:
        if self.default is not None:
            return deepcopy(self.default)
        return {child.id: child.empty_value() for child in self.fields if child.stores_value()}

    def make_widget(self):
        children = [child.make_widget() for child in self.fields]
        if self.label or self.id:
            return widgets.VBox([widgets.HTML(f"<strong>{self.label or self.id}</strong>"), *children])
        return widgets.VBox(children)

    def render(self, form, path: str, allocation, grid):
        heading = widgets.HTML(f"<strong>{self.label or self.id}</strong>")
        error_widget = widgets.HTML("")
        form._register_field(path, self, error_widget)
        children = []
        for child in self.fields:
            child_path = f"{path}.{child.id}" if path else child.id
            children.append(child.render(form, child_path, type(allocation)(start=1, span=grid.columns), grid))
        box = widgets.VBox([heading, error_widget, *children])
        if self.full_width:
            box.layout.width = "100%"
        return box

    def validate_tree(self, form, path: str) -> dict[str, str]:
        errors = self.validate_path(form, path)
        for child in self.fields:
            child_path = f"{path}.{child.id}" if path else child.id
            errors.update(child.validate_tree(form, child_path))
        return errors

    def validate_schema(self, validate_fields: Callable[[list["Field"], str], None], owner: str) -> None:
        validate_fields(self.fields, owner=f"object {self.id!r}")
        if self.default is not None:
            self._validate_default_value(self.default, self.id or owner)

    def _validate_default_value(self, value: Any, path: str) -> None:
        if not isinstance(value, dict):
            raise TypeError(f"Object field default must be a dict: {path}")
        for child in self.fields:
            if not child.stores_value():
                continue
            if child.id not in value:
                raise ValueError(f"Missing object field default for {path}.{child.id}")
            child_path = f"{path}.{child.id}" if path else child.id
            child._validate_default_value(value[child.id], child_path)

    def field_at_parts(self, parts: list[PathPart]) -> Field:
        if not parts:
            return self
        head = parts[0]
        if head.name is None:
            raise ValueError(f"Object path must continue with a child name: {parts!r}")
        for child in self.fields:
            if child.id == head.name:
                return child.field_at_parts(parts[1:])
        raise ValueError(f"Unknown object child {head.name!r} in field {self.id!r}")


@dataclass
class Array(Field):
    item: Field | None = None
    default: list[Any] = field(default_factory=list)

    def empty_value(self) -> list[Any]:
        return deepcopy(self.default)

    def make_widget(self):
        label = widgets.HTML(f"<strong>{self.label or self.id}</strong>")
        note = widgets.HTML("<em>Array editing UI is not implemented yet.</em>")
        return widgets.VBox([label, note])

    def render(self, form, path: str, allocation, grid):
        title = widgets.HTML(f"<strong>{self.label or self.id}</strong>")
        items_box = widgets.VBox([])
        add_button = widgets.Button(description="Add", icon="plus")
        error_widget = widgets.HTML("")
        form._register_field(path, self, error_widget)

        def render_items() -> None:
            values = form.get_value(path)
            if not isinstance(values, list):
                raise TypeError(f"Array field value must be a list: {path}")
            item_widgets = []
            for index, _value in enumerate(values):
                item_path = f"{path}[{index}]"
                remove_button = widgets.Button(description="Remove", icon="trash")
                remove_button.on_click(lambda _button, i=index: remove_item(i))
                item_widgets.append(
                    widgets.VBox(
                        [
                            widgets.HTML(f"<em>Item {index + 1}</em>"),
                            self.item.render(form, item_path, type(allocation)(start=1, span=grid.columns), grid),
                            remove_button,
                        ]
                    )
                )
                if self.full_width:
                    item_widgets[-1].layout.width = "100%"
            items_box.children = tuple(item_widgets)

        def add_item(_button) -> None:
            values = list(form.get_value(path))
            values.append(self.item.empty_value())
            form._set_value_without_widget_sync(path, values)
            render_items()

        def remove_item(index: int) -> None:
            values = list(form.get_value(path))
            del values[index]
            form._set_value_without_widget_sync(path, values)
            render_items()

        add_button.on_click(add_item)
        render_items()
        box = widgets.VBox([title, error_widget, items_box, add_button])
        if self.full_width:
            box.layout.width = "100%"
            items_box.layout.width = "100%"
        return box

    def validate_tree(self, form, path: str) -> dict[str, str]:
        errors = self.validate_path(form, path)
        values = form.get_value(path)
        if not isinstance(values, list):
            raise TypeError(f"Array field value must be a list: {path}")
        for index, _value in enumerate(values):
            item_path = f"{path}[{index}]"
            errors.update(self.item.validate_tree(form, item_path))
        return errors

    def validate_schema(self, validate_fields: Callable[[list["Field"], str], None], owner: str) -> None:
        if self.item is None:
            raise ValueError(f"Array field requires an item schema: {self.id}")
        self.item.validate_schema(validate_fields, owner=f"array {self.id!r} item")
        self._validate_default_value(self.default, self.id or owner)

    def _validate_default_value(self, value: Any, path: str) -> None:
        if not isinstance(value, list):
            raise TypeError(f"Array field default must be a list: {path}")
        if self.item is None:
            raise ValueError(f"Array field requires an item schema: {self.id}")
        for index, item_value in enumerate(value):
            self.item._validate_default_value(item_value, f"{path}[{index}]")

    def field_at_parts(self, parts: list[PathPart]) -> Field:
        if not parts:
            return self
        head = parts[0]
        if head.index is None:
            raise ValueError(f"Array path must continue with an index: {parts!r}")
        return self.item.field_at_parts(parts[1:])
