"""Pexels photos and videos. Key: environment variable PEXELS_API_KEY.
Free tier: 200 requests/hour."""
from __future__ import annotations

import requests

from ..http import headers, require_key

BASE = "https://api.pexels.com"


def _get(path: str, params: dict) -> dict:
    r = requests.get(BASE + path, params=params,
                     headers=headers({"Authorization": require_key("PEXELS_API_KEY")}), timeout=20)
    r.raise_for_status()
    return r.json()


def search_images(query: str, *, num: int = 10) -> list[dict]:
    data = _get("/v1/search", {"query": query, "per_page": min(num, 80)})
    out = []
    for p in (data.get("photos") or [])[:num]:
        src = p.get("src") or {}
        out.append({"source": "pexels", "title": p.get("alt", "") or query,
                    "url": src.get("large", "") or src.get("original", ""),
                    "thumbnail": src.get("medium", ""),
                    "full": src.get("original", ""),
                    "page": p.get("url", ""),
                    "author": p.get("photographer", ""),
                    "author_url": p.get("photographer_url", ""),
                    "type": "image",
                    "license": "Pexels License (free commercial use, attribution appreciated)"})
    return out


def search_videos(query: str, *, num: int = 8) -> list[dict]:
    data = _get("/videos/search", {"query": query, "per_page": min(num, 80)})
    out = []
    for v in (data.get("videos") or [])[:num]:
        files = sorted(v.get("video_files") or [], key=lambda f: f.get("width") or 0)
        out.append({"source": "pexels-video", "title": (v.get("user") or {}).get("name", "") or query,
                    "url": v.get("url", ""),
                    "video_url": files[-1].get("link", "") if files else "",
                    "thumbnail": v.get("image", ""),
                    "duration": v.get("duration"),
                    "author": (v.get("user") or {}).get("name", ""),
                    "width": v.get("width"), "height": v.get("height"),
                    "type": "video",
                    "license": "Pexels License (free commercial use)"})
    return out
