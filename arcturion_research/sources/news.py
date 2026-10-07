"""News via the GDELT 2.0 DOC API (keyless)."""
from __future__ import annotations

import requests

from ..http import headers

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


def search(query: str, *, limit: int = 8) -> list[dict]:
    try:
        r = requests.get(ENDPOINT, params={"query": query, "mode": "ArtList", "format": "json",
                                           "maxrecords": min(limit, 75), "sort": "DateDesc"},
                         headers=headers(), timeout=20)
        r.raise_for_status()
        items = r.json().get("articles") or []
    except Exception:
        return []
    return [{
        "source": "gdelt",
        "title": it.get("title", ""),
        "url": it.get("url", ""),
        "snippet": ((it.get("seendate") or "") + " " + (it.get("domain") or "")).strip(),
        "published": it.get("seendate", ""),
    } for it in items[:limit]]
