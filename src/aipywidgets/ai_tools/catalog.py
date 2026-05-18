from __future__ import annotations

from .base import AIAssistTool
from .identifier_tools import build_identifier_tools
from .url_tools import build_url_tools


def default_ai_tools() -> list[AIAssistTool]:
    return [*build_url_tools(), *build_identifier_tools()]
