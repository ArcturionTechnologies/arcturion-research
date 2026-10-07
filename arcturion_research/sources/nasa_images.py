"""NASA Image and Video Library (keyless, public domain)."""
from __future__ import annotations

import requests

from ..http import headers

ENDPOINT = "https://images-api.nasa.gov/search"


def _search(query: str, limit: int, media_type: str) -> list[dict]:
    r = requests.get(ENDPOINT, params={"q": query, "media_type": media_type},
                     headers=headers(), timeout=20)
    r.raise_for_status()
    out = []
    for it in (r.json().get("collection", {}).get("items") or [])[:limit]:
        data = (it.get("data") or [{}])[0]
        links = it.get("links") or []
        out.append({**data, "thumb": links[0].get("href", "") if links else ""})
    return out


def search_images(query: str, *, num: int = 6) -> list[dict]:
    try:
        raw = _search(query, num, "image")
    except Exception:
        return []
    return [{"source": "nasa", "title": n.get("title", ""),
             "url": n.get("thumb", ""),
             "thumbnail": n.get("thumb", ""),
             "page": f"https://images.nasa.gov/details/{n.get('nasa_id', '')}",
             "author": n.get("center", "") or "NASA",
             "snippet": (n.get("description") or "")[:300],
             "date": n.get("date_created", ""),
             "license": "Public Domain (NASA)",
             "type": "image"} for n in raw]


def search_videos(query: str, *, num: int = 5) -> list[dict]:
    try:
        raw = _search(query, num, "video")
    except Exception:
        return []
    return [{"source": "nasa-video", "title": n.get("title", ""),
             "url": f"https://images.nasa.gov/details/{n.get('nasa_id', '')}",
             "thumbnail": n.get("thumb", ""),
             "snippet": (n.get("description") or "")[:300],
             "date": n.get("date_created", ""),
             "license": "Public Domain (NASA)",
             "type": "video"} for n in raw]
