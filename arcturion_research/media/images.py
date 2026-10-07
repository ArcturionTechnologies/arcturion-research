#!/usr/bin/env python3
"""
images: multi-database free image search (keyless-first, ranked).

ONE call fans out across many free image databases, dedupes, **ranks by relevance +
quality**, license-tags, and returns download-ready results (direct image URL + thumbnail
+ attribution).

Sources — KEYLESS (no API key, always on):
  openverse  api.openverse.org      aggregates Flickr, Wikimedia, museums, Smithsonian …
  wikimedia  commons.wikimedia.org  CC / public domain
  artic      api.artic.edu          Art Institute of Chicago — public-domain art (keyless)
  met        metmuseum.org API      Met Open Access — public-domain art
  nasa       images-api.nasa.gov    NASA image library (public domain)
  loc        loc.gov                Library of Congress photos/prints (rights vary — flagged)

Sources — KEYED (environment variables only; skipped when unset):
  pexels      PEXELS_API_KEY
  pixabay     PIXABAY_API_KEY
  unsplash    UNSPLASH_ACCESS_KEY
  serpapi     SERPAPI_API_KEY or SERPAPI_KEY   Google Images, rights vary
  smithsonian SMITHSONIAN_API_KEY or DATA_GOV_API_KEY (falls back to DEMO_KEY)
  europeana   EUROPEANA_API_KEY (falls back to the public api2demo key)
  dpla        DPLA_API_KEY

Sources — OPTIONAL (used only if reachable):
  searxng    SEARXNG_URL (default http://127.0.0.1:8888)  meta-search, rights vary

Default fan-out is the high-yield core; pass --all (or --sources) for the rest.

Library:
    from arcturion_research.media.images import search
    r = search("rosa rugosa flower", limit=12)              # ranked
    r = search("...", sources=["artic","wikimedia"], rank=False)

CLI:
    python3 -m arcturion_research.media.images "rosa rugosa flower" --limit 12
    python3 -m arcturion_research.media.images "1867 La France rose" --all --json
    python3 -m arcturion_research.media.images "knock out rose" --sources artic,met --raw
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


# ----------------------------------------------------------------------------- http / keys
def _get(url, headers=None, timeout=TIMEOUT):
    h = {"User-Agent": user_agent(), "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _secret(env):
    """API key from the first set environment variable in `env`, else None."""
    return env_key(*env)


def _strip_html(s):
    if not s:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _fmt_cc(code, version=""):
    code = (code or "").lower()
    if code == "cc0":
        return "CC0 (public domain)"
    if code == "pdm":
        return "Public Domain Mark"
    if not code:
        return ""
    return ("CC " + code.upper() + (" " + version if version else "")).strip()


def _mk(title, image_url, source, **kw):
    return {
        "title": (title or "").strip()[:200] or "(untitled)",
        "image_url": image_url,
        "thumbnail_url": kw.get("thumbnail_url") or image_url,
        "source": source,
        "source_page": kw.get("source_page", "") or "",
        "author": _strip_html(kw.get("author", "")) or "Unknown",
        "license": kw.get("license", "") or "see source",
        "license_url": kw.get("license_url", "") or "",
        "width": kw.get("width"),
        "height": kw.get("height"),
    }


# ----------------------------------------------------------------------------- keyless sources
def src_openverse(q, n, lic):
    params = {"q": q, "page_size": min(max(n, 1), 50)}
    if lic == "commercial":
        params["license_type"] = "commercial,modification"
    d = _get("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params))
    out = []
    for r in d.get("results", []):
        if not r.get("url"):
            continue
        out.append(_mk(r.get("title"), r.get("url"), "openverse:" + (r.get("source") or "?"),
                       thumbnail_url=r.get("thumbnail"),
                       source_page=r.get("foreign_landing_url") or r.get("url"),
                       author=r.get("creator", ""),
                       license=_fmt_cc(r.get("license", ""), r.get("license_version", "")),
                       license_url=r.get("license_url", ""),
                       width=r.get("width"), height=r.get("height")))
    return out


def src_wikimedia(q, n, lic):
    params = {"action": "query", "generator": "search",
              "gsrsearch": q + " filetype:bitmap", "gsrnamespace": "6",
              "gsrlimit": min(max(n, 1), 20), "prop": "imageinfo",
              "iiprop": "url|extmetadata|size", "iiurlwidth": "800", "format": "json"}
    d = _get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params))
    out = []
    for p in (d.get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            continue
        em = ii.get("extmetadata", {}) or {}
        g = lambda k: (em.get(k, {}) or {}).get("value", "")  # noqa: E731
        out.append(_mk((p.get("title", "") or "").replace("File:", ""), ii.get("url"), "wikimedia",
                       thumbnail_url=ii.get("thumburl") or ii.get("url"),
                       source_page=ii.get("descriptionurl", ""),
                       author=g("Artist"), license=_strip_html(g("LicenseShortName")),
                       license_url=g("LicenseUrl"), width=ii.get("width"), height=ii.get("height")))
    return out


def src_artic(q, n, lic):
    """Art Institute of Chicago — keyless, public-domain art via IIIF."""
    d = _get("https://api.artic.edu/api/v1/artworks/search?" + urllib.parse.urlencode(
        {"q": q, "limit": min(max(n, 1), 20),
         "fields": "id,title,image_id,artist_display,date_display,is_public_domain"}))
    iiif = (d.get("config", {}) or {}).get("iiif_url", "https://www.artic.edu/iiif/2")
    out = []
    for a in d.get("data", []):
        img = a.get("image_id")
        if not img:
            continue
        base = "%s/%s" % (iiif, img)
        out.append(_mk(a.get("title"), base + "/full/843,/0/default.jpg", "artic",
                       thumbnail_url=base + "/full/400,/0/default.jpg",
                       source_page="https://www.artic.edu/artworks/%s" % a.get("id"),
                       author=a.get("artist_display", "") or "Art Institute of Chicago",
                       license=("CC0 (public domain)" if a.get("is_public_domain") else "AIC — rights vary, verify"),
                       license_url="https://www.artic.edu/image-licensing"))
    return out


def src_met(q, n, lic):
    s = _get("https://collectionapi.metmuseum.org/public/collection/v1/search?"
             + urllib.parse.urlencode({"q": q, "hasImages": "true"}))
    out = []
    for oid in (s.get("objectIDs") or [])[: max(n * 3, 9)]:
        if len(out) >= n:
            break
        try:
            o = _get("https://collectionapi.metmuseum.org/public/collection/v1/objects/%d" % oid)
        except Exception:
            continue
        if not o.get("isPublicDomain") or not o.get("primaryImage"):
            continue
        out.append(_mk(o.get("title"), o.get("primaryImage"), "met",
                       thumbnail_url=o.get("primaryImageSmall") or o.get("primaryImage"),
                       source_page=o.get("objectURL", ""),
                       author=o.get("artistDisplayName") or "Unknown",
                       license="Public Domain (CC0)",
                       license_url="https://www.metmuseum.org/about-the-met/policies-and-documents/open-access"))
    return out


def src_nasa(q, n, lic):
    d = _get("https://images-api.nasa.gov/search?" + urllib.parse.urlencode({"q": q, "media_type": "image"}))
    out = []
    for it in (d.get("collection", {}).get("items", []) or [])[:n]:
        data = (it.get("data") or [{}])[0]
        links = it.get("links") or []
        thumb = links[0].get("href") if links else None
        if not thumb:
            continue
        out.append(_mk(data.get("title"), thumb, "nasa", thumbnail_url=thumb,
                       source_page="https://images.nasa.gov/details/" + (data.get("nasa_id") or ""),
                       author=data.get("photographer") or data.get("secondary_creator") or "NASA",
                       license="Public Domain (NASA)",
                       license_url="https://www.nasa.gov/nasa-brand-center/images-and-media/"))
    return out


def src_loc(q, n, lic):
    """Library of Congress photos/prints — keyless. Rights vary (flagged, demoted)."""
    d = _get("https://www.loc.gov/photos/?" + urllib.parse.urlencode({"q": q, "fo": "json", "c": min(max(n, 1), 20)}))
    out = []
    for r in (d.get("results", []) or [])[:n]:
        imgs = r.get("image_url") or []
        if not imgs:
            continue
        url = imgs[-1] if isinstance(imgs, list) else imgs
        if not str(url).startswith("http"):
            continue
        out.append(_mk(r.get("title"), url, "loc", thumbnail_url=(imgs[0] if isinstance(imgs, list) else url),
                       source_page=r.get("id", "") or (r.get("url", "") or ""),
                       author=(", ".join(r.get("contributor", [])) if isinstance(r.get("contributor"), list) else "Library of Congress"),
                       license="Library of Congress (rights vary — verify)",
                       license_url="https://www.loc.gov/legal/"))
    return out


# ----------------------------------------------------------------------------- keyed sources (env vars)
def src_pexels(q, n, lic):
    k = _secret(["PEXELS_API_KEY"])
    if not k:
        return []
    d = _get("https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
        {"query": q, "per_page": min(max(n, 1), 30)}), headers={"Authorization": k})
    out = []
    for p in d.get("photos", []):
        out.append(_mk(p.get("alt") or q, p["src"]["large"], "pexels",
                       thumbnail_url=p["src"]["medium"], source_page=p.get("url", ""),
                       author=p.get("photographer", "Pexels"),
                       license="Pexels License (free use)", license_url="https://www.pexels.com/license/",
                       width=p.get("width"), height=p.get("height")))
    return out


def src_pixabay(q, n, lic):
    k = _secret(["PIXABAY_API_KEY"])
    if not k:
        return []
    d = _get("https://pixabay.com/api/?" + urllib.parse.urlencode(
        {"key": k, "q": q, "per_page": min(max(n, 3), 30), "image_type": "photo", "safesearch": "true"}))
    out = []
    for p in d.get("hits", []):
        out.append(_mk(p.get("tags") or q, p.get("largeImageURL") or p.get("webformatURL"), "pixabay",
                       thumbnail_url=p.get("webformatURL"), source_page=p.get("pageURL", ""),
                       author=p.get("user", "Pixabay"), license="Pixabay Content License (free use)",
                       license_url="https://pixabay.com/service/license-summary/",
                       width=p.get("imageWidth"), height=p.get("imageHeight")))
    return out


def src_unsplash(q, n, lic):
    k = _secret(["UNSPLASH_ACCESS_KEY"])
    if not k:
        return []
    d = _get("https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(
        {"query": q, "per_page": min(max(n, 1), 30)}), headers={"Authorization": "Client-ID " + k})
    out = []
    for p in d.get("results", []):
        out.append(_mk(p.get("description") or p.get("alt_description") or q, p["urls"]["regular"], "unsplash",
                       thumbnail_url=p["urls"]["small"], source_page=(p.get("links", {}) or {}).get("html", ""),
                       author=(p.get("user", {}) or {}).get("name", "Unsplash"),
                       license="Unsplash License (free use)", license_url="https://unsplash.com/license",
                       width=p.get("width"), height=p.get("height")))
    return out


def src_serpapi(q, n, lic):
    """Google Images via SerpAPI (keyed). Broad coverage but rights uncertain → flagged, demoted, opt-in."""
    k = _secret(["SERPAPI_API_KEY", "SERPAPI_KEY"])
    if not k:
        return []
    d = _get("https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "google_images", "q": q, "num": min(max(n, 1), 30), "api_key": k}))
    out = []
    for r in (d.get("images_results") or [])[:n]:
        if not r.get("original"):
            continue
        out.append(_mk(r.get("title"), r["original"], "serpapi:google",
                       thumbnail_url=r.get("thumbnail"), source_page=r.get("link", ""),
                       author=r.get("source", "") or "via Google Images",
                       license="via Google Images — verify rights before reuse",
                       width=r.get("original_width"), height=r.get("original_height")))
    return out


# ----------------------------------------------------------------------------- optional
def _searxng_url():
    return (os.environ.get("SEARXNG_URL") or "http://127.0.0.1:8888").rstrip("/")


def src_searxng(q, n, lic):
    """SearXNG meta-search (SEARXNG_URL) → Google/Bing/Flickr/DeviantArt images. Optional;
    empty if no instance. Rights vary (flagged, demoted)."""
    try:
        d = _get(_searxng_url() + "/search?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "categories": "images"}), timeout=10)
    except Exception:
        return []
    out = []
    for r in (d.get("results") or [])[:n]:
        img = r.get("img_src") or r.get("thumbnail_src")
        if not img:
            continue
        if img.startswith("//"):
            img = "https:" + img
        out.append(_mk(r.get("title"), img, "searxng:" + (r.get("engine", "?")),
                       thumbnail_url=r.get("thumbnail_src") or img,
                       source_page=r.get("url", ""), author=r.get("author", "") or r.get("source", "") or "via SearXNG",
                       license="via SearXNG — verify rights before reuse"))
    return out


# ----------------------------------------------------------------------------- cultural-heritage (env keys; demo keys work without signup)
def _rights_label(url):
    u = (url or "").lower()
    if "publicdomain/zero" in u or "/cc0" in u:
        return "CC0 (public domain)"
    if "publicdomain/mark" in u or "/pdm" in u:
        return "Public Domain Mark"
    if "/by-nc-sa" in u:
        return "CC BY-NC-SA"
    if "/by-nc-nd" in u:
        return "CC BY-NC-ND"
    if "/by-nc" in u:
        return "CC BY-NC"
    if "/by-nd" in u:
        return "CC BY-ND"
    if "/by-sa" in u:
        return "CC BY-SA"
    if "/by/" in u or u.endswith("/by"):
        return "CC BY"
    if "rightsstatements.org" in u:
        return "rights statement — verify"
    return "rights vary — verify"


def src_smithsonian(q, n, lic):
    """Smithsonian Open Access (api.data.gov key; DEMO_KEY works without signup, rate-limited)."""
    k = _secret(["SMITHSONIAN_API_KEY", "DATA_GOV_API_KEY"]) or "DEMO_KEY"
    qq = '%s AND online_media_type:"Images"' % q
    d = _get("https://api.si.edu/openaccess/api/v1.0/search?" + urllib.parse.urlencode(
        {"q": qq, "api_key": k, "rows": min(max(n * 4, 20), 100)}))
    out = []
    for it in (d.get("response", {}).get("rows", []) or []):
        if len(out) >= n:
            break
        dnr = (it.get("content", {}) or {}).get("descriptiveNonRepeating", {}) or {}
        media = (dnr.get("online_media", {}) or {}).get("media", []) or []
        if not media:
            continue
        m0 = media[0]
        img = m0.get("content") or m0.get("thumbnail")
        if not img or not str(img).startswith("http"):
            continue
        access = str((m0.get("usage", {}) or {}).get("access", "")).upper()
        out.append(_mk(it.get("title") or "Smithsonian item", img, "smithsonian",
                       thumbnail_url=m0.get("thumbnail") or img,
                       source_page=dnr.get("record_link", "") or dnr.get("guid", ""),
                       author=dnr.get("data_source", "") or "Smithsonian",
                       license=("CC0 (public domain)" if access == "CC0" else "Smithsonian Open Access — verify rights"),
                       license_url="https://www.si.edu/openaccess"))
    return out


def src_europeana(q, n, lic):
    """Europeana cultural heritage (wskey; api2demo works without signup, rate-limited)."""
    k = _secret(["EUROPEANA_API_KEY"]) or "api2demo"
    d = _get("https://api.europeana.eu/record/v2/search.json?" + urllib.parse.urlencode(
        {"wskey": k, "query": q, "rows": min(max(n * 3, 12), 50), "media": "true",
         "thumbnail": "true", "qf": "TYPE:IMAGE"}))
    out = []
    for it in (d.get("items", []) or [])[:n]:
        img = (it.get("edmIsShownBy") or it.get("edmObject") or it.get("edmPreview") or [None])[0]
        if not img:
            continue
        rights = (it.get("rights") or [""])[0]
        out.append(_mk((it.get("title") or [q])[0], img, "europeana",
                       thumbnail_url=(it.get("edmPreview") or [img])[0],
                       source_page=it.get("guid", ""),
                       author=(it.get("dataProvider") or ["Europeana"])[0],
                       license=_rights_label(rights), license_url=rights))
    return out


def _s1(v):
    """Coerce a DPLA field (str | list | dict | None) to a single string."""
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("name") or v.get("@id") or v.get("label") or ""
    return v if isinstance(v, str) else ("" if v is None else str(v))


def src_dpla(q, n, lic):
    """Digital Public Library of America (free key by email; DPLA_API_KEY)."""
    k = _secret(["DPLA_API_KEY"])
    if not k:
        return []
    d = _get("https://api.dp.la/v2/items?" + urllib.parse.urlencode(
        {"q": q, "api_key": k, "page_size": min(max(n, 1), 20)}))
    out = []
    for doc in (d.get("docs", []) or [])[:n]:
        img = _s1(doc.get("object"))
        if not img or not img.startswith("http"):
            continue
        sr = doc.get("sourceResource", {}) or {}
        title = _s1(sr.get("title")) or "DPLA item"
        rights = _s1(sr.get("rights"))
        dp = _s1(doc.get("dataProvider")) or _s1((doc.get("provider", {}) or {}).get("name")) or "DPLA"
        out.append(_mk(title, img, "dpla", thumbnail_url=img,
                       source_page=_s1(doc.get("isShownAt")), author=dp,
                       license=(_rights_label(rights) if rights.startswith("http") else (rights[:50] or "DPLA — rights vary, verify"))))
    return out


SOURCES = {"openverse": src_openverse, "wikimedia": src_wikimedia, "artic": src_artic,
           "met": src_met, "nasa": src_nasa, "loc": src_loc, "smithsonian": src_smithsonian,
           "europeana": src_europeana, "dpla": src_dpla,
           "pexels": src_pexels, "pixabay": src_pixabay, "unsplash": src_unsplash,
           "serpapi": src_serpapi, "searxng": src_searxng}
DEFAULT_ORDER = ["openverse", "wikimedia", "artic", "met", "pexels", "pixabay", "unsplash"]
EXTENDED = ["smithsonian", "europeana", "dpla", "loc", "nasa", "serpapi", "searxng"]
KEYLESS = {"openverse", "wikimedia", "artic", "met", "nasa", "loc", "smithsonian", "europeana"}


# ----------------------------------------------------------------------------- ranking
JUNK = {"leaf", "leaves", "pest", "damage", "damaged", "sawfly", "caterpillar", "aphid",
        "mite", "dormant", "fashion", "doll", "costume", "map", "chart", "diagram",
        "schematic", "catalogue", "catalog", "logo", "sale", "price", "meme", "watermark",
        "infographic", "poster", "drawing", "clipart"}
_TRUST = ("wikimedia", "met", "nasa", "artic", "openverse:wikimedia")


def _toks(s):
    return set(t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) >= 3)


def _score(r, qtok):
    ttok = _toks(r.get("title"))
    score = 2 * len(qtok & ttok)
    if qtok and qtok <= ttok:
        score += 3                                   # title contains all query words
    score -= 4 * len((JUNK - qtok) & ttok)           # query-aware junk penalty
    lic = (r.get("license") or "").lower()
    if "cc0" in lic or "public domain" in lic or "pdm" in lic:
        score += 3
    elif "verify" in lic or "rights vary" in lic or lic == "see source":
        score -= 1
    elif "nc" in lic or "-nd" in lic or "noncommercial" in lic or "no-deriv" in lic:
        score -= 1
    elif "cc " in lic or "by-sa" in lic or lic.startswith("cc by"):
        score += 2
    area = (r.get("width") or 0) * (r.get("height") or 0)
    if area >= 8_000_000:
        score += 2
    elif area >= 2_000_000:
        score += 1
    elif 0 < area < 200_000:
        score -= 1
    if any(r.get("source", "").startswith(t) for t in _TRUST):
        score += 1
    return score


def search(query, limit=12, sources=None, license="commercial",
           per_source=None, rank=True, include_extended=False):
    """Fan out, dedupe, rank by relevance+quality, return top `limit`.
    sources: explicit list, else DEFAULT_ORDER (+EXTENDED if include_extended).
    rank=False keeps raw source/relevance order."""
    if sources:
        srcs = sources
    else:
        srcs = list(DEFAULT_ORDER) + (EXTENDED if include_extended else [])
    per = per_source or max(6, (limit // max(1, len(srcs))) + 4)  # over-fetch so ranker has material
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
        iu = (r.get("image_url") or "").split("?")[0]
        if not iu or iu in seen:   # dedupe on image URL only — title-dedup wrongly collapsed
            continue                # distinct works sharing a title (e.g. many "Rose" paintings)
        seen.add(iu)
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
        dims = ("%sx%s" % (r["width"], r["height"])) if r.get("width") else ""
        sc = (" ·score %d" % r["_score"]) if "_score" in r else ""
        print("[%d] %s%s" % (i, r["title"], sc))
        print("     %s · %s · %s %s" % (r["author"], r["license"], r["source"], dims))
        print("     img: %s" % r["image_url"])
        if r["source_page"]:
            print("     src: %s" % r["source_page"])
    if res["errors"]:
        print("\n(note) source errors: " + json.dumps(res["errors"]), file=sys.stderr)
    print("\n%d results from %s%s" % (res["count"], ",".join(res["sources"]),
                                      " [ranked]" if res.get("ranked") else " [raw]"), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Multi-database free image search (ranked, keyless-first).")
    ap.add_argument("query", help="search query")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--sources", default="", help="comma list: " + ",".join(SOURCES))
    ap.add_argument("--all", action="store_true", help="include extended sources: " + ",".join(EXTENDED))
    ap.add_argument("--license", default="commercial", choices=["commercial", "cc", "any"])
    ap.add_argument("--per-source", type=int, default=0)
    ap.add_argument("--raw", action="store_true", help="disable relevance ranking")
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
