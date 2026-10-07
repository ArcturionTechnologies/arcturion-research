"""Jina Reader — URL → clean markdown. Free w/o key (rate-limited), 10M tokens w/ key.

Optional key: https://jina.ai/reader/ -> environment variable JINA_API_KEY.
"""
from __future__ import annotations
import requests

from ..http import env_key, headers, require_key

_ua = headers
ENDPOINT = "https://r.jina.ai/"
SEARCH_ENDPOINT = "https://s.jina.ai/"


def _key() -> str | None:
    return env_key("JINA_API_KEY")


def read(url: str, *, with_links: bool = False) -> str:
    """Fetch URL → clean markdown. Returns markdown body."""
    headers = {**_ua(), "Accept": "text/markdown"}
    if with_links:
        headers["X-With-Links-Summary"] = "true"
    k = _key()
    if k:
        headers["Authorization"] = f"Bearer {k}"
    r = requests.get(ENDPOINT + url, headers=headers, timeout=45)
    r.raise_for_status()
    return r.text


def search(query: str, *, num: int = 8) -> list[dict]:
    """Jina Search — query → list of result objects (URL, title, content)."""
    headers = {**_ua(), "Accept": "application/json"}
    k = _key()
    if k:
        headers["Authorization"] = f"Bearer {k}"
    r = requests.get(SEARCH_ENDPOINT + query, headers=headers, timeout=45)
    r.raise_for_status()
    data = r.json()
    out = []
    for item in (data.get("data") or [])[:num]:
        out.append({"source": "jina-search", "title": item.get("title", ""),
                    "url": item.get("url", ""), "snippet": (item.get("content") or "")[:600]})
    return out
