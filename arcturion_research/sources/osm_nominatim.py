"""OpenStreetMap Nominatim — free Places + geocoding. No key.
Usage policy: 1 req/sec max and an identifying User-Agent (set ARC_RESEARCH_USER_AGENT
with your contact). https://operations.osmfoundation.org/policies/nominatim/
"""
from __future__ import annotations
import time
import requests

from ..http import headers

ENDPOINT = "https://nominatim.openstreetmap.org/search"


def search(query: str, *, num: int = 8, country: str | None = None) -> list[dict]:
    params = {"q": query, "format": "jsonv2", "limit": min(num, 50), "addressdetails": 1,
              "extratags": 1, "namedetails": 1}
    if country:
        params["countrycodes"] = country
    time.sleep(1.0)  # rate-limit policy
    r = requests.get(ENDPOINT, params=params, headers=headers(), timeout=20)
    r.raise_for_status()
    data = r.json() or []
    out = []
    for item in data[:num]:
        addr = item.get("address", {}) or {}
        out.append({"source": "osm-nominatim", "title": item.get("display_name", "").split(",")[0],
                    "address": item.get("display_name", ""), "lat": item.get("lat"),
                    "lon": item.get("lon"), "type": "place",
                    "category": item.get("category", ""), "kind": item.get("type", ""),
                    "url": f"https://www.openstreetmap.org/{item.get('osm_type','')}/{item.get('osm_id','')}",
                    "country": addr.get("country", ""), "city": addr.get("city") or addr.get("town", "")})
    return out


def reverse(lat: float, lon: float) -> dict:
    time.sleep(1.0)
    r = requests.get("https://nominatim.openstreetmap.org/reverse",
                     params={"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1},
                     headers=headers(), timeout=20)
    r.raise_for_status()
    return r.json()
