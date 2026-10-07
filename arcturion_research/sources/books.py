"""Books — free, LEGAL book discovery across keyless public sources.

Fans out across Project Gutenberg (Gutendex), Open Library, Standard Ebooks, and
Internet Archive texts. Normalizes every hit to a uniform book dict and dedupes
on lowercased title|author (keeping the entry with the most formats).

HARD LEGAL RULE — bake into this adapter forever:
  Sources are limited to (1) PUBLIC DOMAIN, (2) OPEN ACCESS / CC-licensed, and
  (3) files the user already OWNS. This module NEVER wires in piracy mirrors
  (Library Genesis / LibGen, Z-Library, Anna's Archive, sci-hub). Adding any such
  source is a violation of the skill's legal contract, not a feature. Don't.

All implemented sources are keyless and public. Docs:
  Gutendex        https://gutendex.com
  Open Library    https://openlibrary.org/dev/docs/api/search
  Standard Ebooks https://standardebooks.org  (public HTML; OPDS feeds now auth-walled)
  Internet Archive (texts)  via sibling archive_org.py
"""
from __future__ import annotations
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..http import user_agent
from . import archive_org  # sibling adapter, reused for Internet Archive texts
from . import research_corpora  # keyless research corpus adapters (CourtListener, SEC EDGAR, arXiv, IRS)

UA = {"User-Agent": user_agent()}

# Piracy mirrors that must NEVER be added as a source. Listed here so the intent
# is explicit and grep-able, and so anyone tempted to "just add LibGen" sees the
# line in the code that forbids it.
_FORBIDDEN_SOURCES = (
    "libgen", "library genesis", "z-library", "zlibrary", "annas-archive",
    "anna's archive", "sci-hub", "scihub",
)


# ── per-source adapters ───────────────────────────────────────────────────────
def _gutenberg(query: str, num: int) -> list[dict]:
    """Project Gutenberg via Gutendex. License: Public Domain in the US."""
    try:
        r = requests.get("https://gutendex.com/books",
                         params={"search": query}, headers=UA, timeout=20)
        r.raise_for_status()
        docs = (r.json().get("results") or [])
    except Exception:
        return []
    out = []
    for d in docs[:num]:
        fmts = d.get("formats") or {}
        # Map MIME → friendly fmt; prefer plain text > html > epub for ingestion.
        download = {}
        for mime, url in fmts.items():
            if mime.startswith("text/plain"):
                download.setdefault("txt", url)
            elif mime.startswith("text/html") and ".htm" in url:
                download.setdefault("html", url)
            elif mime == "application/epub+zip":
                download["epub"] = url
            elif mime == "application/x-mobipocket-ebook":
                download["mobi"] = url
        authors = ", ".join(a.get("name", "") for a in (d.get("authors") or [])) or "Unknown"
        out.append({
            "source": "gutenberg",
            "title": d.get("title", ""),
            "author": authors,
            "year": "",  # Gutendex exposes author birth/death, not pub year
            "language": ", ".join(d.get("languages") or []),
            "license": "Public Domain (Project Gutenberg)",
            "url": f"https://www.gutenberg.org/ebooks/{d.get('id')}",
            "cover": fmts.get("image/jpeg", ""),
            "formats": sorted(download.keys()),
            "download_urls": download,
            "excerpt": " ".join(d.get("subjects") or [])[:300],
            "type": "book",
            "gutenberg_id": d.get("id"),
        })
    return out


def _openlibrary(query: str, num: int) -> list[dict]:
    """Open Library search. Filter to publicly readable / public-domain scans;
    link to the Internet Archive item (the actual legal full text)."""
    try:
        r = requests.get(
            "https://openlibrary.org/search.json",
            params={"q": query, "limit": num * 3,
                    "fields": "title,author_name,first_publish_year,language,ia,"
                              "public_scan_b,ebook_access,cover_i,key"},
            headers=UA, timeout=20)
        r.raise_for_status()
        docs = (r.json().get("docs") or [])
    except Exception:
        return []
    out = []
    for d in docs:
        access = d.get("ebook_access")
        # Only surface legally readable copies: public-domain scans or open access.
        if access not in ("public", "open") and not d.get("public_scan_b"):
            continue
        ia_ids = d.get("ia") or []
        ia_id = ia_ids[0] if ia_ids else ""
        download = {}
        if ia_id:
            # Internet Archive serves a plain-text OCR + epub for public scans.
            download["txt"] = f"https://archive.org/download/{ia_id}/{ia_id}_djvu.txt"
            download["epub"] = f"https://archive.org/download/{ia_id}/{ia_id}.epub"
        lic = ("Public Domain (Open Library / IA scan)" if d.get("public_scan_b")
               else "Open Access (Open Library)")
        cover = (f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg"
                 if d.get("cover_i") else "")
        out.append({
            "source": "openlibrary",
            "title": d.get("title", ""),
            "author": ", ".join(d.get("author_name") or []) or "Unknown",
            "year": d.get("first_publish_year", ""),
            "language": ", ".join(d.get("language") or []),
            "license": lic,
            "url": (f"https://archive.org/details/{ia_id}" if ia_id
                    else f"https://openlibrary.org{d.get('key','')}"),
            "cover": cover,
            "formats": sorted(download.keys()),
            "download_urls": download,
            "excerpt": "",
            "type": "book",
        })
        if len(out) >= num:
            break
    return out


def _standard_ebooks(query: str, num: int) -> list[dict]:
    """Standard Ebooks via public HTML search. License: Public Domain (US),
    CC0 for SE's own typesetting. (Their OPDS/Atom feeds are now auth-walled, so
    we use the open /ebooks?query= search page + deterministic download URLs.)"""
    try:
        r = requests.get("https://standardebooks.org/ebooks",
                         params={"query": query}, headers=UA, timeout=25)
        r.raise_for_status()
        html = r.text
    except Exception:
        return []
    out = []
    # Each result: <li typeof="schema:Book" about="/ebooks/<author>/<title>[/<translator>]">
    for m in re.finditer(r'<li typeof="schema:Book" about="(/ebooks/[^"]+)">(.*?)</li>',
                         html, re.S):
        slug, block = m.group(1), m.group(2)
        names = re.findall(r'<span property="schema:name">(.*?)</span>', block, re.S)
        if not names:
            continue
        title = re.sub(r"\s+", " ", names[0]).strip()
        author = re.sub(r"\s+", " ", names[1]).strip() if len(names) > 1 else "Unknown"
        # Build the canonical download filename from the slug path components.
        parts = slug.strip("/").split("/")[1:]  # drop leading 'ebooks'
        stem = "_".join(parts)
        base = f"https://standardebooks.org{slug}/downloads/{stem}"
        # SE serves a "download started" interstitial on the bare URL; the real
        # binary comes from the same URL with ?source=download appended.
        download = {"epub": f"{base}.epub?source=download",
                    "azw3": f"{base}.azw3?source=download"}
        out.append({
            "source": "standard-ebooks",
            "title": title,
            "author": author,
            "year": "",
            "language": "en",
            "license": "Public Domain / CC0 (Standard Ebooks)",
            "url": f"https://standardebooks.org{slug}",
            "cover": "",
            "formats": sorted(download.keys()),
            "download_urls": download,
            "excerpt": "",
            "type": "book",
        })
        if len(out) >= num:
            break
    return out


def _internet_archive(query: str, num: int) -> list[dict]:
    """Internet Archive texts — REUSE the sibling archive_org adapter."""
    try:
        rows = archive_org.search(query, num=num, mediatype="texts")
    except Exception:
        return []
    out = []
    for d in rows:
        url = d.get("url", "")
        ident = url.rstrip("/").split("/")[-1] if url else ""
        download = {}
        if ident:
            download["txt"] = f"https://archive.org/download/{ident}/{ident}_djvu.txt"
            download["epub"] = f"https://archive.org/download/{ident}/{ident}.epub"
            download["pdf"] = f"https://archive.org/download/{ident}/{ident}.pdf"
        out.append({
            "source": "internet-archive",
            "title": d.get("title", ""),
            "author": d.get("creator", "") or "Unknown",
            "year": d.get("date", ""),
            "language": "",
            # IA hosts a mix; flag for verification rather than over-claiming PD.
            "license": "Internet Archive (verify per item — many Public Domain)",
            "url": url,
            "cover": f"https://archive.org/services/img/{ident}" if ident else "",
            "formats": sorted(download.keys()),
            "download_urls": download,
            "excerpt": (d.get("snippet") or "")[:300],
            "type": "book",
        })
    return out


# ── best-effort stubs (NOT faked — return [] until implemented) ────────────────
def _doab(query: str, num: int) -> list[dict]:
    # TODO: DOAB / OAPEN have a public REST API (https://directory.doabooks.org/rest).
    # Keyless but the response shape needs mapping; left as a no-op stub so the
    # fan-out never returns fabricated results. Implement when bandwidth allows.
    return []


def _wikisource(query: str, num: int) -> list[dict]:
    # TODO: Wikisource via the MediaWiki API (search ns=0 on en.wikisource.org).
    # Texts are CC-BY-SA / Public Domain. Stub returns [] — never fake hits.
    return []


# ── public API ────────────────────────────────────────────────────────────────
_SOURCES = {
    "gutenberg": _gutenberg,
    "openlibrary": _openlibrary,
    "standard-ebooks": _standard_ebooks,
    "internet-archive": _internet_archive,
    # best-effort stubs (return [] today):
    "doab": _doab,
    "wikisource": _wikisource,
    # ── research corpora adapters (research_corpora.py) ────────────────────
    # Public Domain legal / government sources + open-access preprints.
    "courtlistener": research_corpora.courtlistener,   # US case-law opinions (Public Domain)
    "sec-edgar":     research_corpora.sec_edgar,        # SEC EDGAR filings (Public Domain)
    "arxiv":         research_corpora.arxiv,            # arXiv papers (author copyright, open access)
    "irs":           research_corpora.irs,              # IRS publications index (Public Domain)
}


def _as_str(v) -> str:
    """Coerce a field that may arrive as a list (some APIs) into a clean string."""
    if isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    return str(v or "").strip()


def _dedupe(items: list[dict]) -> list[dict]:
    """Dedupe on lowercased title|author, keeping the entry with the most formats."""
    best: dict[str, dict] = {}
    for it in items:
        # Normalize possibly-list fields up front so downstream consumers (manifest
        # writers, dedupe key) always see strings.
        it["title"] = _as_str(it.get("title"))
        it["author"] = _as_str(it.get("author"))
        key = f"{it['title'].lower()}|{it['author'].lower()}"
        cur = best.get(key)
        if cur is None or len(it.get("formats") or []) > len(cur.get("formats") or []):
            best[key] = it
    return list(best.values())


def search(query: str, *, num: int = 10, source: str | None = None, **filters) -> list[dict]:
    """Search free, LEGAL book sources. Returns normalized book dicts.

    Args:
        query:  search string (title, author, subject)
        num:    target results per source (overall list is deduped, may be larger)
        source: restrict to one source name (see _SOURCES); None = fan out all live
        filters: reserved for future use (e.g. language=)

    Each result:
        {source, title, author, year, language, license, url, cover,
         formats: [...], download_urls: {fmt: url}, excerpt, type: "book"}
    """
    if source:
        fn = _SOURCES.get(source)
        if fn is None:
            return []
        targets = {source: fn}
    else:
        targets = _SOURCES

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn, query, num): name for name, fn in targets.items()}
        for f in as_completed(futs):
            try:
                results.extend(f.result(timeout=30) or [])
            except Exception:
                continue
    return _dedupe(results)


if __name__ == "__main__":  # quick manual probe
    import json
    q = " ".join(sys.argv[1:]) or "stoicism"
    hits = search(q, num=5)
    print(f"{len(hits)} hits for {q!r}")
    print(json.dumps(hits[:5], indent=2, ensure_ascii=False))
