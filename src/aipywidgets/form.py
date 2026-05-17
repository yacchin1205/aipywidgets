from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from html import escape
from typing import Any

from .actions import Action
from .ai import AIAssistManager, AIConversationRunner, PatchProposal
from .field_path import get_in, parse_field_path, set_in
from .fields import Array, Field, Object

logger = logging.getLogger(__name__)


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
        style: dict[str, str] | None = None,
    ) -> None:
        self.title = title
        self.fields = fields or []
        self.steps = steps or []
        self.actions = actions or []
        self.ai_config = ai
        self.style = self._normalize_style(style)
        self.ai = AIAssistManager(self)
        self._validate_schema()
        self._values = self._initial_values()
        self._widgets: dict[str, Any] = {}
        self._hooks: dict[str, list[Callable[[HookContext], None]]] = {}
        self._action_handlers: dict[str, Callable[[ActionContext], None]] = {}
        self._active_hook_paths: list[str] = []
        self._ai_runner = AIConversationRunner(self)
        self._ai_conversation_items: list[dict[str, Any]] = []
        self._ai_chat_events: list[dict[str, str]] = []
        self.proposals: list[PatchProposal] = []
        self.approval_events: list[dict[str, Any]] = []
        self._attention_path: str | None = None
        self._assist_state: dict[str, str] = {}
        self._assist_attention: dict[str, str] = {}
        self._assist_errors: dict[str, BaseException] = {}
        self._assist_surfaces: dict[str, Any] = {}
        self._assist_chat_inputs: dict[str, Any] = {}
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

    def accept_proposal(self, index: int) -> None:
        proposal = self._proposal_at(index)
        if proposal.stale:
            raise RuntimeError("Cannot accept a stale AI proposal")
        for operation in proposal.operations:
            self.set_value(operation.path, operation.value)
        self.approval_events.append({"status": "accepted", "proposal": proposal})
        self._record_approval_result("accepted", proposal)
        del self.proposals[index]
        if proposal.assist_id is not None:
            self._assist_state[proposal.assist_id] = "accepted"
            self._clear_assist_surfaces()

    def reject_proposal(self, index: int) -> None:
        proposal = self._proposal_at(index)
        self.approval_events.append({"status": "rejected", "proposal": proposal})
        self._record_approval_result("rejected", proposal)
        del self.proposals[index]
        if proposal.assist_id is not None:
            self._assist_state[proposal.assist_id] = "chat"
            self._render_assist_surface(proposal.assist_id)
            self._focus_assist_input(proposal.assist_id)

    def mark_assist_dirty(self, assist_id: str, attention_path: str) -> None:
        self.proposals = [
            replace(proposal, stale=True)
            if proposal.assist_id == assist_id and not proposal.stale
            else proposal
            for proposal in self.proposals
        ]
        self._assist_state[assist_id] = "waiting"
        self._assist_attention[assist_id] = attention_path
        self._assist_errors.pop(assist_id, None)
        self._render_assist_surface(assist_id)

    def create_proposal(self, assist_id: str) -> PatchProposal:
        try:
            assist = self.ai.get(assist_id)
            if not self.ai.is_dirty(assist_id):
                raise RuntimeError(f"AI assist has no pending input changes: {assist_id}")
            if not self._assist_is_ready(assist_id):
                raise RuntimeError(f"AI assist is not ready: {assist_id}")
            self._assist_state[assist_id] = "generating"
            self._render_assist_surface(assist_id)
            proposal = self._ai_runner.run(assist)
        except Exception as exc:
            self._assist_state[assist_id] = "error"
            self._assist_errors[assist_id] = exc
            logger.exception("AI assist proposal failed: %s", assist_id)
            self._render_assist_surface(assist_id)
            raise
        self.proposals = [existing for existing in self.proposals if existing.assist_id != assist.id]
        self.proposals.append(proposal)
        self.ai.clear_dirty(assist_id)
        self._assist_errors.pop(assist_id, None)
        self._assist_state[assist_id] = "proposal"
        self._render_assist_surface(assist_id)
        return proposal

    def submit_assist_message(self, assist_id: str, message: str) -> PatchProposal:
        if not message.strip():
            raise ValueError("AI assist message is required")
        try:
            assist = self.ai.get(assist_id)
            self._record_ai_event("user", message.strip())
            self._assist_state[assist_id] = "generating"
            self._render_assist_surface(assist_id)
            proposal = self._ai_runner.run_with_message(assist, message.strip())
        except Exception as exc:
            self._assist_state[assist_id] = "error"
            self._assist_errors[assist_id] = exc
            logger.exception("AI assist chat failed: %s", assist_id)
            self._render_assist_surface(assist_id)
            raise
        self.proposals = [existing for existing in self.proposals if existing.assist_id != assist.id]
        self.proposals.append(proposal)
        self._assist_errors.pop(assist_id, None)
        self._assist_state[assist_id] = "proposal"
        self._render_assist_surface(assist_id)
        return proposal

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
        margin_bottom = self.style.get("margin_bottom")
        if margin_bottom is not None:
            spacer = widgets.Box(layout=widgets.Layout(height=margin_bottom))
            spacer.add_class("aipy-form-margin-bottom")
            children.append(spacer)
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

    def _normalize_style(self, style: dict[str, str] | None) -> dict[str, str]:
        if style is None:
            return {}
        if not isinstance(style, dict):
            raise TypeError("AIForm style must be a dictionary")
        supported = {"margin_bottom"}
        normalized: dict[str, str] = {}
        for key, value in style.items():
            if key not in supported:
                raise ValueError(f"Unsupported AIForm style key: {key}")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"AIForm style value must be a non-empty string: {key}")
            normalized[key] = value
        return normalized

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
        surface = widgets.VBox([], layout=widgets.Layout(height="0px", overflow="visible"))
        surface.add_class("aipy-assist-surface")
        self._assist_surfaces[path] = surface
        shell = widgets.VBox([widget, surface], layout=widgets.Layout(overflow="visible"))
        shell.add_class("aipy-field-shell")
        return shell

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
        self._attention_path = path
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
        self.ai.mark_changed(path)

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

    def _record_ai_event(self, role: str, content: str) -> None:
        self._ai_chat_events.append({"role": role, "content": content})

    def _record_approval_result(self, status: str, proposal: PatchProposal) -> None:
        operations = [
            {"op": operation.op, "path": operation.path, "value": operation.value}
            for operation in proposal.operations
        ]
        content = json.dumps(
            {
                "approval": status,
                "proposal": {
                    "message": proposal.message,
                    "operations": operations,
                },
            },
            ensure_ascii=False,
        )
        self._ai_conversation_items.append({"role": "user", "content": content})
        self._record_ai_event("user", f"{status.capitalize()} proposal.")

    def _focus_assist_input(self, assist_id: str) -> None:
        input_widget = self._assist_chat_inputs.get(assist_id)
        if input_widget is not None and hasattr(input_widget, "focus"):
            input_widget.focus()

    def _render_assist_surface(self, assist_id: str) -> None:
        attention_path = self._assist_attention.get(assist_id)
        if attention_path is None:
            return
        surface = self._assist_surfaces.get(attention_path)
        if surface is None:
            return

        import ipywidgets as widgets

        self._clear_assist_surfaces(except_path=attention_path)
        state = self._assist_state.get(assist_id)
        if state == "waiting":
            surface.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        status="AI will suggest after input settles...",
                        input_disabled=False,
                    )
                ),
            )
            return
        if state == "generating":
            surface.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        status="Generating suggestion...",
                        input_disabled=True,
                    )
                ),
            )
            return
        if state == "error":
            error = self._assist_errors.get(assist_id)
            message = "AI suggestion failed."
            if error is not None:
                message = f"AI suggestion failed: {type(error).__name__}: {error}"
            surface.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        status=message,
                        status_kind="error",
                        input_disabled=False,
                    )
                ),
            )
            return
        if state == "chat":
            surface.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        status="Add instructions to adjust the proposal.",
                        input_disabled=False,
                    )
                ),
            )
            return
        proposal_index = self._proposal_index_for_assist(assist_id)
        if proposal_index is None:
            return
        proposal = self.proposals[proposal_index]
        stale = " <em>(stale)</em>" if proposal.stale else ""
        operations = "<br>".join(
            f"<code>{escape(operation.path)}</code> = {escape(repr(operation.value))}"
            for operation in proposal.operations
        )
        accept_button = widgets.Button(description="Accept", button_style="success")
        reject_button = widgets.Button(description="Reject", button_style="warning")
        accept_button.disabled = proposal.stale
        accept_button.on_click(lambda _button, i=proposal_index: self.accept_proposal(i))
        reject_button.on_click(lambda _button, i=proposal_index: self.reject_proposal(i))
        surface.children = (
            self._assist_bubble(
                self._assist_chat_children(
                    assist_id,
                    proposal_html=(
                        f"<strong>AI proposal{stale}</strong>"
                        f"<p>{escape(proposal.message)}</p>"
                        f"<div class='aipy-assist-operations'>{operations}</div>"
                    ),
                    actions=widgets.HBox([accept_button, reject_button]),
                    input_disabled=True,
                ),
                proposal=True,
            ),
        )

    def _clear_assist_surfaces(self, *, except_path: str | None = None) -> None:
        for path, surface in self._assist_surfaces.items():
            if path == except_path:
                continue
            surface.children = ()

    def _assist_bubble(self, children: list[Any], *, proposal: bool = False):
        import ipywidgets as widgets

        bubble = widgets.VBox(children, layout=widgets.Layout(width="320px", overflow="visible"))
        bubble.add_class("aipy-assist-bubble-wrap")
        if proposal:
            bubble.add_class("aipy-assist-proposal-wrap")
        return bubble

    def _assist_chat_children(
        self,
        assist_id: str,
        *,
        status: str | None = None,
        status_kind: str = "info",
        proposal_html: str | None = None,
        actions: Any | None = None,
        input_disabled: bool,
    ) -> list[Any]:
        import ipywidgets as widgets

        input_widget = widgets.Text(
            placeholder="Add instructions...",
            disabled=input_disabled,
            layout=widgets.Layout(width="232px"),
        )
        send_button = widgets.Button(
            description="Send",
            disabled=input_disabled,
            layout=widgets.Layout(width="70px"),
        )

        def send(_button) -> None:
            message = input_widget.value
            input_widget.value = ""
            self.submit_assist_message(assist_id, message)

        send_button.on_click(send)
        self._on_text_submit(input_widget, send)
        self._assist_chat_inputs[assist_id] = input_widget

        body_parts = [self._assist_css(), "<div class='aipy-assist-panel'>"]
        body_parts.append(self._assist_history_html())
        if status is not None:
            class_name = "aipy-assist-status aipy-assist-error-text" if status_kind == "error" else "aipy-assist-status"
            body_parts.append(f"<div class='{class_name}'>{escape(status)}</div>")
        if proposal_html is not None:
            body_parts.append(f"<div class='aipy-assist-proposal'>{proposal_html}</div>")
        body_parts.append("</div>")

        children: list[Any] = [widgets.HTML("".join(body_parts))]
        if actions is not None:
            children.append(actions)
        input_row = widgets.HBox([input_widget, send_button])
        input_row.add_class("aipy-assist-input-row")
        children.append(input_row)
        return children

    def _on_text_submit(self, input_widget: Any, callback: Callable[[Any], None]) -> None:
        dispatcher = getattr(input_widget, "_submission_callbacks", None)
        if dispatcher is None:
            raise RuntimeError("Assist chat input does not support Enter submission")
        dispatcher.register_callback(callback)

    def _assist_history_html(self) -> str:
        if not self._ai_chat_events:
            return "<div class='aipy-assist-history'></div>"
        rows = []
        for event in reversed(self._ai_chat_events):
            role = escape(event["role"])
            content = escape(event["content"])
            rows.append(
                f"<div class='aipy-assist-event-row aipy-assist-event-row-{role}'>"
                f"<div class='aipy-assist-event aipy-assist-event-{role}'>{content}</div>"
                "</div>"
            )
        return "<div class='aipy-assist-history'>" + "".join(rows) + "</div>"

    def _assist_status_html(self, message: str, *, kind: str = "info") -> str:
        class_name = "aipy-assist-bubble aipy-assist-error" if kind == "error" else "aipy-assist-bubble"
        return f"{self._assist_css()}<div class='{class_name}'><em>{escape(message)}</em></div>"

    def _assist_css(self) -> str:
        return """
<style>
.aipy-field-shell {
  position: relative;
  overflow: visible !important;
  width: max-content;
  max-width: 100%;
  z-index: 2147483647;
}
.aipy-assist-surface {
  position: relative;
  height: 0 !important;
  overflow: visible !important;
  z-index: 2147483647;
}
.aipy-assist-bubble-wrap {
  position: absolute;
  left: calc(100% + 12px);
  top: -40px;
  z-index: 2147483647;
  background: #ffffff;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.18);
  color: #111827;
  padding: 10px 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}
.aipy-assist-panel {
  position: relative;
  background: transparent;
  border: 0;
  box-shadow: none;
  color: inherit;
  padding: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.aipy-assist-panel p {
  margin: 8px 0;
}
.aipy-assist-history {
  border-bottom: 1px solid #e5e7eb;
  margin-bottom: 8px;
  padding-bottom: 6px;
  max-height: 132px;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column-reverse;
}
.aipy-assist-event-row {
  display: flex;
  margin: 4px 0;
}
.aipy-assist-event-row-user {
  justify-content: flex-end;
}
.aipy-assist-event-row-assistant {
  justify-content: flex-start;
}
.aipy-assist-event {
  font-size: 12px;
  line-height: 1.35;
  max-width: 82%;
  padding: 6px 8px;
  border-radius: 6px;
  overflow-wrap: anywhere;
}
.aipy-assist-event-assistant {
  background: #f3f4f6;
  color: #111827;
}
.aipy-assist-event-user {
  background: #dbeafe;
  color: #1e3a8a;
}
.aipy-assist-status {
  font-size: 12px;
  line-height: 1.4;
  margin: 0 0 8px;
}
.aipy-assist-error-text {
  color: #991b1b;
}
.aipy-assist-proposal {
  margin-bottom: 8px;
  max-height: 120px;
  overflow-y: auto;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 8px;
  background: #ffffff;
}
.aipy-assist-error {
  border-color: #dc2626;
  color: #991b1b;
}
.aipy-assist-bubble-wrap:has(.aipy-assist-error)::before {
  border-left-color: #dc2626;
  border-bottom-color: #dc2626;
}
.aipy-assist-operations {
  font-size: 12px;
  line-height: 1.4;
  margin: 0;
}
.aipy-assist-bubble-wrap .widget-hbox {
  margin-top: 8px;
  z-index: 2147483647;
}
.aipy-assist-bubble-wrap .aipy-assist-input-row {
  border-top: 1px solid #e5e7eb;
  padding-top: 8px;
}
.aipy-assist-bubble-wrap .widget-button {
  height: 26px;
}
.aipy-assist-bubble-wrap::before {
  content: "";
  position: absolute;
  left: -7px;
  top: 16px;
  width: 14px;
  height: 14px;
  background: #ffffff;
  border-left: 1px solid #d1d5db;
  border-bottom: 1px solid #d1d5db;
  transform: rotate(45deg);
  box-shadow: -4px 4px 8px rgba(15, 23, 42, 0.06);
}
@media (max-width: 900px) {
  .aipy-assist-bubble-wrap {
    left: 0;
    top: 8px;
  }
  .aipy-assist-bubble-wrap::before {
    display: none;
  }
}
</style>
"""

    def _proposal_at(self, index: int) -> PatchProposal:
        if index < 0 or index >= len(self.proposals):
            raise IndexError(f"Proposal index out of range: {index}")
        return self.proposals[index]

    def _proposal_index_for_assist(self, assist_id: str) -> int | None:
        for index, proposal in enumerate(self.proposals):
            if proposal.assist_id == assist_id:
                return index
        return None

    def _assist_is_ready(self, assist_id: str) -> bool:
        assist = self.ai.get(assist_id)
        for path in assist.watch:
            value = self.get_value(path)
            if isinstance(value, str) and len(value.strip()) < assist.trigger.min_chars:
                return False
        return True
