"""Hacker News stories via the Algolia search API (keyless)."""
from __future__ import annotations

import requests

from ..http import headers

ENDPOINT = "https://hn.algolia.com/api/v1/search"


def search(query: str, *, limit: int = 8) -> list[dict]:
    try:
        r = requests.get(ENDPOINT, params={"query": query, "tags": "story", "hitsPerPage": limit},
                         headers=headers(), timeout=15)
        r.raise_for_status()
        hits = r.json().get("hits") or []
    except Exception:
        return []
    out = []
    for h in hits[:limit]:
        out.append({
            "source": "hackernews",
            "title": h.get("title", ""),
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
            "snippet": (h.get("story_text") or "")[:300],
            "points": h.get("points", 0),
            "comments": h.get("num_comments", 0),
            "published": h.get("created_at", ""),
        })
    return out
