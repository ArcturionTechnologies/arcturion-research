"""NewsData.io — free tier 200 credits/day (about 2k articles/day).
Signup: https://newsdata.io/register  -> environment variable NEWSDATA_API_KEY.
"""
from __future__ import annotations
import requests

from ..http import env_key, headers, require_key

ENDPOINT = "https://newsdata.io/api/1/latest"


def _key() -> str:
    return require_key("NEWSDATA_API_KEY")


def search(query: str, *, language: str = "en", country: str | None = None,
           category: str | None = None, num: int = 10) -> list[dict]:
    params = {"apikey": _key(), "q": query, "language": language, "size": min(num, 10)}
    if country:
        params["country"] = country
    if category:
        params["category"] = category
    r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    out = []
    for item in (data.get("results") or [])[:num]:
        out.append({"source": "newsdata", "title": item.get("title", ""),
                    "url": item.get("link", ""), "snippet": (item.get("description") or "")[:600],
                    "published": item.get("pubDate", ""), "publisher": item.get("source_id", ""),
                    "image": item.get("image_url", ""), "country": item.get("country", []),
                    "category": item.get("category", [])})
    return out
