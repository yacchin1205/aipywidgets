from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ai_tools import AIAssistTool


@dataclass
class AIConfig:
    client: Any
    model: str
    tools: list[AIAssistTool] | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            raise RuntimeError(
                "AIConfig.client is required. Create a Responses API compatible client and pass it explicitly."
            )
        if not self.model:
            raise RuntimeError("AIConfig.model is required")
        if self.tools is None:
            self.tools = []

    def get_client(self) -> Any:
        return self.client

    def get_tools(self) -> list[AIAssistTool]:
        return list(self.tools or [])

    def get_tool(self, name: str) -> AIAssistTool:
        for tool in self.get_tools():
            if tool.name == name:
                return tool
        raise ValueError(f"Unknown AI assist tool: {name}")
