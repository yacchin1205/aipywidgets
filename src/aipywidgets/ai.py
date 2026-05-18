from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .field_path import parse_field_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatchOperation:
    op: str
    path: str
    value: Any

    def __post_init__(self) -> None:
        if self.op != "set":
            raise ValueError(f"Unsupported patch operation: {self.op}")
        parse_field_path(self.path)


@dataclass(frozen=True)
class PatchProposal:
    operations: list[PatchOperation]
    message: str = ""
    assist_id: str | None = None
    input_paths: tuple[str, ...] = ()
    input_snapshot: dict[str, Any] | None = None
    tool_call_id: str | None = None
    stale: bool = False


@dataclass(frozen=True)
class WhenIdle:
    ms: int = 1200
    min_chars: int = 0

    def __post_init__(self) -> None:
        if self.ms <= 0:
            raise ValueError("WhenIdle.ms must be positive")
        if self.min_chars < 0:
            raise ValueError("WhenIdle.min_chars cannot be negative")


@dataclass(frozen=True)
class AIAssist:
    id: str
    label: str
    watch: tuple[str, ...]
    trigger: WhenIdle
    prompt: str
    outputs: dict[str, str]


class AIAssistManager:
    def __init__(self, form: Any) -> None:
        self._form = form
        self._assists: dict[str, AIAssist] = {}
        self._watch_index: dict[str, list[str]] = {}
        self._dirty: set[str] = set()
        self._timers: dict[str, Any] = {}

    def assist(
        self,
        *,
        id: str,
        label: str,
        watch: list[str],
        trigger: WhenIdle,
        prompt: str,
        outputs: dict[str, str],
    ) -> AIAssist:
        if self._form.ai_config is None:
            raise RuntimeError("AI assist requires AIConfig")
        if not id:
            raise ValueError("AI assist id is required")
        if id in self._assists:
            raise ValueError(f"Duplicate AI assist id: {id}")
        if not label:
            raise ValueError("AI assist label is required")
        if not watch:
            raise ValueError("AI assist watch paths are required")
        for watch_path in watch:
            parse_field_path(watch_path)
        if not prompt.strip():
            raise ValueError("AI assist prompt is required")
        if not outputs:
            raise ValueError("AI assist outputs are required")
        for output_path in outputs:
            parse_field_path(output_path)

        assist = AIAssist(
            id=id,
            label=label,
            watch=tuple(watch),
            trigger=trigger,
            prompt=prompt,
            outputs=outputs,
        )
        self._assists[id] = assist
        for watch_path in assist.watch:
            self._watch_index.setdefault(watch_path, []).append(id)
        return assist

    def mark_changed(self, path: str) -> None:
        for assist_id in self._watch_index.get(path, []):
            self._dirty.add(assist_id)
            self._form.mark_assist_dirty(assist_id, path)
            self._schedule(assist_id)

    def ready(self) -> list[AIAssist]:
        return [self._assists[assist_id] for assist_id in sorted(self._dirty)]

    def get(self, assist_id: str) -> AIAssist:
        try:
            return self._assists[assist_id]
        except KeyError as exc:
            raise ValueError(f"Unknown AI assist id: {assist_id}") from exc

    def clear_dirty(self, assist_id: str) -> None:
        self._dirty.discard(assist_id)

    def is_dirty(self, assist_id: str) -> bool:
        self.get(assist_id)
        return assist_id in self._dirty

    def _schedule(self, assist_id: str) -> None:
        existing = self._timers.pop(assist_id, None)
        if existing is not None:
            existing.cancel()
        if not self._form._assist_is_ready(assist_id):
            return
        if self._form._root_widget is None:
            logger.warning("Cannot schedule AI assist before widget() is rendered: %s", assist_id)
            return
        assist = self.get(assist_id)
        loop = asyncio.get_running_loop()
        handle = loop.call_later(assist.trigger.ms / 1000, self._run_scheduled, assist_id)
        self._timers[assist_id] = handle

    def _run_scheduled(self, assist_id: str) -> None:
        try:
            self._form.create_proposal(assist_id)
        except Exception:
            logger.debug("Scheduled AI assist proposal failed after form error handling: %s", assist_id)
        finally:
            self._timers.pop(assist_id, None)


class AIConversationRunner:
    def __init__(self, form: Any) -> None:
        self._form = form

    def run(self, assist: AIAssist) -> PatchProposal:
        prompt = self._initial_user_prompt(assist)
        return self.run_with_message(assist, prompt)

    def run_with_message(self, assist: AIAssist, message: str) -> PatchProposal:
        config = self._form.ai_config
        if config is None:
            raise RuntimeError("AI assist requires AIConfig")
        client = config.get_client()
        if not config.model:
            raise RuntimeError("AI assist requires AIConfig.model")

        input_snapshot = {path: self._form.get_value(path) for path in assist.watch}
        user_item = {"role": "user", "content": message}
        input_items = [*self._form._ai_conversation_items, user_item]
        response = client.responses.create(
            model=config.model,
            instructions=self._instructions(assist),
            input=input_items,
            tools=[self._proposal_tool(assist)],
            tool_choice={"type": "function", "name": "propose_form_update"},
        )
        output_items = response_output_items(response)
        proposal = self._proposal_from_tool_call(assist, output_items)
        self._form._ai_conversation_items.extend([user_item, *output_items])
        self._form._ai_conversation_items.append(
            {
                "type": "function_call_output",
                "call_id": proposal.tool_call_id,
                "output": json.dumps(
                    {
                        "status": "proposal_created",
                        "message": proposal.message,
                        "operations_count": len(proposal.operations),
                    },
                    ensure_ascii=False,
                ),
            }
        )
        self._form._record_ai_event("assistant", proposal.message or "Created a proposal.")
        return PatchProposal(
            assist_id=assist.id,
            input_paths=assist.watch,
            input_snapshot=input_snapshot,
            message=proposal.message,
            operations=proposal.operations,
            tool_call_id=proposal.tool_call_id,
        )

    def _initial_user_prompt(self, assist: AIAssist) -> str:
        watched_values = {path: self._form.get_value(path) for path in assist.watch}
        outputs = json.dumps(assist.outputs, ensure_ascii=False, indent=2)
        values = json.dumps(watched_values, ensure_ascii=False, indent=2)
        return (
            "Create a reviewable form update proposal for this assist.\n\n"
            f"Assist id: {assist.id}\n"
            f"Assist label: {assist.label}\n\n"
            "Assist prompt:\n"
            f"{self._render_prompt(assist.prompt)}\n\n"
            "Watched input values:\n"
            f"{values}\n\n"
            "Allowed output paths:\n"
            f"{outputs}"
        )

    def _render_prompt(self, prompt: str) -> str:
        values_json = json.dumps(self._form.get_values(), ensure_ascii=False, indent=2)
        rendered = prompt.replace("{{ values }}", values_json)

        def replace_value(match: re.Match[str]) -> str:
            path = match.group(1)
            value = self._form.get_value(path)
            return json.dumps(value, ensure_ascii=False)

        return re.sub(r"\{\{\s*values\.([A-Za-z_][A-Za-z0-9_\.\[\]]*)\s*\}\}", replace_value, rendered)

    def _instructions(self, assist: AIAssist) -> str:
        outputs = json.dumps(assist.outputs, ensure_ascii=False, indent=2)
        return (
            "You help complete an aipywidgets form. "
            "Use the propose_form_update tool when proposing form edits. "
            "Do not claim that changes have been applied. "
            "The tool creates a reviewable proposal only; the UI applies accepted proposals. "
            "Respect the allowed output paths and their meanings:\n"
            f"{outputs}"
        )

    def _proposal_tool(self, assist: AIAssist) -> dict[str, Any]:
        return {
            "type": "function",
            "name": "propose_form_update",
            "description": "Create a reviewable proposal for updating form values. Does not apply changes.",
            "parameters": self._proposal_schema(assist),
            "strict": True,
        }

    def _proposal_schema(self, assist: AIAssist) -> dict[str, Any]:
        output_paths = list(assist.outputs)
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "message": {"type": "string"},
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "op": {"type": "string", "enum": ["set"]},
                            "path": {"type": "string", "enum": output_paths},
                            "value": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "integer"},
                                    {"type": "boolean"},
                                    {
                                        "type": "array",
                                        "items": {
                                            "anyOf": [
                                                {"type": "string"},
                                                {"type": "number"},
                                                {"type": "integer"},
                                                {"type": "boolean"},
                                                {"type": "null"},
                                            ]
                                        },
                                    },
                                    {
                                        "type": "object",
                                        "additionalProperties": {
                                            "anyOf": [
                                                {"type": "string"},
                                                {"type": "number"},
                                                {"type": "integer"},
                                                {"type": "boolean"},
                                                {"type": "null"},
                                            ]
                                        },
                                    },
                                    {"type": "null"},
                                ]
                            },
                        },
                        "required": ["op", "path", "value"],
                    },
                },
            },
            "required": ["message", "operations"],
        }

    def _proposal_from_tool_call(self, assist: AIAssist, output_items: list[dict[str, Any]]) -> PatchProposal:
        for item in output_items:
            if item.get("type") != "function_call":
                continue
            if item.get("name") != "propose_form_update":
                raise RuntimeError(f"Unexpected AI tool call: {item.get('name')}")
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                data = json.loads(arguments)
            elif isinstance(arguments, dict):
                data = arguments
            else:
                raise TypeError("propose_form_update arguments must be a JSON object")
            proposal = parse_patch_proposal_data(data)
            for operation in proposal.operations:
                if operation.path not in assist.outputs:
                    raise ValueError(f"AI proposed a path that is not allowed: {operation.path}")
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("propose_form_update call_id is required")
            return PatchProposal(
                message=proposal.message,
                operations=proposal.operations,
                tool_call_id=call_id,
            )
        raise RuntimeError("AI response did not call propose_form_update")


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text

    if isinstance(response, dict):
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text

    raise TypeError("Responses API result does not expose output_text")


def response_output_items(response: Any) -> list[dict[str, Any]]:
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    if not isinstance(output, list):
        raise TypeError("Responses API result does not expose output items")
    return [_response_item_to_dict(item) for item in output]


def _response_item_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        dumped = item.model_dump()
        if isinstance(dumped, dict):
            return dumped
    data = {
        key: getattr(item, key)
        for key in ("type", "name", "arguments", "call_id", "id", "status")
        if hasattr(item, key)
    }
    if not data:
        raise TypeError(f"Unsupported Responses API output item: {item!r}")
    return data


def parse_patch_proposal(text: str) -> PatchProposal:
    return parse_patch_proposal_data(json.loads(text))


def parse_patch_proposal_data(data: Any) -> PatchProposal:
    if not isinstance(data, dict):
        raise TypeError("Patch proposal must be a JSON object")
    operations = data.get("operations")
    if not isinstance(operations, list):
        raise TypeError("Patch proposal operations must be a list")
    message = data.get("message", "")
    if not isinstance(message, str):
        raise TypeError("Patch proposal message must be a string")
    return PatchProposal(
        message=message,
        operations=[
            PatchOperation(
                op=_required_str(operation, "op"),
                path=_required_str(operation, "path"),
                value=_required(operation, "value"),
            )
            for operation in operations
        ],
    )


def _required(data: Any, key: str) -> Any:
    if not isinstance(data, dict):
        raise TypeError("Patch operation must be a JSON object")
    if key not in data:
        raise ValueError(f"Patch operation is missing required key: {key}")
    return data[key]


def _required_str(data: Any, key: str) -> str:
    value = _required(data, key)
    if not isinstance(value, str):
        raise TypeError(f"Patch operation {key!r} must be a string")
    return value
