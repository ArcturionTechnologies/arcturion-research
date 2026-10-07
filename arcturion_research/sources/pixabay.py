"""Pixabay photos, illustrations, vectors and video. Key: PIXABAY_API_KEY.
Free tier: 100 requests/60 s. The API rejects per_page below 3."""
from __future__ import annotations

import requests

from ..http import headers, require_key

BASE = "https://pixabay.com/api/"


def _get(path: str, query: str, num: int) -> dict:
    params = {"key": require_key("PIXABAY_API_KEY"), "q": query,
              "per_page": max(3, min(num, 200)), "safesearch": "true"}
    r = requests.get(BASE + path, params=params, headers=headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def search_images(query: str, *, num: int = 10) -> list[dict]:
    data = _get("", query, max(num, 5))
    return [{"source": "pixabay", "title": p.get("tags", "") or query,
             "url": p.get("largeImageURL", "") or p.get("webformatURL", ""),
             "thumbnail": p.get("previewURL", ""),
             "full": p.get("imageURL", "") or p.get("fullHDURL", ""),
             "page": p.get("pageURL", ""),
             "author": p.get("user", ""),
             "type": p.get("type", "image"),
             "license": "Pixabay Content License"} for p in (data.get("hits") or [])[:num]]


def search_videos(query: str, *, num: int = 8) -> list[dict]:
    data = _get("videos/", query, max(num, 5))
    out = []
    for v in (data.get("hits") or [])[:num]:
        vids = v.get("videos") or {}
        best = vids.get("large") or vids.get("medium") or {}
        out.append({"source": "pixabay-video", "title": v.get("tags", "") or query,
                    "url": v.get("pageURL", ""),
                    "video_url": best.get("url", ""),
                    "thumbnail": best.get("thumbnail", ""), "duration": v.get("duration"),
                    "author": v.get("user", ""),
                    "type": "video",
                    "license": "Pixabay Content License"})
    return out
