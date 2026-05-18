from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

from .base import AIAssistTool, ToolApprovalRequest

_IDENTIFIER_SOURCES = ("crossref", "jalc", "pubmed", "arxiv")


def build_identifier_tools() -> list[AIAssistTool]:
    return [_resolve_identifier_metadata_tool()]


def _resolve_identifier_metadata_tool() -> AIAssistTool:
    return AIAssistTool(
        name="prepareResolveIdentifierMetadata",
        description="Prepares a metadata lookup proposal for DOI, PMID, or arXiv identifiers. This does not contact external services until approved.",
        parameters={
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "A DOI, PMID, arXiv ID, or arXiv DOI.",
                },
                "reason": {
                    "type": "string",
                    "description": "A short reason for resolving this identifier now.",
                },
                "preferredSources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": list(_IDENTIFIER_SOURCES),
                    },
                    "description": "Optional source order override.",
                },
            },
            "required": ["identifier", "reason"],
            "additionalProperties": False,
        },
        proposal_builder=_build_identifier_proposal,
        executor=_execute_identifier_resolution,
    )


def _build_identifier_proposal(arguments: dict[str, Any]) -> ToolApprovalRequest:
    normalized = _normalize_identifier(arguments.get("identifier"))
    reason = str(arguments.get("reason", "")).strip()
    if not reason:
        raise ValueError("reason is required")
    source_order = _normalize_source_order(arguments.get("preferredSources"), normalized)
    return ToolApprovalRequest(
        message=f"Resolve metadata for {normalized['value']} after approval.",
        preview={
            "tool": "prepareResolveIdentifierMetadata",
            "identifier": normalized["value"],
            "identifierKind": normalized["kind"],
            "preferredSources": source_order,
            "reason": reason,
        },
    )


def _execute_identifier_resolution(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_identifier(arguments.get("identifier"))
    source_order = _normalize_source_order(arguments.get("preferredSources"), normalized)
    attempts: list[dict[str, str]] = []
    for source in source_order:
        try:
            metadata = _fetch_identifier_metadata(normalized, source)
        except Exception as exc:
            attempts.append({"source": source, "status": "error", "message": str(exc)})
            continue
        if metadata is None:
            attempts.append({"source": source, "status": "not_found", "message": ""})
            continue
        attempts.append({"source": source, "status": "found", "message": ""})
        return {
            "status": "completed",
            "type": "resolved_identifier_metadata",
            "identifier": normalized["value"],
            "identifierKind": normalized["kind"],
            "source": source,
            "metadata": metadata,
            "attempts": attempts,
        }
    raise RuntimeError(f"No metadata could be resolved for {normalized['value']}")


def _normalize_identifier(value: Any) -> dict[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("identifier is required")
    lower = raw.lower()
    cleaned = (
        raw.replace("https://doi.org/", "")
        .replace("http://doi.org/", "")
        .replace("doi:", "")
        .replace("pmid:", "")
        .replace("https://arxiv.org/abs/", "")
        .replace("https://arxiv.org/pdf/", "")
        .replace(".pdf", "")
    )
    if re.match(r"^10\.48550/arxiv\.", cleaned, re.IGNORECASE):
        return {
            "kind": "arxiv_doi",
            "value": cleaned,
            "doi": cleaned,
            "arxivId": re.sub(r"^10\.48550/arxiv\.", "", cleaned, flags=re.IGNORECASE),
        }
    if re.match(r"^10\.\d{4,}/[-._;()/:A-Za-z0-9]+$", cleaned):
        return {"kind": "doi", "value": cleaned, "doi": cleaned}
    if re.match(r"^\d+$", cleaned) or lower.startswith("pmid:"):
        pmid = re.sub(r"^pmid:", "", cleaned, flags=re.IGNORECASE)
        return {"kind": "pmid", "value": pmid, "pmid": pmid}
    if re.match(r"^(\d{4}\.\d{4,5}(v\d+)?|[A-Za-z\-]+(\.[A-Za-z]{2})?/\d{7}(v\d+)?)$", cleaned):
        return {
            "kind": "arxiv",
            "value": cleaned,
            "arxivId": cleaned,
            "doi": "10.48550/arXiv." + cleaned,
        }
    raise ValueError(f"Unsupported identifier format: {raw}")


def _normalize_source_order(preferred: Any, normalized: dict[str, str]) -> list[str]:
    candidates = preferred if isinstance(preferred, list) and preferred else _default_source_order(normalized)
    result: list[str] = []
    for item in candidates:
        source = str(item or "").strip().lower()
        if source in _IDENTIFIER_SOURCES and source not in result:
            result.append(source)
    if not result:
        raise ValueError("preferredSources must contain at least one supported source")
    return result


def _default_source_order(normalized: dict[str, str]) -> list[str]:
    kind = normalized["kind"]
    if kind == "pmid":
        return ["pubmed", "crossref", "jalc", "arxiv"]
    if kind in {"arxiv", "arxiv_doi"}:
        return ["arxiv", "crossref", "jalc", "pubmed"]
    return list(_IDENTIFIER_SOURCES)


def _fetch_identifier_metadata(normalized: dict[str, str], source: str) -> dict[str, Any] | None:
    if source == "crossref":
        doi = normalized.get("doi")
        return None if not doi else _fetch_crossref_metadata(doi)
    if source == "jalc":
        doi = normalized.get("doi")
        return None if not doi else _fetch_jalc_metadata(doi)
    if source == "pubmed":
        return _fetch_pubmed_metadata(normalized)
    if source == "arxiv":
        arxiv_id = normalized.get("arxivId")
        return None if not arxiv_id else _fetch_arxiv_metadata(arxiv_id)
    raise ValueError(f"Unknown source: {source}")


def _fetch_crossref_metadata(doi: str) -> dict[str, Any] | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    data = _get_json(url)
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    return {
        "title": _first_str(message.get("title")),
        "doi": message.get("DOI", doi),
        "publisher": message.get("publisher", ""),
        "publishedDate": _crossref_date(message.get("issued")),
        "journal": _first_str(message.get("container-title")),
        "authors": _crossref_authors(message.get("author")),
        "url": message.get("URL", ""),
        "raw": message,
    }


def _fetch_jalc_metadata(doi: str) -> dict[str, Any] | None:
    url = "https://api.japanlinkcenter.org/v2/dois/" + urllib.parse.quote(doi, safe="")
    data = _get_json(url)
    item = data if isinstance(data, dict) else None
    if item is None:
        return None
    title = ""
    titles = item.get("title_list")
    if isinstance(titles, list) and titles:
        first = titles[0]
        if isinstance(first, dict):
            title = str(first.get("title", "")).strip()
    return {
        "title": title,
        "doi": item.get("doi", doi),
        "publisher": str(item.get("publisher_name", "")).strip(),
        "publishedDate": str(item.get("publication_date", "")).strip(),
        "journal": str(item.get("journal_title_name", "")).strip(),
        "authors": _jalc_authors(item.get("creator_list")),
        "url": str(item.get("updated_url", "")).strip(),
        "raw": item,
    }


def _fetch_pubmed_metadata(normalized: dict[str, str]) -> dict[str, Any] | None:
    pmid = normalized.get("pmid")
    if not pmid:
        doi = normalized.get("doi")
        if doi:
            pmid = _search_pubmed_id(doi)
    if not pmid:
        return None
    summary_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        "?db=pubmed&retmode=json&id="
        + urllib.parse.quote(pmid, safe="")
    )
    data = _get_json(summary_url)
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    item = result.get(pmid)
    if not isinstance(item, dict):
        return None
    return {
        "title": str(item.get("title", "")).strip(),
        "doi": _pubmed_article_id(item.get("articleids"), "doi") or normalized.get("doi", ""),
        "pmid": pmid,
        "publisher": str(item.get("fulljournalname", "")).strip(),
        "publishedDate": str(item.get("pubdate", "")).strip(),
        "journal": str(item.get("fulljournalname", "")).strip(),
        "authors": _pubmed_authors(item.get("authors")),
        "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
        "raw": item,
    }


def _fetch_arxiv_metadata(arxiv_id: str) -> dict[str, Any] | None:
    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id, safe="")
    text = _get_text(url)
    title = _xml_text(text, "title")
    if not title:
        return None
    return {
        "title": title,
        "doi": "10.48550/arXiv." + arxiv_id,
        "publisher": "arXiv",
        "publishedDate": _xml_text(text, "published"),
        "journal": "arXiv",
        "authors": re.findall(r"<name>(.*?)</name>", text, re.DOTALL),
        "url": _xml_text(text, "id"),
        "summary": _xml_text(text, "summary"),
        "raw": {"entry": text},
    }


def _search_pubmed_id(query: str) -> str:
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        "?db=pubmed&retmode=json&retmax=1&term="
        + urllib.parse.quote(query, safe="")
    )
    data = _get_json(url)
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not isinstance(ids, list) or not ids:
        return ""
    return str(ids[0]).strip()


def _get_json(url: str) -> dict[str, Any]:
    text = _get_text(url)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError(f"Expected JSON object from {url}")
    return data


def _get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "aipywidgets/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _first_str(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _crossref_date(value: Any) -> str:
    parts = value.get("date-parts") if isinstance(value, dict) else None
    if not isinstance(parts, list) or not parts:
        return ""
    first = parts[0]
    if not isinstance(first, list):
        return ""
    return "-".join(str(part) for part in first if part is not None)


def _crossref_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        given = str(item.get("given", "")).strip()
        family = str(item.get("family", "")).strip()
        name = " ".join(part for part in [given, family] if part).strip()
        if name:
            authors.append(name)
    return authors


def _jalc_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("creator_name", "")).strip()
        if name:
            authors.append(name)
    return authors


def _pubmed_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name:
            authors.append(name)
    return authors


def _pubmed_article_id(value: Any, id_type: str) -> str:
    if not isinstance(value, list):
        return ""
    for item in value:
        if not isinstance(item, dict):
            continue
        if str(item.get("idtype", "")).strip().lower() == id_type:
            return str(item.get("value", "")).strip()
    return ""


def _xml_text(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.DOTALL)
    if match is None:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()
