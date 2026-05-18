from __future__ import annotations

import ipaddress
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .base import AIAssistTool, ToolApprovalRequest

_URL_FETCH_USER_AGENT = "aipywidgets/1.0"
_URL_TEXT_MAX_CHARS = 12000


def build_url_tools() -> list[AIAssistTool]:
    return [_fetch_url_metadata_tool(), _fetch_url_text_tool()]


def _fetch_url_metadata_tool() -> AIAssistTool:
    return AIAssistTool(
        name="fetchUrlMetadata",
        description="Prepares a metadata fetch proposal for a public web page. This does not contact the URL until approved.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "A public http or https URL.",
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        proposal_builder=_build_fetch_url_metadata_proposal,
        executor=_execute_fetch_url_metadata,
    )


def _fetch_url_text_tool() -> AIAssistTool:
    return AIAssistTool(
        name="fetchUrlText",
        description="Prepares a text fetch proposal for a public web page. This does not contact the URL until approved.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "A public http or https URL.",
                },
                "summarize": {
                    "type": "boolean",
                    "description": "Whether the model should summarize the fetched text after approval. Defaults to true.",
                },
                "focus": {
                    "type": ["string", "null"],
                    "description": "Optional focus hint for the later summary.",
                },
            },
            "required": ["url", "summarize", "focus"],
            "additionalProperties": False,
        },
        proposal_builder=_build_fetch_url_text_proposal,
        executor=_execute_fetch_url_text,
    )


def _build_fetch_url_metadata_proposal(arguments: dict[str, Any]) -> ToolApprovalRequest:
    url = _validate_public_url(arguments.get("url"))
    return ToolApprovalRequest(
        message=f"Fetch metadata from {url} after approval.",
        preview={"tool": "fetchUrlMetadata", "url": url},
    )


def _build_fetch_url_text_proposal(arguments: dict[str, Any]) -> ToolApprovalRequest:
    url = _validate_public_url(arguments.get("url"))
    summarize = arguments.get("summarize")
    should_summarize = True if summarize is None else bool(summarize)
    focus = str(arguments.get("focus", "")).strip()
    return ToolApprovalRequest(
        message=f"Fetch page text from {url} after approval.",
        preview={
            "tool": "fetchUrlText",
            "url": url,
            "summarize": should_summarize,
            "focus": focus,
        },
    )


def _execute_fetch_url_metadata(arguments: dict[str, Any]) -> dict[str, Any]:
    url = _validate_public_url(arguments.get("url"))
    html, final_url = _fetch_html(url)
    return {
        "status": "completed",
        "type": "fetched_url_metadata",
        "url": final_url,
        "title": _extract_title(html),
        "description": _extract_meta_content(html, "name", "description"),
        "canonicalUrl": _extract_canonical_url(html),
        "ogTitle": _extract_meta_content(html, "property", "og:title"),
        "ogDescription": _extract_meta_content(html, "property", "og:description"),
        "ogImage": _extract_meta_content(html, "property", "og:image"),
    }


def _execute_fetch_url_text(arguments: dict[str, Any]) -> dict[str, Any]:
    url = _validate_public_url(arguments.get("url"))
    summarize = arguments.get("summarize")
    should_summarize = True if summarize is None else bool(summarize)
    focus = str(arguments.get("focus", "")).strip()
    html, final_url = _fetch_html(url)
    text = _html_to_text(html)
    return {
        "status": "completed",
        "type": "fetched_url_text",
        "url": final_url,
        "title": _extract_title(html),
        "summarize": should_summarize,
        "focus": focus,
        "text": text[:_URL_TEXT_MAX_CHARS],
        "truncated": len(text) > _URL_TEXT_MAX_CHARS,
    }


def _validate_public_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("url is required")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must use http or https")
    if not parsed.netloc:
        raise ValueError("url hostname is required")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("url hostname is required")
    lower = hostname.lower()
    if lower in {"localhost"} or lower.endswith(".local"):
        raise ValueError("url must be public")
    try:
        address = ipaddress.ip_address(lower)
    except ValueError:
        return raw
    if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast:
        raise ValueError("url must be public")
    return raw


def _fetch_html(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": _URL_FETCH_USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")
        return html, response.geturl()


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match is None:
        return ""
    return _compact_ws(match.group(1))


def _extract_meta_content(html: str, attr_name: str, attr_value: str) -> str:
    pattern = (
        r"<meta[^>]*"
        + re.escape(attr_name)
        + r"\s*=\s*[\"']"
        + re.escape(attr_value)
        + r"[\"'][^>]*content\s*=\s*[\"'](.*?)[\"'][^>]*>"
    )
    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if match is None:
        return ""
    return _compact_ws(match.group(1))


def _extract_canonical_url(html: str) -> str:
    match = re.search(
        r"<link[^>]*rel\s*=\s*[\"']canonical[\"'][^>]*href\s*=\s*[\"'](.*?)[\"'][^>]*>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return ""
    return _compact_ws(match.group(1))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in {"p", "br", "div", "li", "section", "article", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        return _compact_ws(" ".join(self._parts).replace("\n ", "\n"))


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _compact_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
