"""Antigravity CLI (`agy`) — grounded web research via Google Search grounding.

`agy` uses whatever Google account the local CLI is signed in to. This adapter
passes no API key and stores no secret; any cost or quota is that account's.
If `agy` is not installed or not signed in, search() returns [].

Contract: `search(query, max_results=N) -> list[{title,url,snippet}]` — the shape
`arcturion_research.registry` expects. Fails soft (returns []) on any error so the
router can fall through to Tavily.

Safety posture: `agy` is a full agent, not a search endpoint. We run it from a
neutral cwd with no `--add-dir` and never `--dangerously-skip-permissions`, so
workspace writes have nothing to reach and shell commands stay soft-denied.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BIN = os.environ.get("AGY_BIN") or shutil.which("agy") or "agy"

# Wall-clock ceiling. Grounded multi-search runs observed at 10-25s; the cap is a
# runaway guard, not a target. Subprocess timeout sits above agy's own so agy
# gets the chance to exit cleanly and report status first.
PRINT_TIMEOUT = "3m"
PROC_TIMEOUT = 210  # seconds

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["title", "url", "snippet"],
            },
        }
    },
    "required": ["items"],
}

_PROMPT = (
    "Use your google_search tool to research this query: {q}\n\n"
    "Return the {n} most useful findings as JSON matching the provided schema.\n"
    "Rules:\n"
    "- Every `url` MUST be a real URL you actually retrieved via google_search. "
    "Prefer the specific deep link over a bare homepage.\n"
    "- Never invent, guess, or construct a URL. Omit the item instead.\n"
    "- `snippet` is 1-3 factual sentences grounded in that source, not commentary.\n"
    "- Do not read, write, or modify any local file. Search only.\n"
    "- No preamble, no closing remarks."
)


def _run(prompt: str) -> dict | None:
    """Invoke agy headlessly and return its parsed JSON envelope."""
    cmd = [
        BIN,
        "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(_SCHEMA),
        "--print-timeout", PRINT_TIMEOUT,
    ]
    try:
        # Neutral, throwaway cwd: agy inherits no workspace to touch.
        with tempfile.TemporaryDirectory(prefix="agy-search-") as cwd:
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=PROC_TIMEOUT
            )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


# Google returns grounded citations as opaque Vertex redirect links that expire.
# Anything you keep needs the durable destination, so resolve them.
_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


def _resolve(url: str) -> str:
    """Follow a Vertex grounding redirect to its real destination.

    Returns the original url unchanged if it isn't a redirect or won't resolve —
    a stale citation still beats dropping the finding.
    """
    if _REDIRECT_HOST not in url:
        return url
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.geturl() or url
    except urllib.error.HTTPError as e:
        # The redirect chain completed; the *destination* refused us (403/404 on a
        # bot-hostile site). e.url is that destination, which is what we want —
        # we only need the citation, never the body.
        final = getattr(e, "url", None) or url
        return final if _REDIRECT_HOST not in final else url
    except Exception:
        return url


def _resolve_all(urls: list[str]) -> list[str]:
    if not any(_REDIRECT_HOST in u for u in urls):
        return urls
    try:
        with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
            return list(pool.map(_resolve, urls))
    except Exception:
        return urls


def search(query: str, *, max_results: int = 5) -> list[dict]:
    """Grounded web search. Returns normalized items; [] on any failure."""
    n = max(1, min(int(max_results or 5), 10))
    env = _run(_PROMPT.format(q=query, n=n))
    if not env or env.get("status") != "SUCCESS":
        return []

    items = (env.get("structured_output") or {}).get("items") or []
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = (it.get("url") or "").strip()
        # Drop anything that isn't a real http(s) URL — guards against the model
        # emitting a placeholder rather than omitting the item as instructed.
        if not url.startswith(("http://", "https://")):
            continue
        out.append({
            "source": "agy",
            "title": (it.get("title") or "").strip(),
            "url": url,
            "snippet": (it.get("snippet") or "").strip()[:600],
        })
        if len(out) >= n:
            break

    for item, final in zip(out, _resolve_all([i["url"] for i in out])):
        item["url"] = final
    return out


def available() -> bool:
    """True if the agy binary is present and authenticated."""
    if not shutil.which(BIN) and not BIN.startswith("/"):
        return False
    env = _run("Reply with a JSON object: {\"items\": []}")
    return bool(env and env.get("status") == "SUCCESS")


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "latest Anthropic Claude model releases"
    for r in search(q, max_results=5):
        print(f"- {r['title']}\n  {r['url']}\n  {r['snippet'][:160]}")
