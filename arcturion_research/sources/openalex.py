"""OpenAlex — 250M+ scholarly works.
Optional key: https://openalex.org/settings/api -> environment variable OPENALEX_API_KEY.
"""
from __future__ import annotations
import requests

from ..http import env_key, headers, require_key

ENDPOINT = "https://api.openalex.org/works"


def _key() -> str | None:
    return env_key("OPENALEX_API_KEY")


def search(query: str, *, num: int = 10) -> list[dict]:
    params = {"search": query, "per_page": min(num, 200)}
    k = _key()
    if k:
        params["api_key"] = k
    r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    out = []
    for item in (data.get("results") or [])[:num]:
        authors = ", ".join((a.get("author") or {}).get("display_name", "")
                            for a in (item.get("authorships") or [])[:5])
        oa = item.get("open_access", {}) or {}
        out.append({"source": "openalex", "title": item.get("title", ""),
                    "url": oa.get("oa_url") or item.get("doi", ""), "authors": authors,
                    "year": item.get("publication_year"),
                    "snippet": (item.get("abstract_inverted_index") and "[has abstract]" or "")[:600],
                    "venue": ((item.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                    "citations": item.get("cited_by_count"), "doi": item.get("doi", ""),
                    "type": "paper"})
    return out
