"""Wikimedia Commons image search (keyless). CC0 / CC BY / public-domain media."""
from __future__ import annotations

import re

import requests

from ..http import headers

ENDPOINT = "https://commons.wikimedia.org/w/api.php"


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def search_images(query: str, *, num: int = 8) -> list[dict]:
    params = {"action": "query", "generator": "search", "gsrsearch": query + " filetype:bitmap",
              "gsrnamespace": "6", "gsrlimit": min(max(num, 1), 50), "prop": "imageinfo",
              "iiprop": "url|extmetadata", "iiurlwidth": "800", "format": "json"}
    try:
        r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=20)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
    except Exception:
        return []
    out = []
    for page in list(pages.values())[:num]:
        ii = (page.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            continue
        meta = ii.get("extmetadata") or {}
        val = lambda k: (meta.get(k) or {}).get("value", "")  # noqa: E731
        out.append({"source": "wikimedia", "title": (page.get("title") or "").replace("File:", ""),
                    "url": ii.get("url", ""),
                    "thumbnail": ii.get("thumburl", ""),
                    "page": ii.get("descriptionurl", "") or f"https://commons.wikimedia.org/?curid={page.get('pageid', '')}",
                    "author": _strip(val("Artist")),
                    "license": _strip(val("LicenseShortName")) or "Wikimedia Commons (varies)",
                    "snippet": _strip(val("ImageDescription"))[:300],
                    "type": "image"})
    return out
