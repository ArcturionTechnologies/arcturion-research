"""DuckDuckGo Instant Answer API — free, no key. Disambiguation + abstracts.
Note: NOT a full web search; best for entities, definitions, computations.
"""
from __future__ import annotations
import requests

from ..http import headers

ENDPOINT = "https://api.duckduckgo.com/"


def search(query: str, *, num: int = 8) -> list[dict]:
    params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 0, "t": "arcturion-research"}
    r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    out: list[dict] = []
    if data.get("AbstractText"):
        out.append({"source": "ddg-abstract", "title": data.get("Heading", ""),
                    "url": data.get("AbstractURL", ""), "snippet": data.get("AbstractText", "")[:600],
                    "publisher": data.get("AbstractSource", "")})
    for topic in (data.get("RelatedTopics") or [])[:num]:
        if "Topics" in topic:
            for t in (topic.get("Topics") or [])[:3]:
                out.append({"source": "ddg-related", "title": t.get("Text", "")[:80],
                            "url": t.get("FirstURL", ""), "snippet": t.get("Text", "")[:500]})
        else:
            out.append({"source": "ddg-related", "title": topic.get("Text", "")[:80],
                        "url": topic.get("FirstURL", ""), "snippet": topic.get("Text", "")[:500]})
    return out[:num]
