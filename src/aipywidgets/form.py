from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from html import escape
from typing import Any

import ipywidgets as widgets

from .actions import Action
from .assist_layer import AssistLayer
from .ai import AIAssistManager, AIConversationRunner, PatchProposal, ToolApprovalProposal
from .field_path import get_in, parse_field_path, set_in
from .fields import Field

logger = logging.getLogger(__name__)
_FORM_RENDER_COUNTER = 0


@dataclass(frozen=True)
class GridSpec:
    columns: int = 12
    label_units: int = 3
    column_gap: str = "12px"
    row_gap: str = "6px"
    inline_threshold_units: int = 8


@dataclass(frozen=True)
class FieldAllocation:
    start: int
    span: int


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
        steps: list[dict[str, Any]],
        actions: list[Action],
        ai: Any | None = None,
        style: dict[str, str] | None = None,
    ) -> None:
        self.title = title
        self.steps = steps
        self.actions = actions
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
        self.proposals: list[PatchProposal | ToolApprovalProposal] = []
        self.approval_events: list[dict[str, Any]] = []
        self._attention_path: str | None = None
        self._assist_state: dict[str, str] = {}
        self._assist_attention: dict[str, str] = {}
        self._assist_errors: dict[str, BaseException] = {}
        self._assist_surfaces: dict[str, Any] = {}
        self._assist_anchors: dict[str, Any] = {}
        self._assist_chat_inputs: dict[str, Any] = {}
        self._error_widgets: dict[str, Any] = {}
        self._field_defs: dict[str, Field] = {}
        self._current_step_index = 0
        self._dom_token = self._next_dom_token()
        self._step_title_widget = None
        self._step_summary_widget = None
        self._step_body_widget = None
        self._step_nav_widget = None
        self._actions_box = None
        self._errors: dict[str, str] = {}
        self._message_widget = None
        self._root_widget = None
        self._assist_layer_widget = None
        self._grid = GridSpec()

    def get_values(self) -> dict[str, Any]:
        return self._values

    def get_value(self, path: str) -> Any:
        return get_in(self._values, path)

    def set_value(self, path: str, value: Any) -> None:
        if path in self._active_hook_paths:
            chain = " -> ".join([*self._active_hook_paths, path])
            raise RuntimeError(f"Cyclic hook update detected: {chain}")
        self._set_value_without_widget_sync(path, value)
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
        del self.proposals[index]
        if isinstance(proposal, PatchProposal):
            for operation in proposal.operations:
                self.set_value(operation.path, operation.value)
            self.approval_events.append({"status": "accepted", "proposal": proposal})
            self._record_patch_approval_result("accepted", proposal)
            if proposal.assist_id is not None:
                self._assist_state[proposal.assist_id] = "accepted"
                self._clear_assist_surfaces()
            return
        if proposal.assist_id is None:
            raise RuntimeError("External tool proposal is missing assist_id")
        self._assist_state[proposal.assist_id] = "generating"
        self._render_assist_surface(proposal.assist_id)
        try:
            next_proposal = self._ai_runner.continue_after_tool_approval(proposal)
        except Exception:
            self._assist_state[proposal.assist_id] = "error"
            self._render_assist_surface(proposal.assist_id)
            raise
        self.proposals = [existing for existing in self.proposals if existing.assist_id != proposal.assist_id]
        self.proposals.append(next_proposal)
        self.approval_events.append({"status": "accepted", "proposal": proposal})
        self._assist_errors.pop(proposal.assist_id, None)
        self._assist_state[proposal.assist_id] = "proposal"
        self._render_assist_surface(proposal.assist_id)

    def reject_proposal(self, index: int) -> None:
        proposal = self._proposal_at(index)
        self.approval_events.append({"status": "rejected", "proposal": proposal})
        del self.proposals[index]
        if isinstance(proposal, PatchProposal):
            self._record_patch_approval_result("rejected", proposal)
        else:
            self._record_tool_rejection(proposal)
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

    def create_proposal(self, assist_id: str) -> PatchProposal | ToolApprovalProposal:
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

    def submit_assist_message(self, assist_id: str, message: str) -> PatchProposal | ToolApprovalProposal:
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
        children: list[Any] = []
        if self.title:
            children.append(widgets.HTML(f"<h3>{self.title}</h3>"))
        self._step_title_widget = widgets.HTML("")
        self._step_summary_widget = widgets.HTML("")
        self._step_body_widget = widgets.VBox([], layout=widgets.Layout(width="100%"))
        self._step_nav_widget = widgets.HBox(
            [],
            layout=widgets.Layout(width="100%", justify_content="flex-end"),
        )
        children.extend(
            [
                self._step_title_widget,
                self._step_summary_widget,
                self._step_body_widget,
                self._step_nav_widget,
            ]
        )
        self._message_widget = widgets.HTML("")
        children.append(self._message_widget)
        self._assist_layer_widget = AssistLayer(layout=widgets.Layout(width="100%", height="0px", overflow="visible"))
        self._assist_layer_widget.form_dom_class = self._form_dom_class()
        self._assist_layer_widget.add_class("aipy-assist-layer")
        children.append(self._assist_layer_widget)
        margin_bottom = self.style.get("margin_bottom")
        if margin_bottom is not None:
            spacer = widgets.Box(layout=widgets.Layout(height=margin_bottom))
            spacer.add_class("aipy-form-margin-bottom")
            children.append(spacer)
        root = widgets.VBox(children, layout=widgets.Layout(width="100%"))
        root.add_class(self._form_dom_class())
        self._root_widget = root
        self._render_current_step()
        return root

    def _initial_values(self) -> dict[str, Any]:
        return {field.id: field.empty_value() for field in self._all_fields() if field.stores_value()}

    def _validate_schema(self) -> None:
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
        self._validate_fields(self._all_fields(), owner="form")
        if not self.actions:
            raise ValueError("At least one action is required")
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
            field.validate_schema(self._validate_fields, owner)

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

    def _all_fields(self) -> list[Field]:
        all_fields: list[Field] = []
        for step in self.steps:
            all_fields.extend(step["fields"])
        return all_fields

    def _step_fields(self, step_index: int) -> list[Field]:
        return self.steps[step_index]["fields"]

    def _field_widgets(self, step: dict[str, Any], prefix: str = "") -> list[Any]:
        rows = step.get("layout")
        if rows is None:
            rows = [[field] for field in step["fields"]]
        rendered = []
        for row in rows:
            allocations = self._row_allocations(row)
            row_children = []
            for item, allocation in zip(row, allocations, strict=True):
                field = item["field"] if isinstance(item, dict) else item
                path = f"{prefix}.{field.id}" if prefix else field.id
                widget = field.render(self, path, allocation, self._grid)
                cell = widgets.Box(
                    [widget],
                    layout=widgets.Layout(
                        width="auto",
                        min_width="0",
                        flex=f"{allocation.span} {allocation.span} 0",
                        overflow="visible",
                    ),
                )
                row_children.append(cell)
            row_widget = widgets.HBox(
                row_children,
                layout=widgets.Layout(
                    width="100%",
                    align_items="flex-start",
                    overflow="visible",
                ),
            )
            row_widget.add_class("aipy-form-row")
            rendered.append(row_widget)
        return rendered

    def _row_allocations(self, row: list[Any]) -> list[FieldAllocation]:
        if not row:
            return []
        explicit_spans = []
        auto_items = 0
        for item in row:
            if isinstance(item, dict) and "span" in item:
                explicit_spans.append(int(item["span"]))
            else:
                explicit_spans.append(None)
                auto_items += 1
        used = sum(span for span in explicit_spans if span is not None)
        if used > self._grid.columns:
            raise ValueError(f"Row spans exceed grid width: {used} > {self._grid.columns}")
        remaining = self._grid.columns - used
        auto_span = remaining // auto_items if auto_items else 0
        remainder = remaining % auto_items if auto_items else 0
        allocations: list[FieldAllocation] = []
        start = 1
        for span in explicit_spans:
            if span is None:
                span = auto_span
                if remainder:
                    span += 1
                    remainder -= 1
            allocations.append(FieldAllocation(start=start, span=span))
            start += span
        return allocations

    def _render_current_step(self) -> None:
        step = self.steps[self._current_step_index]
        label = escape(self._step_title(step))
        self._widgets = {}
        self._assist_surfaces = {}
        self._assist_anchors = {}
        self._assist_chat_inputs = {}
        self._error_widgets = {}
        self._field_defs = {}
        if self._assist_layer_widget is not None:
            self._assist_layer_widget.children = ()
        self._step_title_widget.value = (
            f"<h4>Step {self._current_step_index + 1} of {len(self.steps)}: {label}</h4>"
        )
        self._step_body_widget.children = tuple(self._field_widgets(step))
        self._refresh_step_errors()
        nav_children = []
        if self._current_step_index > 0:
            previous_button = widgets.Button(description="Previous")
            previous_button.on_click(self._go_previous)
            nav_children.append(previous_button)
        if self._current_step_index == len(self.steps) - 1:
            self._actions_box = self._actions_widget()
            nav_children.append(self._actions_box)
            self._step_nav_widget.children = tuple(nav_children)
            return
        next_button = widgets.Button(description="Next", button_style="primary")
        next_button.on_click(self._go_next)
        nav_children.append(next_button)
        self._step_nav_widget.children = tuple(nav_children)

    def _actions_widget(self):
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

    def _register_field(self, path: str, field: Field, error_widget: Any) -> None:
        self._field_defs[path] = field
        self._error_widgets[path] = error_widget

    def _set_value_without_widget_sync(self, path: str, value: Any) -> None:
        set_in(self._values, path, value)
        self._refresh_field_errors(path)

    def _run_action(self, action: Action) -> None:
        if not self._validate_form():
            return
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

    def _go_previous(self, _button) -> None:
        if self._current_step_index == 0:
            return
        self._current_step_index -= 1
        self._render_current_step()

    def _go_next(self, _button) -> None:
        if self._current_step_index == len(self.steps) - 1:
            return
        if not self._validate_step(self._current_step_index):
            return
        self._current_step_index += 1
        self._render_current_step()

    def _validate_form(self) -> bool:
        first_invalid_step_index: int | None = None
        valid = True
        for step_index in range(len(self.steps)):
            if not self._validate_step(step_index):
                if first_invalid_step_index is None:
                    first_invalid_step_index = step_index
                valid = False
        if not valid and first_invalid_step_index is not None and first_invalid_step_index != self._current_step_index:
            self._current_step_index = first_invalid_step_index
            self._render_current_step()
        return valid

    def _validate_step(self, step_index: int) -> bool:
        step_fields = self._step_fields(step_index)
        for path in self._field_defs:
            self._errors.pop(path, None)
        valid = True
        for field in step_fields:
            path = field.id
            errors = field.validate_tree(self, path)
            if errors:
                self._errors.update(errors)
                valid = False
        if step_index == self._current_step_index:
            self._refresh_step_errors()
        return valid

    def _refresh_step_errors(self) -> None:
        if self._step_summary_widget is None:
            return
        step_paths = set(self._field_defs)
        step_errors = {path: message for path, message in self._errors.items() if path in step_paths}
        for path, widget in self._error_widgets.items():
            message = step_errors.get(path, "")
            widget.value = f"<div style='color: #b00020; font-size: 0.9em;'>{escape(message)}</div>" if message else ""
        if step_errors:
            count = len(step_errors)
            noun = "error" if count == 1 else "errors"
            self._step_summary_widget.value = f"<div style='color: #b00020;'>{count} {noun} in this step.</div>"
        else:
            self._step_summary_widget.value = ""

    def _refresh_field_errors(self, path: str) -> None:
        field = self._field_for_path(path)
        errors = field.validate_tree(self, path)
        paths_to_clear = [existing_path for existing_path in self._errors if existing_path == path or existing_path.startswith(f"{path}.") or existing_path.startswith(f"{path}[")]
        for existing_path in paths_to_clear:
            del self._errors[existing_path]
        self._errors.update(errors)
        self._refresh_step_errors()

    def _field_for_path(self, path: str) -> Field:
        parts = parse_field_path(path)
        if not parts:
            raise ValueError("Path must resolve to a field")
        head = parts[0]
        if head.name is None:
            raise ValueError(f"Top-level path must start with a field name: {path!r}")
        for field in self._all_fields():
            if field.id == head.name:
                return field.field_at_parts(parts[1:])
        raise ValueError(f"Unknown field path: {path!r}")

    def _record_ai_event(self, role: str, content: str) -> None:
        self._ai_chat_events.append({"role": role, "content": content})

    def _record_patch_approval_result(self, status: str, proposal: PatchProposal) -> None:
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

    def _record_tool_rejection(self, proposal: ToolApprovalProposal) -> None:
        self._ai_conversation_items.append(
            {
                "type": "function_call_output",
                "call_id": proposal.tool_call_id,
                "output": json.dumps(
                    {"status": "rejected", "message": "User rejected the external tool request."},
                    ensure_ascii=False,
                ),
            }
        )
        self._record_ai_event("user", "Rejected proposal.")

    def _focus_assist_input(self, assist_id: str) -> None:
        input_widget = self._assist_chat_inputs.get(assist_id)
        if input_widget is not None and hasattr(input_widget, "focus"):
            input_widget.focus()

    def _render_assist_surface(self, assist_id: str) -> None:
        attention_path = self._assist_attention.get(assist_id)
        if attention_path is None:
            return
        if self._assist_layer_widget is None:
            return

        self._clear_assist_surfaces(except_path=attention_path)
        self._assist_layer_widget.anchor_dom_class = self._anchor_dom_class(attention_path)
        self._assist_layer_widget.placement = self._assist_placement_for_path(attention_path)
        state = self._assist_state.get(assist_id)
        if state == "waiting":
            self._assist_layer_widget.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        attention_path=attention_path,
                        status="AI will suggest after input settles...",
                        input_disabled=False,
                    )
                ),
            )
            return
        if state == "generating":
            self._assist_layer_widget.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        attention_path=attention_path,
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
            self._assist_layer_widget.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        attention_path=attention_path,
                        status=message,
                        status_kind="error",
                        input_disabled=False,
                    )
                ),
            )
            return
        if state == "chat":
            self._assist_layer_widget.children = (
                self._assist_bubble(
                    self._assist_chat_children(
                        assist_id,
                        attention_path=attention_path,
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
        accept_button = widgets.Button(description="Accept", button_style="success")
        reject_button = widgets.Button(description="Reject", button_style="warning")
        accept_button.disabled = proposal.stale
        accept_button.on_click(lambda _button, i=proposal_index: self.accept_proposal(i))
        reject_button.on_click(lambda _button, i=proposal_index: self.reject_proposal(i))
        if isinstance(proposal, PatchProposal):
            proposal_html = (
                f"<strong>AI proposal{stale}</strong>"
                f"<p>{escape(proposal.message)}</p>"
                f"<div class='aipy-assist-operations'>"
                + "<br>".join(
                    f"<code>{escape(operation.path)}</code> = {escape(repr(operation.value))}"
                    for operation in proposal.operations
                )
                + "</div>"
            )
        else:
            proposal_html = (
                f"<strong>External tool proposal{stale}</strong>"
                f"<p>{escape(proposal.message)}</p>"
                f"<div class='aipy-assist-operations'>"
                + "<br>".join(
                    f"<code>{escape(str(key))}</code> = {escape(repr(value))}"
                    for key, value in proposal.preview.items()
                )
                + "</div>"
            )
        self._assist_layer_widget.children = (
            self._assist_bubble(
                self._assist_chat_children(
                    assist_id,
                    attention_path=attention_path,
                    proposal_html=proposal_html,
                    actions=widgets.HBox([accept_button, reject_button]),
                    input_disabled=True,
                ),
                proposal=True,
            ),
        )

    def _clear_assist_surfaces(self, *, except_path: str | None = None) -> None:
        if self._assist_layer_widget is None:
            return
        if except_path is None:
            self._assist_layer_widget.children = ()

    def _assist_placement_for_path(self, path: str) -> str:
        field = self._field_defs[path]
        if field.full_width:
            return "below"
        return "right"

    def _assist_bubble(self, children: list[Any], *, proposal: bool = False):
        bubble = widgets.VBox(children, layout=widgets.Layout(width="320px", overflow="visible"))
        bubble.add_class("aipy-assist-bubble-wrap")
        if proposal:
            bubble.add_class("aipy-assist-proposal-wrap")
        return bubble

    def _assist_chat_children(
        self,
        assist_id: str,
        *,
        attention_path: str,
        status: str | None = None,
        status_kind: str = "info",
        proposal_html: str | None = None,
        actions: Any | None = None,
        input_disabled: bool,
    ) -> list[Any]:
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
.aipy-assist-layer {
  position: relative;
  height: 0 !important;
  overflow: visible !important;
  z-index: 2147483647;
}
.aipy-assist-bubble-wrap {
  position: absolute;
  left: 0;
  top: 0;
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
.aipy-assist-bubble-wrap.aipy-assist-bubble-below::before {
  left: calc(var(--aipy-assist-arrow-left, 32px) - 7px);
  top: -8px;
  border-left: 1px solid #d1d5db;
  border-bottom: 0;
  border-right: 0;
  border-top: 1px solid #d1d5db;
  box-shadow: -3px -3px 6px rgba(15, 23, 42, 0.05);
}
.aipy-assist-bubble-wrap.aipy-assist-bubble-below {
  margin-top: 2px;
  box-shadow: -4px -4px 8px rgba(15, 23, 42, 0.06);
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

    def _proposal_at(self, index: int) -> PatchProposal | ToolApprovalProposal:
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

    @staticmethod
    def _next_dom_token() -> str:
        global _FORM_RENDER_COUNTER
        _FORM_RENDER_COUNTER += 1
        return f"aipy-form-{_FORM_RENDER_COUNTER}"

    def _form_dom_class(self) -> str:
        return self._dom_token

    def _anchor_dom_class(self, path: str) -> str:
        token = "".join(char if char.isalnum() else "-" for char in path)
        return f"{self._dom_token}-anchor-{token}"
