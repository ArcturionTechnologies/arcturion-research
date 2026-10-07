"""SerpAPI engines (Google web, images, news, scholar, maps; YouTube).
Key: environment variable SERPAPI_API_KEY (SERPAPI_KEY also accepted).
Free tier is about 100 searches/month, so the router caps it hard."""
from __future__ import annotations

import requests

from ..http import headers, require_key

ENDPOINT = "https://serpapi.com/search.json"


def _search(engine: str, **params) -> dict:
    params = {"engine": engine, "api_key": require_key("SERPAPI_API_KEY", "SERPAPI_KEY"), **params}
    r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def google(query: str, *, num: int = 8) -> dict:
    return _search("google", q=query, num=num)


def google_images(query: str, *, num: int = 12) -> dict:
    return _search("google_images", q=query, num=num)


def google_maps(query: str, *, ll: str | None = None) -> dict:
    extra = {"ll": ll} if ll else {}
    return _search("google_maps", q=query, type="search", **extra)


def google_scholar(query: str, *, num: int = 10) -> dict:
    return _search("google_scholar", q=query, num=num)


def google_news(query: str, *, num: int = 10) -> dict:
    return _search("google_news", q=query, num=num)


def youtube(query: str, *, num: int = 10) -> dict:
    return _search("youtube", search_query=query)


_RESULT_KEYS = ("organic_results", "video_results", "news_results", "images_results",
                "local_results", "results")


def normalize(resp: dict, *, kind: str = "google") -> list[dict]:
    items: list = []
    for key in _RESULT_KEYS:
        if resp.get(key):
            items = resp[key]
            break
    return [{
        "source": f"serpapi_{kind}",
        "title": (it.get("title") or it.get("name") or "")[:200],
        "url": it.get("link") or it.get("url") or it.get("original") or "",
        "snippet": (it.get("snippet") or it.get("description") or "")[:600],
    } for it in items if isinstance(it, dict)]
