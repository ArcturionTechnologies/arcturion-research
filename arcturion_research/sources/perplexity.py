#!/usr/bin/env python3
"""Perplexity Sonar source adapter.

Two modes:
  search(query)        — fast sonar model, returns items like other sources
  deep_research(query) — sonar-deep-research model, returns synthesis + citations

Key: environment variable PERPLEXITY_API_KEY.
Billing: sonar=$1/1M tokens (~$0.001/req), sonar-deep-research=$5/1M tokens
"""
from __future__ import annotations
from ..http import env_key

BASE_URL = "https://api.perplexity.ai"

SONAR_MODEL = "sonar"
SONAR_PRO_MODEL = "sonar-pro"
SONAR_DEEP_MODEL = "sonar-deep-research"


def _get_key() -> str | None:
    return env_key("PERPLEXITY_API_KEY")


def _call(query: str, model: str, max_tokens: int = 1024) -> dict:
    import urllib.request
    import json as _json

    key = _get_key()
    if not key:
        raise RuntimeError("Perplexity API key not found: set PERPLEXITY_API_KEY")

    payload = _json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise research assistant. Answer factually with sources. "
                    "Be concise. Cite inline where possible."
                )
            },
            {"role": "user", "content": query}
        ],
        "max_tokens": max_tokens,
        "search_recency_filter": "month",
        "return_citations": True,
        "return_images": False,
    }).encode()

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return _json.loads(resp.read())


def search(query: str, num: int = 6) -> list[dict]:
    """Fast sonar search — returns normalized items like other source modules."""
    try:
        resp = _call(query, SONAR_MODEL, max_tokens=800)
        text = resp["choices"][0]["message"]["content"]
        citations = resp.get("citations", [])
        items = []
        # Sonar returns a single synthesized answer — surface it as one rich item
        items.append({
            "source": "perplexity_sonar",
            "title": f"Perplexity Sonar: {query[:80]}",
            "snippet": text[:600],
            "text": text,
            "url": citations[0] if citations else "https://www.perplexity.ai",
            "type": "synthesis",
        })
        # Also add individual citations as source items
        for i, url in enumerate(citations[:num - 1]):
            items.append({
                "source": "perplexity_citation",
                "title": f"Source [{i+1}]",
                "snippet": "",
                "url": url,
                "type": "web",
            })
        return items
    except Exception as e:
        return [{"source": "perplexity_sonar", "title": "Perplexity error", "snippet": str(e), "url": "", "type": "error"}]


def deep_research(query: str) -> tuple[str, list[str]]:
    """Full deep-research mode — sonar-deep-research model.

    Returns (synthesis_markdown, list_of_citation_urls).
    Takes 45-120s — uses extended timeout.
    """
    import urllib.request, json as _json
    key = _get_key()
    if not key:
        raise ValueError("No Perplexity API key available")
    payload = _json.dumps({
        "model": SONAR_DEEP_MODEL,
        "messages": [{"role": "user", "content": query}],
        "max_tokens": 4000,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        resp_data = _json.loads(resp.read())
    resp = resp_data
    text = resp["choices"][0]["message"]["content"]
    citations = resp.get("citations", [])
    thinking = resp["choices"][0].get("message", {}).get("reasoning", "")
    model_used = resp.get("model", SONAR_DEEP_MODEL)
    usage = resp.get("usage", {})

    # Annotate with model info
    header = f"> **Source:** Perplexity {model_used} | tokens: {usage.get('total_tokens', '?')}\n\n"
    return header + text, citations


def health() -> bool:
    key = _get_key()
    return bool(key)
