"""The Met Museum Open Access API (keyless). Public-domain artworks.

Two steps: search returns object IDs, then each ID is resolved to a record.
Many objects have no image, so the search over-fetches.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..http import headers

BASE = "https://collectionapi.metmuseum.org/public/collection/v1"


def _search_ids(query: str, limit: int) -> list[int]:
    r = requests.get(f"{BASE}/search", params={"q": query, "hasImages": "true"},
                     headers=headers(), timeout=20)
    r.raise_for_status()
    return (r.json().get("objectIDs") or [])[:limit]


def _get_object(oid: int) -> dict:
    r = requests.get(f"{BASE}/objects/{oid}", headers=headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def search_images(query: str, *, num: int = 6) -> list[dict]:
    try:
        ids = _search_ids(query, num * 3)
    except Exception:
        return []
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_get_object, oid): oid for oid in ids}
        for fut in as_completed(futures, timeout=30):
            try:
                obj = fut.result(timeout=10)
            except Exception:
                continue
            img = obj.get("primaryImage", "") or obj.get("primaryImageSmall", "")
            if not img:
                continue
            out.append({"source": "met", "title": obj.get("title", ""),
                        "url": img,
                        "thumbnail": obj.get("primaryImageSmall", "") or img,
                        "full": obj.get("primaryImage", ""),
                        "page": obj.get("objectURL", ""),
                        "author": obj.get("artistDisplayName", ""),
                        "snippet": f"{obj.get('objectDate', '')} · {obj.get('medium', '')}"[:200],
                        "license": "Public Domain (Met Open Access)" if obj.get("isPublicDomain") else "The Met (verify rights)",
                        "type": "image"})
            if len(out) >= num:
                break
    return out
