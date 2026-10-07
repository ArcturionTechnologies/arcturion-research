"""Wikipedia search plus page summaries (keyless)."""
from __future__ import annotations

import urllib.parse

import requests

from ..http import headers

SEARCH = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def get_summary(title: str) -> dict:
    r = requests.get(SUMMARY + urllib.parse.quote(title.replace(" ", "_")), headers=headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def search(query: str, *, limit: int = 5) -> list[dict]:
    try:
        r = requests.get(SEARCH, params={"action": "query", "list": "search", "srsearch": query,
                                         "srlimit": limit, "format": "json"},
                         headers=headers(), timeout=15)
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
    except Exception:
        return []
    out = []
    for h in hits:
        title = h.get("title", "")
        try:
            summary = get_summary(title)
        except Exception:
            continue
        out.append({
            "source": "wikipedia",
            "title": title,
            "url": (summary.get("content_urls") or {}).get("desktop", {}).get(
                "page", f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"),
            "snippet": (summary.get("extract") or "")[:500],
            "thumbnail": (summary.get("thumbnail") or {}).get("source", ""),
        })
    return out
