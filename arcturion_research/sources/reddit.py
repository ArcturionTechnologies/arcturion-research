"""Reddit post search via the public JSON endpoint (keyless, rate-limited).
Reddit expects a descriptive User-Agent; see ARC_RESEARCH_USER_AGENT."""
from __future__ import annotations

import requests

from ..http import headers

ENDPOINT = "https://www.reddit.com/search.json"


def search(query: str, *, limit: int = 5) -> list[dict]:
    try:
        r = requests.get(ENDPOINT, params={"q": query, "limit": limit, "sort": "relevance"},
                         headers=headers(), timeout=15)
        r.raise_for_status()
        children = (r.json().get("data") or {}).get("children") or []
    except Exception:
        return []
    out = []
    for child in children[:limit]:
        it = child.get("data") or {}
        out.append({
            "source": "reddit",
            "title": it.get("title", ""),
            "url": "https://www.reddit.com" + it["permalink"] if it.get("permalink") else it.get("url", ""),
            "snippet": (it.get("selftext") or "")[:400],
            "subreddit": it.get("subreddit", ""),
            "score": it.get("score", 0),
            "published": str(it.get("created_utc", "")),
        })
    return out
