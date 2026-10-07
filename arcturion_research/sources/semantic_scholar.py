"""Semantic Scholar — no key, 5k req/5min shared pool. Free forever for non-commercial.
Docs: https://api.semanticscholar.org/api-docs/
"""
from __future__ import annotations
import time
import requests

from ..http import headers

ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,authors,venue,citationCount,url,externalIds,openAccessPdf"


def search(query: str, *, num: int = 10, year_from: int | None = None) -> list[dict]:
    params = {"query": query, "limit": min(num, 100), "fields": FIELDS}
    if year_from:
        params["year"] = f"{year_from}-"
    # Shared pool 429-prone — retry once w/ backoff
    for attempt in range(2):
        r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=20)
        if r.status_code == 429 and attempt == 0:
            time.sleep(2.5)
            continue
        break
    if r.status_code == 429:
        return []  # graceful — orchestrator marks as skipped
    r.raise_for_status()
    data = r.json()
    out = []
    for item in (data.get("data") or [])[:num]:
        authors = ", ".join(a.get("name", "") for a in (item.get("authors") or [])[:5])
        out.append({"source": "semanticscholar", "title": item.get("title", ""),
                    "url": item.get("url", "") or item.get("openAccessPdf", {}).get("url", ""),
                    "snippet": (item.get("abstract") or "")[:600],
                    "authors": authors, "year": item.get("year"),
                    "venue": item.get("venue", ""), "citations": item.get("citationCount"),
                    "doi": (item.get("externalIds") or {}).get("DOI", ""),
                    "type": "paper"})
    return out
