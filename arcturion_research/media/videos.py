#!/usr/bin/env python3
"""
videos: multi-database free video/footage search (keyless-first, ranked).

ONE call fans out across free video databases, dedupes, **ranks by relevance + quality**,
license-tags, and returns watch/landing URL + thumbnail plus a direct file URL where
available.

Sources — KEYLESS (always on):
  peertube   sepiasearch.org        federated PeerTube video (CC-leaning)
  wikimedia  commons.wikimedia.org  freely-licensed video (webm/ogv)
  archive    archive.org            Internet Archive movies (license varies — flagged)

Sources — KEYED (environment variables only; skipped when unset):
  pexels     PEXELS_API_KEY                   free stock footage
  pixabay    PIXABAY_API_KEY                  free stock footage
  serpapi    SERPAPI_API_KEY or SERPAPI_KEY   YouTube search, link/embed only

Sources — OPTIONAL (used only if reachable):
  searxng    SEARXNG_URL (default http://127.0.0.1:8888)  meta-search → YouTube/Vimeo/PeerTube

CLI:
    python3 -m arcturion_research.media.videos "rose pruning" --limit 6
    python3 -m arcturion_research.media.videos "timelapse rose bloom" --all --json
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from ..http import env_key, user_agent

TIMEOUT = 20


def _get(url, headers=None, timeout=TIMEOUT):
    h = {"User-Agent": user_agent(), "Accept": "application/json"}
    if headers:
        h.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _secret(env):
    """API key from the first set environment variable in `env`, else None."""
    return env_key(*env)


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _fmt_secs(s):
    try:
        s = int(float(s))
    except Exception:
        return ""
    return "%d:%02d" % (s // 60, s % 60)


def _mk(title, watch_url, source, **kw):
    return {"title": (title or "").strip()[:200] or "(untitled)", "watch_url": watch_url,
            "thumbnail_url": kw.get("thumbnail_url", "") or "", "file_url": kw.get("file_url", "") or "",
            "source": source, "author": _strip_html(kw.get("author", "")) or "Unknown",
            "license": kw.get("license", "") or "see source", "license_url": kw.get("license_url", "") or "",
            "duration": kw.get("duration", "") or "", "width": kw.get("width"), "height": kw.get("height")}


# ----------------------------------------------------------------------------- sources
def src_peertube(q, n, lic):
    """Federated PeerTube via SepiaSearch — keyless."""
    d = _get("https://sepiasearch.org/api/v1/search/videos?" + urllib.parse.urlencode(
        {"search": q, "count": min(max(n, 1), 20), "sort": "-match"}))
    out = []
    for r in (d.get("data") or [])[:n]:
        ch = r.get("channel") or {}
        acct = r.get("account") or {}
        host = ch.get("host") or acct.get("host") or ""
        uuid = r.get("uuid") or r.get("shortUUID") or ""
        watch = r.get("url") or (("https://%s/w/%s" % (host, uuid)) if host and uuid else "")
        if not watch:
            continue
        tp = r.get("thumbnailPath") or ""
        thumb = ("https://%s%s" % (host, tp)) if tp.startswith("/") and host else (r.get("previewPath") or "")
        lab = (r.get("licence") or {}).get("label") if isinstance(r.get("licence"), dict) else None
        out.append(_mk(r.get("name"), watch, "peertube", thumbnail_url=thumb,
                       author=ch.get("displayName") or acct.get("displayName") or host or "PeerTube",
                       license=lab or "PeerTube (license varies — verify)",
                       duration=_fmt_secs(r.get("duration"))))
    return out


def src_wikimedia(q, n, lic):
    params = {"action": "query", "generator": "search", "gsrsearch": q + " filetype:video",
              "gsrnamespace": "6", "gsrlimit": min(max(n, 1), 20), "prop": "imageinfo",
              "iiprop": "url|extmetadata|size", "iiurlwidth": "640", "format": "json"}
    d = _get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params))
    out = []
    for p in (d.get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            continue
        em = ii.get("extmetadata", {}) or {}
        g = lambda k: (em.get(k, {}) or {}).get("value", "")  # noqa: E731
        out.append(_mk((p.get("title", "") or "").replace("File:", ""),
                       ii.get("descriptionurl", "") or ii.get("url"), "wikimedia",
                       thumbnail_url=ii.get("thumburl", ""), file_url=ii.get("url", ""),
                       author=g("Artist"), license=_strip_html(g("LicenseShortName")),
                       license_url=g("LicenseUrl"), duration=_fmt_secs(g("Duration")),
                       width=ii.get("width"), height=ii.get("height")))
    return out


def src_archive(q, n, lic):
    base = "https://archive.org/advancedsearch.php?"
    qs = urllib.parse.urlencode({"q": "(%s) AND mediatype:movies" % q, "rows": min(max(n, 1), 20),
                                 "output": "json", "page": 1})
    qs += "&fl[]=identifier&fl[]=title&fl[]=creator&fl[]=licenseurl&sort[]=downloads+desc"
    d = _get(base + qs)
    out = []
    for doc in (d.get("response", {}).get("docs", []) or [])[:n]:
        ident = doc.get("identifier")
        if not ident:
            continue
        lic_u = doc.get("licenseurl", "")
        cr = doc.get("creator")
        out.append(_mk(doc.get("title") or ident, "https://archive.org/details/" + ident, "archive",
                       thumbnail_url="https://archive.org/services/img/" + ident,
                       author=(cr if isinstance(cr, str) else (cr or ["Internet Archive"])[0]),
                       license=("CC / open (see page)" if lic_u else "Internet Archive (license varies — verify)"),
                       license_url=lic_u))
    return out


def src_pexels(q, n, lic):
    k = _secret(["PEXELS_API_KEY"])
    if not k:
        return []
    d = _get("https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": q, "per_page": min(max(n, 1), 30)}), headers={"Authorization": k})
    out = []
    for v in d.get("videos", []):
        files = v.get("video_files") or []
        out.append(_mk(((v.get("user", {}) or {}).get("name", "") + " — Pexels video").strip(" —"),
                       v.get("url", ""), "pexels", thumbnail_url=v.get("image", ""),
                       file_url=(sorted(files, key=lambda f: (f.get("width") or 0))[-1].get("link", "") if files else ""),
                       author=(v.get("user", {}) or {}).get("name", "Pexels"),
                       license="Pexels License (free use)", license_url="https://www.pexels.com/license/",
                       duration=_fmt_secs(v.get("duration")), width=v.get("width"), height=v.get("height")))
    return out


def src_pixabay(q, n, lic):
    k = _secret(["PIXABAY_API_KEY"])
    if not k:
        return []
    d = _get("https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
        {"key": k, "q": q, "per_page": min(max(n, 3), 30), "safesearch": "true"}))
    out = []
    for v in d.get("hits", []):
        vid = (v.get("videos", {}) or {}).get("medium", {}) or {}
        out.append(_mk(((v.get("tags") or q) + " — Pixabay video"), v.get("pageURL", ""), "pixabay",
                       thumbnail_url=(vid.get("thumbnail") or (("https://i.vimeocdn.com/video/%s_640x360.jpg" % v.get("picture_id")) if v.get("picture_id") else "")),
                       file_url=vid.get("url", ""), author=v.get("user", "Pixabay"),
                       license="Pixabay Content License (free use)",
                       license_url="https://pixabay.com/service/license-summary/",
                       duration=_fmt_secs(v.get("duration")), width=vid.get("width"), height=vid.get("height")))
    return out


def src_serpapi(q, n, lic):
    """YouTube search via SerpAPI (keyed). Watch/embed only (no download)."""
    k = _secret(["SERPAPI_API_KEY", "SERPAPI_KEY"])
    if not k:
        return []
    d = _get("https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "youtube", "search_query": q, "api_key": k}))
    out = []
    for v in (d.get("video_results") or [])[:n]:
        if not v.get("link"):
            continue
        out.append(_mk(v.get("title"), v["link"], "serpapi:youtube",
                       thumbnail_url=(v.get("thumbnail", {}) or {}).get("static", ""),
                       author=(v.get("channel", {}) or {}).get("name", "YouTube"),
                       license="YouTube (standard licence) — link/embed only",
                       duration=v.get("length", "")))
    return out


def src_searxng(q, n, lic):
    try:
        d = _get((os.environ.get("SEARXNG_URL") or "http://127.0.0.1:8888").rstrip("/") + "/search?"
                 + urllib.parse.urlencode({"q": q, "format": "json", "categories": "videos"}), timeout=10)
    except Exception:
        return []
    out = []
    for r in (d.get("results") or [])[:n]:
        if not r.get("url"):
            continue
        out.append(_mk(r.get("title"), r["url"], "searxng:" + (r.get("engine", "?")),
                       thumbnail_url=r.get("thumbnail") or r.get("thumbnail_src") or "",
                       author=r.get("author", "") or "via SearXNG",
                       license="via SearXNG — verify rights", duration=str(r.get("length", "") or "")))
    return out


SOURCES = {"peertube": src_peertube, "wikimedia": src_wikimedia, "archive": src_archive,
           "pexels": src_pexels, "pixabay": src_pixabay, "serpapi": src_serpapi, "searxng": src_searxng}
DEFAULT_ORDER = ["peertube", "wikimedia", "archive", "pexels", "pixabay"]
EXTENDED = ["serpapi", "searxng"]
KEYLESS = {"peertube", "wikimedia", "archive"}


# ----------------------------------------------------------------------------- ranking
JUNK = {"reaction", "shorts", "asmr", "prank", "compilation", "tiktok", "meme", "unboxing",
        "trailer", "gameplay", "diss", "lyrics"}
_TRUST = ("peertube", "wikimedia")


def _toks(s):
    return set(t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) >= 3)


def _score(r, qtok):
    ttok = _toks(r.get("title"))
    score = 2 * len(qtok & ttok)
    if qtok and qtok <= ttok:
        score += 3
    score -= 3 * len((JUNK - qtok) & ttok)
    lic = (r.get("license") or "").lower()
    if "cc" in lic or "public" in lic or "creative commons" in lic:
        score += 2
    elif "verify" in lic or "varies" in lic or "standard" in lic or lic == "see source":
        score -= 1
    if r.get("duration"):
        score += 1
    if any(r.get("source", "").startswith(t) for t in _TRUST):
        score += 1
    return score


def search(query, limit=8, sources=None, license="any", per_source=None,
           rank=True, include_extended=False):
    srcs = sources if sources else list(DEFAULT_ORDER) + (EXTENDED if include_extended else [])
    per = per_source or max(5, (limit // max(1, len(srcs))) + 3)
    collected, errors = [], {}
    for s in srcs:
        fn = SOURCES.get(s)
        if not fn:
            errors[s] = "unknown source"
            continue
        try:
            collected.extend(fn(query, per, license))
        except urllib.error.HTTPError as e:
            errors[s] = "HTTP %s" % e.code
        except Exception as e:
            errors[s] = type(e).__name__ + ": " + str(e)[:120]
    seen, uniq = set(), []
    for r in collected:
        w = (r.get("watch_url") or "").split("?")[0]
        if not w or w in seen:   # dedupe on watch URL only
            continue
        seen.add(w)
        uniq.append(r)
    if rank:
        qtok = _toks(query)
        for r in uniq:
            r["_score"] = _score(r, qtok)
        uniq.sort(key=lambda r: r["_score"], reverse=True)
    return {"query": query, "count": len(uniq[:limit]), "results": uniq[:limit],
            "errors": errors, "sources": srcs, "ranked": bool(rank)}


# ----------------------------------------------------------------------------- cli
def _print_pretty(res):
    for i, r in enumerate(res["results"], 1):
        sc = (" ·score %d" % r["_score"]) if "_score" in r else ""
        dur = ("(" + r["duration"] + ")") if r["duration"] else ""
        print("[%d] %s %s%s" % (i, r["title"], dur, sc))
        print("     %s · %s · %s" % (r["author"], r["license"], r["source"]))
        print("     watch: %s" % r["watch_url"])
        if r["thumbnail_url"]:
            print("     thumb: %s" % r["thumbnail_url"])
    if res["errors"]:
        print("\n(note) source errors: " + json.dumps(res["errors"]), file=sys.stderr)
    print("\n%d results from %s%s" % (res["count"], ",".join(res["sources"]),
                                      " [ranked]" if res.get("ranked") else " [raw]"), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Multi-database free video/footage search (ranked, keyless-first).")
    ap.add_argument("query", help="search query")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--sources", default="", help="comma list: " + ",".join(SOURCES))
    ap.add_argument("--all", action="store_true", help="include extended: " + ",".join(EXTENDED))
    ap.add_argument("--license", default="any", choices=["any", "cc"])
    ap.add_argument("--per-source", type=int, default=0)
    ap.add_argument("--raw", action="store_true", help="disable ranking")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    srcs = [s.strip() for s in a.sources.split(",") if s.strip()] or None
    res = search(a.query, limit=a.limit, sources=srcs, license=a.license,
                 per_source=(a.per_source or None), rank=not a.raw, include_extended=a.all)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        _print_pretty(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
