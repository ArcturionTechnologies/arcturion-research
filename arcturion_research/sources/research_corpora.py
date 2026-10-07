"""research_corpora — four keyless source adapters for legal research documents.

Adapters registered in books.py _SOURCES:
  courtlistener  US case-law opinions (CourtListener / Free Law Project)
  sec-edgar      SEC EDGAR public filings (10-K, 10-Q, 8-K, etc.)
  arxiv          arXiv preprints (author copyright, open access)
  irs            IRS publications curated keyword index (no search API exists)

All four are legitimate, keyless public sources.  See books.py for the
legal guardrail (_FORBIDDEN_SOURCES); it is not duplicated here.

===========================================================================
KNOWN GAP — IRS Tax Court opinions (Dawson API):
  The US Tax Court public REST API at
      https://public-api-green.dawson.ustaxcourt.gov
  currently returns "this api is disabled" (as of 2026-06-29).
  Dawson is on the roadmap to re-enable it; revisit when the API comes back.
  Today's `irs` adapter covers IRS *publications* (Pub 17, 334, 463, …)
  only.  Tax Court case law is a known gap.
===========================================================================
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

from ..http import user_agent

UA = {"User-Agent": user_agent()}
_TIMEOUT = 25


# ─────────────────────────────────────────────────────────────────────────────
# 1. CourtListener — US case-law (Public Domain)
# ─────────────────────────────────────────────────────────────────────────────
def courtlistener(query: str, num: int) -> list[dict]:
    """CourtListener (Free Law Project) — keyless REST v4 search.

    Public Domain (U.S. court opinions are not copyrightable per 17 USC §105).
    Search docs: https://www.courtlistener.com/api/rest/v4/
    """
    try:
        r = requests.get(
            "https://www.courtlistener.com/api/rest/v4/search/",
            params={"q": query, "type": "o", "order_by": "score desc", "page_size": num},
            headers=UA,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception:
        return []

    out: list[dict] = []
    for res in results[:num]:
        case_name = res.get("caseName") or res.get("case_name") or "Unknown"
        date_filed = res.get("dateFiled") or res.get("date_filed") or ""
        court = res.get("court") or res.get("court_id") or ""
        abs_url = res.get("absolute_url") or ""
        cl_url = f"https://www.courtlistener.com{abs_url}" if abs_url else ""

        # Resolve a direct downloadable file URL.
        # Strategy: look for opinions[] in the result (cluster-level search returns them);
        # then attempt to pull the opinion detail for a PDF or plain_text filepath.
        download_urls: dict[str, str] = {}
        opinions = res.get("opinions") or []
        for op in opinions[:2]:  # try up to 2 opinions per cluster
            dl_url = _resolve_cl_opinion(op)
            if dl_url:
                # Distinguish by format from URL extension
                fmt = "pdf" if dl_url.lower().endswith(".pdf") else "txt"
                download_urls.setdefault(fmt, dl_url)
                break

        # If no opinion resolved, fall back to the HTML page (still useful to an
        # HTML extractor even if it's not a raw file).
        if not download_urls and cl_url:
            download_urls["html"] = cl_url

        out.append({
            "source": "courtlistener",
            "title": case_name,
            "author": court,
            "year": date_filed[:4] if date_filed else "",
            "language": "en",
            "license": "Public Domain (U.S. court opinion · CourtListener)",
            "url": cl_url,
            "cover": "",
            "formats": sorted(download_urls.keys()),
            "download_urls": download_urls,
            "excerpt": (res.get("snippet") or "")[:300],
            "type": "opinion",
        })
    return out


def _resolve_cl_opinion(op: dict[str, Any]) -> str:
    """Return the best direct download URL for a CourtListener opinion dict.

    The search result's opinions[] array already contains these fields:
      local_path  — relative path on CourtListener's storage CDN (preferred)
      download_url — original court PDF URL (good fallback)

    Priority: local_path → download_url → empty string.
    The detail/cluster endpoints require authentication, so we do NOT call them.
    """
    # Case 1: local_path on CourtListener storage CDN.
    # local_path already contains the relative sub-path (e.g. "pdf/2016/…/opinion.pdf")
    local_path = op.get("local_path") or ""
    if local_path:
        return f"https://storage.courtlistener.com/{local_path.lstrip('/')}"

    # Case 2: download_url provided in the search result.
    dl = op.get("download_url") or ""
    if dl:
        return dl

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. SEC EDGAR — public filings (Public Domain)
# ─────────────────────────────────────────────────────────────────────────────
def sec_edgar(query: str, num: int) -> list[dict]:
    """SEC EDGAR full-text search — keyless; requires an explicit User-Agent.

    License: Public Domain (U.S. Government / SEC EDGAR).
    SEC blocks undeclared tools, so always send the UA header (set
    ARC_RESEARCH_USER_AGENT to "<name> <contact email>" per SEC guidance).
    EFTS docs: https://efts.sec.gov/LATEST/search-index?q=<query>

    _id format in results: "{accession_with_dashes}:{filename}"
    _source fields: ciks (list), adsh (accession), display_names, file_type/form,
                    file_date, file_description
    Primary document URL:
      https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{accession_nodashes}/{filename}
    """
    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": query},
            headers={**UA, "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        hits = ((r.json().get("hits") or {}).get("hits") or [])
    except Exception:
        return []

    out: list[dict] = []
    for h in hits[:num]:
        src = h.get("_source") or {}
        # _id is "{accession-with-dashes}:{filename}"
        raw_id = h.get("_id") or ""
        if ":" in raw_id:
            accession_dashes, filename = raw_id.split(":", 1)
        else:
            accession_dashes = src.get("adsh") or ""
            filename = ""

        # CIK — comes as a list; strip leading zeros for the URL path.
        ciks = src.get("ciks") or []
        cik = str(ciks[0]).lstrip("0") if ciks else ""

        # Build the primary document URL.
        # Format: /Archives/edgar/data/{cik}/{accession_nodashes}/{filename}
        accession_nodashes = re.sub(r"[^0-9a-zA-Z]", "", accession_dashes)
        download_urls: dict[str, str] = {}
        primary_url = ""
        if cik and accession_nodashes and filename:
            primary_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik}/{accession_nodashes}/{filename}"
            )
            fmt = Path_ext(filename)
            download_urls[fmt] = primary_url

        # Filing index page (always constructable from accession).
        if accession_dashes:
            filing_url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik}&type={src.get('file_type','')}"
                f"&dateb=&owner=include&count=40"
                if cik
                else primary_url or ""
            )
        else:
            filing_url = primary_url or ""

        display_names = src.get("display_names") or []
        company = ", ".join(display_names) if display_names else (f"CIK {cik}" if cik else "Unknown")

        file_type = (src.get("file_type") or src.get("form") or "filing")
        file_date = src.get("file_date") or ""

        out.append({
            "source": "sec-edgar",
            "title": f"{company} — {file_type}",
            "author": company,
            "year": file_date[:4] if file_date else "",
            "language": "en",
            "license": "Public Domain (U.S. SEC EDGAR)",
            "url": filing_url or primary_url,
            "cover": "",
            "formats": sorted(download_urls.keys()),
            "download_urls": download_urls,
            "excerpt": (src.get("file_description") or file_type)[:300],
            "type": "filing",
        })
    return out


def Path_ext(filename: str) -> str:
    """Return a format hint from a filename extension."""
    low = (filename or "").lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".htm") or low.endswith(".html"):
        return "html"
    if low.endswith(".txt"):
        return "txt"
    return "html"  # SEC primary docs are almost always .htm


# ─────────────────────────────────────────────────────────────────────────────
# 3. arXiv — open-access preprints (AUTHOR COPYRIGHT, free to read)
# ─────────────────────────────────────────────────────────────────────────────
_ARXIV_NS = "http://www.w3.org/2005/Atom"


def arxiv(query: str, num: int) -> list[dict]:
    """arXiv preprints via the public Atom API — keyless.

    LICENSE NOTICE: arXiv papers are NOT public domain.  They are author-
    copyrighted works made freely accessible for reading and research under
    arXiv's submission license.  They are NOT free for redistribution or
    commercial use without the author's explicit permission.  This adapter
    surfaces them for research reading; do not label them public domain.

    Docs: https://info.arxiv.org/help/api/index.html
    The sibling arxiv.py adapter delegates here too.
    """
    try:
        r = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "max_results": num,
                    "sortBy": "relevance"},
            headers=UA,
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []

    out: list[dict] = []
    for entry in root.findall(f"{{{_ARXIV_NS}}}entry"):
        raw_id = _ax(entry, "id") or ""
        # arxiv id looks like http://arxiv.org/abs/2106.09685v1
        m = re.search(r"arxiv\.org/abs/([^/\s]+)", raw_id, re.I)
        arxiv_id = m.group(1) if m else raw_id.rstrip("/").split("/")[-1]
        # strip version suffix for the canonical PDF URL
        arxiv_id_base = re.sub(r"v\d+$", "", arxiv_id)

        title = re.sub(r"\s+", " ", _ax(entry, "title") or "").strip()
        summary = re.sub(r"\s+", " ", _ax(entry, "summary") or "").strip()
        published = (_ax(entry, "published") or "")[:10]

        authors = [
            _ax(a, "name") or ""
            for a in entry.findall(f"{{{_ARXIV_NS}}}author")
        ]

        pdf_url = f"https://arxiv.org/pdf/{arxiv_id_base}.pdf"
        abs_url = f"https://arxiv.org/abs/{arxiv_id_base}"
        download_urls = {"pdf": pdf_url}

        out.append({
            "source": "arxiv",
            "title": title,
            "author": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            "year": published[:4] if published else "",
            "language": "en",
            "license": (
                "arXiv — author copyright, open access "
                "(free to read/research; NOT public domain)"
            ),
            "url": abs_url,
            "cover": "",
            "formats": ["pdf"],
            "download_urls": download_urls,
            "excerpt": summary[:300],
            "type": "paper",
        })
    return out


def _ax(el: ET.Element, tag: str) -> str:
    """Get text of a child element in the Atom namespace."""
    child = el.find(f"{{{_ARXIV_NS}}}{tag}")
    return (child.text or "").strip() if child is not None else ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. IRS — federal tax publications (Public Domain)
# ─────────────────────────────────────────────────────────────────────────────
# Curated index of major IRS publications.
# IRS has no public search API; Tax Court Dawson API is currently DISABLED
# (public-api-green.dawson.ustaxcourt.gov → "this api is disabled" as of 2026-06-29).
# This index covers the most commonly referenced publications; expand as needed.
_IRS_PUBS: list[dict] = [
    {"num": 17,    "title": "Your Federal Income Tax (For Individuals)",
     "keywords": ["income tax", "individual", "federal tax", "personal tax", "tax return",
                  "deductions", "credits", "filing"]},
    {"num": 334,   "title": "Tax Guide for Small Business",
     "keywords": ["small business", "self-employed", "sole proprietor", "schedule c",
                  "business income"]},
    {"num": 463,   "title": "Travel, Gift, and Car Expenses",
     "keywords": ["travel", "gift", "car", "vehicle", "mileage", "entertainment",
                  "business expense", "reimbursement"]},
    {"num": 501,   "title": "Dependents, Standard Deduction, and Filing Information",
     "keywords": ["dependent", "standard deduction", "filing status", "exemption",
                  "head of household"]},
    {"num": 503,   "title": "Child and Dependent Care Expenses",
     "keywords": ["child care", "dependent care", "daycare", "childcare credit"]},
    {"num": 525,   "title": "Taxable and Nontaxable Income",
     "keywords": ["taxable income", "nontaxable", "income types", "wages", "fringe benefits",
                  "alimony", "gambling", "prizes"]},
    {"num": 526,   "title": "Charitable Contributions",
     "keywords": ["charity", "charitable", "donation", "contributions", "nonprofit",
                  "501c3"]},
    {"num": 535,   "title": "Business Expenses",
     "keywords": ["business expenses", "deductible", "operating costs", "startup",
                  "home office", "interest expense"]},
    {"num": 544,   "title": "Sales and Other Dispositions of Assets",
     "keywords": ["capital gains", "asset sale", "disposition", "securities",
                  "real property sale"]},
    {"num": 550,   "title": "Investment Income and Expenses",
     "keywords": ["investment", "dividends", "interest income", "stocks", "bonds",
                  "mutual funds", "capital gains"]},
    {"num": 560,   "title": "Retirement Plans for Small Business (SEP, SIMPLE, Qualified Plans)",
     "keywords": ["sep", "simple ira", "retirement plan", "small business retirement",
                  "qualified plan", "401k small business"]},
    {"num": 590,   "title": "Individual Retirement Arrangements (IRAs) — General",
     "keywords": ["ira", "individual retirement", "retirement account"]},
    {"num": "590-A", "title": "Contributions to Individual Retirement Arrangements (IRAs)",
     "keywords": ["ira contribution", "roth ira", "traditional ira", "retirement contribution",
                  "ira deduction"]},
    {"num": "590-B", "title": "Distributions from Individual Retirement Arrangements (IRAs)",
     "keywords": ["ira distribution", "ira withdrawal", "rmd", "required minimum distribution",
                  "retirement withdrawal"]},
    {"num": 596,   "title": "Earned Income Credit (EIC)",
     "keywords": ["earned income credit", "eic", "eitc", "earned income tax credit"]},
    {"num": 946,   "title": "How To Depreciate Property",
     "keywords": ["depreciation", "macrs", "bonus depreciation", "section 179",
                  "property depreciation", "fixed assets"]},
    {"num": 970,   "title": "Tax Benefits for Education",
     "keywords": ["education", "tuition", "student loan", "529", "american opportunity",
                  "lifetime learning", "scholarship"]},
    {"num": "4681", "title": "Canceled Debts, Foreclosures, Repossessions, and Abandonments",
     "keywords": ["canceled debt", "forgiven debt", "foreclosure", "repossession",
                  "1099-c", "debt relief"]},
    {"num": "15",  "title": "Employer's Tax Guide (Circular E)",
     "keywords": ["employer", "payroll", "withholding", "employment tax", "w-2",
                  "circular e", "federal tax deposit"]},
    {"num": "51",  "title": "Agricultural Employer's Tax Guide",
     "keywords": ["farm", "agricultural", "farmworker", "agriculture payroll"]},
    {"num": 54,    "title": "Tax Guide for U.S. Citizens and Resident Aliens Abroad",
     "keywords": ["expat", "foreign income", "exclusion", "abroad", "overseas",
                  "foreign earned income"]},
    {"num": 505,   "title": "Tax Withholding and Estimated Tax",
     "keywords": ["withholding", "estimated tax", "quarterly tax", "w-4",
                  "underpayment penalty"]},
    {"num": 521,   "title": "Moving Expenses",
     "keywords": ["moving", "relocation", "move expenses"]},
    {"num": 523,   "title": "Selling Your Home",
     "keywords": ["home sale", "selling house", "principal residence", "exclusion",
                  "real estate sale"]},
    {"num": 527,   "title": "Residential Rental Property",
     "keywords": ["rental", "rental income", "landlord", "residential rental",
                  "schedule e"]},
    {"num": 529,   "title": "Miscellaneous Deductions",
     "keywords": ["miscellaneous deductions", "unreimbursed", "investment expenses",
                  "hobby loss"]},
    {"num": 530,   "title": "Tax Information for Homeowners",
     "keywords": ["homeowner", "mortgage interest", "property tax", "home deduction",
                  "first time homebuyer"]},
    {"num": 537,   "title": "Installment Sales",
     "keywords": ["installment sale", "seller financing", "deferred payment"]},
    {"num": 541,   "title": "Partnerships",
     "keywords": ["partnership", "general partner", "limited partner", "schedule k-1",
                  "pass-through"]},
    {"num": 542,   "title": "Corporations",
     "keywords": ["corporation", "c corp", "s corp", "corporate tax", "dividends received"]},
    {"num": 547,   "title": "Casualties, Disasters, and Thefts",
     "keywords": ["casualty", "disaster", "theft", "loss deduction", "natural disaster"]},
    {"num": 587,   "title": "Business Use of Your Home (Including Use by Daycare Providers)",
     "keywords": ["home office", "business use home", "daycare home", "home office deduction"]},
    {"num": 908,   "title": "Bankruptcy Tax Guide",
     "keywords": ["bankruptcy", "debt discharge", "insolvency"]},
    {"num": 915,   "title": "Social Security and Equivalent Railroad Retirement Benefits",
     "keywords": ["social security", "railroad retirement", "ssa benefits", "ss income"]},
    {"num": 925,   "title": "Passive Activity and At-Risk Rules",
     "keywords": ["passive activity", "at-risk", "passive loss", "material participation"]},
    {"num": 936,   "title": "Home Mortgage Interest Deduction",
     "keywords": ["mortgage interest", "home mortgage", "interest deduction",
                  "qualified home", "second home"]},
    {"num": 939,   "title": "General Rule for Pensions and Annuities",
     "keywords": ["pension", "annuity", "simplified method", "retirement income"]},
    {"num": 969,   "title": "Health Savings Accounts and Other Tax-Favored Health Plans",
     "keywords": ["hsa", "health savings", "fsa", "hra", "medical savings",
                  "high deductible health plan"]},
]


def irs(query: str, num: int) -> list[dict]:
    """IRS publications — curated keyword-match index.

    IRS has no public search API.  The US Tax Court Dawson API
    (public-api-green.dawson.ustaxcourt.gov) is currently DISABLED as of
    2026-06-29 — Tax Court case opinions are a KNOWN GAP; revisit when
    that API is re-enabled.

    download_url = https://www.irs.gov/pub/irs-pdf/p{N}.pdf (verified 200 OK).
    License: Public Domain (IRS) — U.S. Government works per 17 USC §105.
    """
    q_lower = query.lower()
    q_words = set(re.split(r"\W+", q_lower))

    scored: list[tuple[int, dict]] = []
    for pub in _IRS_PUBS:
        keywords = pub["keywords"]
        score = 0
        for kw in keywords:
            # Score phrase matches higher than word matches.
            if kw in q_lower:
                score += 3
            else:
                kw_words = set(re.split(r"\W+", kw))
                score += len(kw_words & q_words)
        if score > 0:
            scored.append((score, pub))

    # Sort by score descending, cap at num.
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [pub for _, pub in scored[:num]]

    # If nothing matched (very narrow query), surface Pub 17 as a general fallback.
    if not top and num >= 1:
        top = [_IRS_PUBS[0]]  # Pub 17 — broadest publication

    out: list[dict] = []
    for pub in top:
        num_str = str(pub["num"])
        pdf_url = f"https://www.irs.gov/pub/irs-pdf/p{num_str}.pdf"
        out.append({
            "source": "irs",
            "title": f"IRS Publication {num_str}: {pub['title']}",
            "author": "Internal Revenue Service",
            "year": "",
            "language": "en",
            "license": "Public Domain (IRS)",
            "url": f"https://www.irs.gov/publications/p{num_str}",
            "cover": "",
            "formats": ["pdf"],
            "download_urls": {"pdf": pdf_url},
            "excerpt": f"IRS Publication {num_str}: {pub['title']}",
            "type": "publication",
        })
    return out
