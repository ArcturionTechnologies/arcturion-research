"""Unsplash — 50 req/hr on the free Demo tier.
Signup: https://unsplash.com/developers -> environment variable UNSPLASH_ACCESS_KEY.
"""
from __future__ import annotations
import requests

from ..http import env_key, headers, require_key

ENDPOINT = "https://api.unsplash.com/search/photos"


def _key() -> str:
    return require_key("UNSPLASH_ACCESS_KEY")


def search(query: str, *, num: int = 12, orientation: str | None = None) -> list[dict]:
    headers_ = headers({"Authorization": f"Client-ID {_key()}", "Accept-Version": "v1"})
    params = {"query": query, "per_page": min(num, 30)}
    if orientation:
        params["orientation"] = orientation  # landscape | portrait | squarish
    r = requests.get(ENDPOINT, headers=headers_, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    out = []
    for item in (data.get("results") or [])[:num]:
        urls = item.get("urls", {})
        out.append({"source": "unsplash", "title": item.get("description") or item.get("alt_description", ""),
                    "url": urls.get("regular", ""), "thumbnail": urls.get("thumb", ""),
                    "full": urls.get("full", ""), "page": item.get("links", {}).get("html", ""),
                    "author": (item.get("user") or {}).get("name", ""),
                    "author_url": (item.get("user") or {}).get("links", {}).get("html", ""),
                    "type": "image", "license": "Unsplash License (free use w/ attribution)"})
    return out
