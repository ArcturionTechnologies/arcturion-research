"""Tool registry: one uniform callable per source.

Every tool is `fn(query: str, max_results: int) -> list[{title, url, snippet, source}]`.
A tool may raise; the router turns the exception into an error status for that
source and carries on with the others.
"""
from __future__ import annotations

import json
import re
import subprocess

import requests

from .http import MissingKey, headers
from .sources import (agy as _agy, archive_org as _archive_org, arxiv as _arxiv, books as _books,
                      duckduckgo as _ddg, hackernews as _hn, jina_reader as _jina, met as _met,
                      nasa_images as _nasa, news as _news, newsdata as _newsdata, openalex as _openalex,
                      osm_nominatim as _osm, perplexity as _pplx, pexels as _pexels, pixabay as _pixabay,
                      reddit as _reddit, research_corpora as _corpora,
                      semantic_scholar as _s2, serpapi as _serp, tavily as _tavily,
                      unsplash as _unsplash, wikimedia as _wikimedia, wikipedia as _wikipedia,
                      wolfram as _wolfram)


def _norm(items, source: str) -> list[dict]:
    """Normalize varied adapter outputs into one shape."""
    out: list[dict] = []
    if not items:
        return out
    if isinstance(items, dict):
        items = items.get("results") or items.get("items") or []
    for it in items[:30]:
        if not isinstance(it, dict):
            continue
        out.append({
            "title": it.get("title") or it.get("name") or it.get("question") or "",
            "url": it.get("url") or it.get("link") or it.get("href") or it.get("page") or "",
            "snippet": (it.get("snippet") or it.get("summary") or it.get("description")
                        or it.get("content") or it.get("excerpt") or it.get("text") or "")[:600],
            "source": source,
        })
    return out


def _keyed(fn):
    """Turn a missing key into a 'skipped' marker instead of an error."""
    def wrapper(q: str, max_results: int = 5) -> list[dict]:
        try:
            return fn(q, max_results)
        except MissingKey as e:
            return [{"skipped": str(e), "source": fn.__name__}]
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# -- grounded / web ----------------------------------------------------------
def agy(q, max_results=5):        return _norm(_agy.search(q, max_results=max_results), "agy")
def duckduckgo(q, max_results=5): return _norm(_ddg.search(q, num=max_results), "duckduckgo")


@_keyed
def tavily(q, max_results=5):
    return [{**r, "source": "tavily"} for r in _norm(
        [x for x in _tavily.normalize(_tavily.search(q, max_results=max_results, include_images=False))
         if x.get("type") not in ("image", "answer")], "tavily")]


def jina(q, max_results=3):       return _norm(_jina.search(q, num=max_results), "jina")


# -- news --------------------------------------------------------------------
def gdelt(q, max_results=5):      return _norm(_news.search(q, limit=max_results), "gdelt")
@_keyed
def newsdata(q, max_results=5):   return _norm(_newsdata.search(q, num=max_results), "newsdata")
def hackernews(q, max_results=5): return _norm(_hn.search(q, limit=max_results), "hackernews")


# -- academic / books ----------------------------------------------------------
def openalex(q, max_results=5):   return _norm(_openalex.search(q, num=max_results), "openalex")
def arxiv(q, max_results=5):      return _norm(_arxiv.search(q, limit=max_results), "arxiv")
def semantic_scholar(q, max_results=4): return _norm(_s2.search(q, num=max_results), "semantic_scholar")
def wikipedia(q, max_results=2):  return _norm(_wikipedia.search(q, limit=max_results), "wikipedia")
def archive_org(q, max_results=5): return _norm(_archive_org.search(q, num=max_results), "archive_org")
def courtlistener(q, max_results=5): return _norm(_corpora.courtlistener(q, max_results), "courtlistener")
def sec_edgar(q, max_results=5):  return _norm(_corpora.sec_edgar(q, max_results), "sec_edgar")


def legal_books(q: str, max_results: int = 5) -> list[dict]:
    """Public-domain and open-access book discovery (no piracy mirrors, ever)."""
    return [{"title": h.get("title", ""), "url": h.get("url", ""),
             "snippet": f"{h.get('author', '')} · {h.get('license', '')}",
             "source": "legal_books"} for h in _books.search(q, num=max_results)[:max_results]]


# -- factual -------------------------------------------------------------------
@_keyed
def wolfram(q, max_results=1):    return _norm(_wolfram.query(q)[:max_results], "wolfram")
@_keyed
def perplexity(q, max_results=1): return _norm(_pplx.search(q, num=max_results), "perplexity")


# -- social --------------------------------------------------------------------
def reddit(q, max_results=6):     return _norm(_reddit.search(q, limit=max_results), "reddit")


def x_search(q: str, max_results: int = 5) -> list[dict]:
    """X/Twitter search via public Nitter mirrors (no key). Best effort; mirrors
    come and go, so an empty result is normal."""
    mirrors = ["https://nitter.net", "https://nitter.privacydev.net", "https://nitter.poast.org"]
    for base in mirrors:
        try:
            r = requests.get(f"{base}/search", params={"q": q, "f": "tweets"},
                             headers=headers(), timeout=8)
            r.raise_for_status()
            tweets = re.findall(r'href="(/[^/]+/status/\d+[^"]*)"[^>]*>([^<]{10,300})', r.text)
        except Exception:
            continue
        items, seen = [], set()
        for href, text in tweets:
            if href in seen:
                continue
            seen.add(href)
            items.append({"title": text.strip()[:200],
                          "url": "https://x.com" + href.split("#")[0],
                          "snippet": text.strip()[:400], "source": "x_search"})
            if len(items) >= max_results:
                break
        if items:
            return items
    return []


# -- images / places -------------------------------------------------------------
@_keyed
def unsplash(q, max_results=4):   return _norm(_unsplash.search(q, num=max_results), "unsplash")
@_keyed
def pexels(q, max_results=4):     return _norm(_pexels.search_images(q, num=max_results), "pexels")
@_keyed
def pixabay(q, max_results=4):    return _norm(_pixabay.search_images(q, num=max_results), "pixabay")
def nasa_images(q, max_results=3): return _norm(_nasa.search_images(q, num=max_results), "nasa_images")
def wikimedia(q, max_results=3):  return _norm(_wikimedia.search_images(q, num=max_results), "wikimedia")
def met(q, max_results=3):        return _norm(_met.search_images(q, num=max_results), "met")
def osm_nominatim(q, max_results=5): return _norm(_osm.search(q, num=max_results), "osm_nominatim")


# -- SerpAPI engines -------------------------------------------------------------
def _serp_tool(engine: str, kind: str):
    @_keyed
    def fn(q, max_results=5):
        return _serp_call(engine, kind, q, max_results)
    fn.__name__ = f"serpapi_{kind}"
    return fn


def _serp_call(engine: str, kind: str, q: str, max_results: int) -> list[dict]:
    resp = getattr(_serp, engine)(q) if engine in ("youtube", "google_maps") else getattr(_serp, engine)(q, num=max_results)
    return _serp.normalize(resp, kind=kind)[:max_results]


serpapi_google = _serp_tool("google", "google")
serpapi_youtube = _serp_tool("youtube", "youtube")
serpapi_news = _serp_tool("google_news", "news")
serpapi_images = _serp_tool("google_images", "images")
serpapi_scholar = _serp_tool("google_scholar", "scholar")
serpapi_maps = _serp_tool("google_maps", "maps")


# -- video / code ------------------------------------------------------------------
def youtube_yt_dlp(q: str, max_results: int = 5) -> list[dict]:
    """YouTube search through a locally installed yt-dlp (no API quota)."""
    try:
        out = subprocess.check_output(
            ["yt-dlp", f"ytsearch{max_results}:{q}", "--flat-playlist",
             "--print", "%(title)s\t%(webpage_url)s\t%(uploader)s\t%(duration)s\t%(view_count)s"],
            text=True, timeout=30, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    items = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        uploader, duration, views = (parts + ["", "", ""])[2:5]
        items.append({"title": parts[0], "url": parts[1],
                      "snippet": f"{uploader} · {duration}s · {views} views", "source": "youtube_yt_dlp"})
    return items


def github_search(q: str, max_results: int = 5) -> list[dict]:
    """GitHub repository search: the `gh` CLI if installed (uses its login),
    else the public REST API (unauthenticated, 10 requests/minute)."""
    try:
        out = subprocess.check_output(
            ["gh", "search", "repos", q, "--limit", str(max_results),
             "--json", "fullName,description,url,stargazersCount"],
            text=True, timeout=15, stderr=subprocess.DEVNULL)
        rows = json.loads(out)
        return [{"title": r["fullName"], "url": r["url"],
                 "snippet": f"★ {r.get('stargazersCount', 0)} — {(r.get('description') or '')[:300]}",
                 "source": "github_search"} for r in rows]
    except Exception:
        pass
    try:
        r = requests.get("https://api.github.com/search/repositories",
                         params={"q": q, "per_page": max_results},
                         headers=headers({"Accept": "application/vnd.github+json"}), timeout=10)
        r.raise_for_status()
        rows = r.json().get("items", [])
    except Exception:
        return []
    return [{"title": r.get("full_name", ""), "url": r.get("html_url", ""),
             "snippet": f"★ {r.get('stargazers_count', 0)} — {(r.get('description') or '')[:300]}",
             "source": "github_search"} for r in rows]


def stackoverflow(q: str, max_results: int = 4) -> list[dict]:
    try:
        r = requests.get("https://api.stackexchange.com/2.3/search/advanced",
                         params={"order": "desc", "sort": "relevance", "q": q,
                                 "site": "stackoverflow", "pagesize": max_results},
                         headers=headers(), timeout=10)
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception:
        return []
    return [{"title": it.get("title", ""), "url": it.get("link", ""),
             "snippet": f"score={it.get('score', 0)} answers={it.get('answer_count', 0)}",
             "source": "stackoverflow"} for it in items[:max_results]]


# -- registry ----------------------------------------------------------------------
TOOLS = {
    # grounded research / web
    "agy": agy, "tavily": tavily, "jina": jina, "duckduckgo": duckduckgo,
    # news
    "gdelt": gdelt, "newsdata": newsdata, "hackernews": hackernews,
    # academic / books / documents
    "openalex": openalex, "arxiv": arxiv, "semantic_scholar": semantic_scholar,
    "wikipedia": wikipedia, "archive_org": archive_org, "legal_books": legal_books,
    "courtlistener": courtlistener, "sec_edgar": sec_edgar,
    # factual / synthesis
    "wolfram": wolfram, "perplexity": perplexity,
    # social
    "reddit": reddit, "x_search": x_search,
    # images
    "unsplash": unsplash, "pexels": pexels, "pixabay": pixabay,
    "nasa_images": nasa_images, "wikimedia": wikimedia, "met": met,
    # places
    "osm_nominatim": osm_nominatim,
    # SerpAPI engines
    "serpapi_google": serpapi_google, "serpapi_youtube": serpapi_youtube,
    "serpapi_news": serpapi_news, "serpapi_images": serpapi_images,
    "serpapi_scholar": serpapi_scholar, "serpapi_maps": serpapi_maps,
    # video / code
    "youtube_yt_dlp": youtube_yt_dlp, "github_search": github_search,
    "stackoverflow": stackoverflow,
}
