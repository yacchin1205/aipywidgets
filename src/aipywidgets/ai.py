from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from threading import Timer
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
        self._timers: dict[str, Timer] = {}

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
        assist = self.get(assist_id)
        timer = Timer(assist.trigger.ms / 1000, self._run_scheduled, args=[assist_id])
        timer.daemon = True
        self._timers[assist_id] = timer
        timer.start()

    def _run_scheduled(self, assist_id: str) -> None:
        try:
            self._form.create_proposal(assist_id)
        except Exception:
            logger.debug("Scheduled AI assist proposal failed after form error handling: %s", assist_id)
        finally:
            self._timers.pop(assist_id, None)


class AIProposalRunner:
    def __init__(self, form: Any) -> None:
        self._form = form

    def run(self, assist: AIAssist) -> PatchProposal:
        config = self._form.ai_config
        if config is None:
            raise RuntimeError("AI assist requires AIConfig")
        client = config.get_client()
        if not config.model:
            raise RuntimeError("AI assist requires AIConfig.model")

        input_snapshot = {path: self._form.get_value(path) for path in assist.watch}
        prompt = self._render_prompt(assist.prompt)
        response = client.responses.create(
            model=config.model,
            input=[
                {
                    "role": "system",
                    "content": self._system_prompt(assist),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "aipywidgets_patch_proposal",
                    "schema": self._proposal_schema(assist),
                }
            },
        )
        proposal = parse_patch_proposal(extract_response_text(response))
        return PatchProposal(
            assist_id=assist.id,
            input_paths=assist.watch,
            input_snapshot=input_snapshot,
            message=proposal.message,
            operations=proposal.operations,
        )

    def _render_prompt(self, prompt: str) -> str:
        values_json = json.dumps(self._form.get_values(), ensure_ascii=False, indent=2)
        rendered = prompt.replace("{{ values }}", values_json)

        def replace_value(match: re.Match[str]) -> str:
            path = match.group(1)
            value = self._form.get_value(path)
            return json.dumps(value, ensure_ascii=False)

        return re.sub(r"\{\{\s*values\.([A-Za-z_][A-Za-z0-9_\.\[\]]*)\s*\}\}", replace_value, rendered)

    def _system_prompt(self, assist: AIAssist) -> str:
        outputs = json.dumps(assist.outputs, ensure_ascii=False, indent=2)
        return (
            "You propose form updates for aipywidgets. "
            "Return only a JSON object matching the supplied schema. "
            "Do not claim that changes have been applied. "
            f"Allowed output paths and their meanings: {outputs}"
        )

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


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text

    if isinstance(response, dict):
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text

    raise TypeError("Responses API result does not expose output_text")


def parse_patch_proposal(text: str) -> PatchProposal:
    data = json.loads(text)
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
