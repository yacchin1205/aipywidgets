from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolApprovalRequest:
    message: str
    preview: dict[str, Any]


@dataclass(frozen=True)
class AIAssistTool:
    name: str
    description: str
    parameters: dict[str, Any]
    proposal_builder: Any
    executor: Any

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }

    def build_proposal(self, arguments: dict[str, Any]) -> ToolApprovalRequest:
        return self.proposal_builder(arguments)

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        output = self.executor(arguments)
        if not isinstance(output, dict):
            raise TypeError(f"AI assist tool {self.name!r} must return a JSON object")
        return output
