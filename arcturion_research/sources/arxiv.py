"""arXiv preprints (keyless). Thin wrapper over research_corpora.arxiv in the
common {title, url, snippet} shape."""
from __future__ import annotations

from . import research_corpora


def search(query: str, *, limit: int = 5) -> list[dict]:
    out = []
    for p in research_corpora.arxiv(query, limit):
        out.append({
            "source": "arxiv",
            "title": p.get("title", ""),
            "url": p.get("url", ""),
            "snippet": (p.get("excerpt") or "")[:500],
            "authors": p.get("author", ""),
            "published": p.get("year", ""),
        })
    return out
