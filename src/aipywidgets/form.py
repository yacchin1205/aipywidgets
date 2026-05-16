from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .actions import Action
from .field_path import get_in, parse_field_path, set_in
from .fields import Array, Field, Object


@dataclass
class HookContext:
    form: "AIForm"
    path: str
    value: Any

    @property
    def values(self) -> dict[str, Any]:
        return self.form.get_values()

    def get_value(self, path: str) -> Any:
        return self.form.get_value(self._resolve(path))

    def set_value(self, path: str, value: Any) -> None:
        self.form.set_value(self._resolve(path), value)

    def info(self, message: str) -> None:
        self.form.info(message)

    def error(self, message: str) -> None:
        self.form.error(message)

    def _resolve(self, path: str) -> str:
        if path == ".":
            return self.path
        if path.startswith("./"):
            prefix = self.path.rsplit(".", 1)[0] if "." in self.path else ""
            return f"{prefix}.{path[2:]}" if prefix else path[2:]
        return path


@dataclass
class ActionContext:
    form: "AIForm"
    action: Action

    @property
    def values(self) -> dict[str, Any]:
        return self.form.get_values()

    def get_value(self, path: str) -> Any:
        return self.form.get_value(path)

    def set_value(self, path: str, value: Any) -> None:
        self.form.set_value(path, value)

    def info(self, message: str) -> None:
        self.form.info(message)

    def error(self, message: str) -> None:
        self.form.error(message)


class AIForm:
    def __init__(
        self,
        *,
        title: str | None = None,
        fields: list[Field] | None = None,
        steps: list[dict[str, Any]] | None = None,
        actions: list[Action] | None = None,
        ai: Any | None = None,
    ) -> None:
        self.title = title
        self.fields = fields or []
        self.steps = steps or []
        self.actions = actions or []
        self.ai = ai
        self._validate_schema()
        self._values = self._initial_values()
        self._widgets: dict[str, Any] = {}
        self._hooks: dict[str, list[Callable[[HookContext], None]]] = {}
        self._action_handlers: dict[str, Callable[[ActionContext], None]] = {}
        self._active_hook_paths: list[str] = []
        self._message_widget = None

    def get_values(self) -> dict[str, Any]:
        return self._values

    def get_value(self, path: str) -> Any:
        return get_in(self._values, path)

    def set_value(self, path: str, value: Any) -> None:
        if path in self._active_hook_paths:
            chain = " -> ".join([*self._active_hook_paths, path])
            raise RuntimeError(f"Cyclic hook update detected: {chain}")
        set_in(self._values, path, value)
        widget = self._widgets.get(path)
        if widget is not None and hasattr(widget, "value"):
            widget.value = value
        self._run_hooks(path, value)

    def on_change(self, path: str):
        parse_field_path(path)

        def decorator(func: Callable[[HookContext], None]):
            self._hooks.setdefault(path, []).append(func)
            return func

        return decorator

    def on_action(self, action_id: str):
        def decorator(func: Callable[[ActionContext], None]):
            if action_id in self._action_handlers:
                raise ValueError(f"Action handler is already registered: {action_id}")
            if action_id not in {action.id for action in self.actions}:
                raise ValueError(f"Unknown action id: {action_id}")
            self._action_handlers[action_id] = func
            return func

        return decorator

    def info(self, message: str) -> None:
        self._set_message(message, kind="info")

    def error(self, message: str) -> None:
        self._set_message(message, kind="error")

    def _repr_mimebundle_(self, **kwargs):
        return self.widget()._repr_mimebundle_(**kwargs)

    def widget(self):
        import ipywidgets as widgets

        children: list[Any] = []
        if self.title:
            children.append(widgets.HTML(f"<h3>{self.title}</h3>"))
        if self.steps:
            children.extend(self._step_widgets())
        else:
            children.extend(self._field_widgets(self.fields))
        if self.actions:
            children.append(self._actions_widget())
        self._message_widget = widgets.HTML("")
        children.append(self._message_widget)
        return widgets.VBox(children)

    def _initial_values(self) -> dict[str, Any]:
        if self.steps:
            all_fields: list[Field] = []
            for step in self.steps:
                all_fields.extend(step["fields"])
        else:
            all_fields = self.fields
        return {field.id: field.empty_value() for field in all_fields}

    def _validate_schema(self) -> None:
        if self.fields and self.steps:
            raise ValueError("AIForm cannot define both fields and steps")
        if self.steps:
            step_ids: set[str] = set()
            for step in self.steps:
                if "fields" not in step:
                    raise ValueError(f"Step is missing required 'fields': {step!r}")
                if not isinstance(step["fields"], list):
                    raise TypeError(f"Step fields must be a list: {step!r}")
                step_id = step.get("id")
                if step_id is not None:
                    if step_id in step_ids:
                        raise ValueError(f"Duplicate step id: {step_id}")
                    step_ids.add(step_id)
                self._step_title(step)
                self._validate_fields(step["fields"], owner=f"step {step.get('id')!r}")
        else:
            self._validate_fields(self.fields, owner="form")
        action_ids: set[str] = set()
        for action in self.actions:
            if not action.id:
                raise ValueError("Action id is required")
            if action.id in action_ids:
                raise ValueError(f"Duplicate action id: {action.id}")
            action_ids.add(action.id)

    def _validate_fields(self, fields: list[Field], *, owner: str) -> None:
        seen: set[str] = set()
        for field in fields:
            if not field.id:
                raise ValueError(f"Field id is required in {owner}")
            if field.id in seen:
                raise ValueError(f"Duplicate field id in {owner}: {field.id}")
            seen.add(field.id)
            if isinstance(field, Object):
                self._validate_fields(field.fields, owner=f"object {field.id!r}")
            if isinstance(field, Array):
                if field.item is None:
                    raise ValueError(f"Array field requires an item schema: {field.id}")
                if isinstance(field.item, Object):
                    self._validate_fields(field.item.fields, owner=f"array {field.id!r} item")

    def _field_widgets(self, fields: list[Field], prefix: str = "") -> list[Any]:
        widgets = []
        for field in fields:
            path = f"{prefix}.{field.id}" if prefix else field.id
            widgets.append(self._field_widget(field, path))
        return widgets

    def _field_widget(self, field: Field, path: str):
        import ipywidgets as widgets

        if isinstance(field, Object):
            heading = widgets.HTML(f"<strong>{field.label or field.id}</strong>")
            children = self._field_widgets(field.fields, prefix=path)
            return widgets.VBox([heading, *children])

        if isinstance(field, Array):
            return self._array_widget(field, path)

        widget = field.make_widget()
        self._sync_widget_value(widget, path)
        self._widgets[path] = widget
        if hasattr(widget, "observe"):
            widget.observe(lambda change, p=path: self._on_widget_change(p, change), names="value")
        return widget

    def _array_widget(self, field: Array, path: str):
        import ipywidgets as widgets

        title = widgets.HTML(f"<strong>{field.label or field.id}</strong>")
        items_box = widgets.VBox([])
        add_button = widgets.Button(description="Add", icon="plus")

        def render_items() -> None:
            values = self.get_value(path)
            item_widgets = []
            for index, _value in enumerate(values):
                item_path = f"{path}[{index}]"
                if isinstance(field.item, Object):
                    item_widgets.append(
                        widgets.VBox(
                            [
                                widgets.HTML(f"<em>Item {index + 1}</em>"),
                                *self._field_widgets(field.item.fields, prefix=item_path),
                            ]
                        )
                    )
                else:
                    item_widgets.append(self._field_widget(field.item, item_path))
            items_box.children = tuple(item_widgets)

        def add_item(_button) -> None:
            values = list(self.get_value(path))
            values.append(field.item.empty_value())
            set_in(self._values, path, values)
            render_items()

        add_button.on_click(add_item)
        render_items()
        return widgets.VBox([title, items_box, add_button])

    def _step_widgets(self) -> list[Any]:
        import ipywidgets as widgets

        step_children = []
        for step in self.steps:
            label = self._step_title(step)
            heading = widgets.HTML(f"<h4>{label}</h4>")
            step_children.append(widgets.VBox([heading, *self._field_widgets(step["fields"])]))
        return [
            widgets.Accordion(
                children=step_children,
                titles=tuple(self._step_title(step) for step in self.steps),
            )
        ]

    def _actions_widget(self):
        import ipywidgets as widgets

        buttons = []
        for action in self.actions:
            style = "success" if action.style == "primary" else ""
            button = widgets.Button(description=action.label, button_style=style)
            button.on_click(lambda _button, a=action: self._run_action(a))
            buttons.append(button)
        return widgets.HBox(buttons)

    def _on_widget_change(self, path: str, change: dict[str, Any]) -> None:
        if change["name"] != "value":
            raise ValueError(f"Unexpected widget change event for {path}: {change!r}")
        self.set_value(path, change["new"])

    def _sync_widget_value(self, widget: Any, path: str) -> None:
        if not hasattr(widget, "value"):
            raise TypeError(f"Field widget for {path!r} does not expose a value attribute")
        widget.value = self.get_value(path)

    def _run_hooks(self, path: str, value: Any) -> None:
        for hook_path, funcs in self._hooks.items():
            if hook_path != path:
                continue
            self._active_hook_paths.append(path)
            try:
                ctx = HookContext(form=self, path=path, value=value)
                for func in funcs:
                    func(ctx)
            finally:
                self._active_hook_paths.pop()

    def _run_action(self, action: Action) -> None:
        handler = self._action_handlers.get(action.id)
        if handler is None:
            raise RuntimeError(f"No handler registered for action: {action.id}")
        handler(ActionContext(form=self, action=action))

    def _set_message(self, message: str, *, kind: str) -> None:
        if self._message_widget is None:
            raise RuntimeError("Cannot display a form message before widget() has been rendered")
        color = "#b00020" if kind == "error" else "#174ea6"
        self._message_widget.value = f"<span style='color: {color}'>{message}</span>"

    def _step_title(self, step: dict[str, Any]) -> str:
        label = step.get("label") or step.get("id")
        if label is None:
            raise ValueError(f"Step is missing 'id' or 'label': {step!r}")
        return label
