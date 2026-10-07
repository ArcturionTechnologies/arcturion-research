"""Wolfram Alpha — 2000 non-commercial calls/month free.
Signup: https://developer.wolframalpha.com/access -> environment variable WOLFRAM_APP_ID.
"""
from __future__ import annotations
import requests

from ..http import env_key, headers, require_key

FULL_ENDPOINT = "https://api.wolframalpha.com/v2/query"
SHORT_ENDPOINT = "https://api.wolframalpha.com/v1/result"


def _key() -> str:
    return require_key("WOLFRAM_APP_ID")


def short_answer(query: str) -> str:
    """One-line answer — best for factual recall."""
    r = requests.get(SHORT_ENDPOINT, params={"appid": _key(), "i": query}, headers=headers(), timeout=20)
    r.raise_for_status()
    return r.text


def query(question: str) -> list[dict]:
    """Full structured answer — pods + plaintext."""
    r = requests.get(FULL_ENDPOINT, params={"appid": _key(), "input": question, "output": "json",
                                              "format": "plaintext,image"}, headers=headers(), timeout=30)
    r.raise_for_status()
    data = r.json().get("queryresult", {})
    out = []
    for pod in data.get("pods", []) or []:
        text = "\n".join(s.get("plaintext", "") for s in (pod.get("subpods") or []) if s.get("plaintext"))
        if not text:
            continue
        out.append({"source": "wolfram", "title": pod.get("title", ""), "snippet": text[:800],
                    "type": "computation"})
    return out
