"""Shared HTTP settings for every source adapter.

User-Agent: some free APIs (OpenStreetMap Nominatim, SEC EDGAR, Wikimedia) ask
callers to identify themselves with a contact. Set ARC_RESEARCH_USER_AGENT to
something like "my-app/1.0 (you@example.org)" before heavy use. The default
names this project and its repository, with no personal contact.

API keys are read from environment variables only. Nothing here talks to a
password manager or a secrets file.
"""
from __future__ import annotations

import os

DEFAULT_USER_AGENT = "ArcturionResearch/0.1 (+https://github.com/ArcturionTechnologies/arcturion-research)"


def user_agent() -> str:
    return os.environ.get("ARC_RESEARCH_USER_AGENT") or DEFAULT_USER_AGENT


def headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": user_agent()}
    if extra:
        h.update(extra)
    return h


def env_key(*names: str) -> str | None:
    """First non-empty environment variable among `names`, else None."""
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


class MissingKey(RuntimeError):
    """Raised by keyed adapters when their API key is not set."""

    def __init__(self, *names: str):
        super().__init__("missing API key: set " + " or ".join(names))
        self.names = names


def require_key(*names: str) -> str:
    val = env_key(*names)
    if not val:
        raise MissingKey(*names)
    return val
