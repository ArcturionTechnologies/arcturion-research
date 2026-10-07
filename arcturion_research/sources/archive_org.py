"""Internet Archive — free, no key. Search 800B+ web pages, films, audio, books.
Docs: https://archive.org/advancedsearch.php
"""
from __future__ import annotations
import requests

from ..http import headers

ENDPOINT = "https://archive.org/advancedsearch.php"


def search(query: str, *, num: int = 10, mediatype: str | None = None) -> list[dict]:
    """mediatype: movies | audio | texts | image | software | etc. None = all."""
    q = query
    if mediatype:
        q = f"{query} AND mediatype:{mediatype}"
    params = {"q": q, "fl[]": "identifier,title,description,creator,date,mediatype,downloads",
              "rows": min(num, 50), "page": 1, "output": "json", "sort[]": "downloads desc"}
    r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    docs = (data.get("response") or {}).get("docs") or []
    out = []
    for d in docs[:num]:
        ident = d.get("identifier", "")
        out.append({"source": "archive-org", "title": d.get("title", ""),
                    "url": f"https://archive.org/details/{ident}",
                    "snippet": (d.get("description") or "")[:600] if isinstance(d.get("description"), str)
                    else " ".join(d.get("description") or [])[:600],
                    "creator": d.get("creator", ""), "date": d.get("date", ""),
                    "mediatype": d.get("mediatype", ""), "downloads": d.get("downloads"),
                    "type": d.get("mediatype", "")})
    return out
