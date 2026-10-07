#!/usr/bin/env python3
"""ArcturionResearch router: one query, many sources, one merged answer.

Classifies the query's intent, picks that intent's tool stack, runs the tools
as a cheapest-first waterfall (escalating only when results are thin), enforces
per-source rate budgets, caches clean results, deduplicates, and summarizes.

CLI:
    python3 -m arcturion_research "what is the current state of <topic>"
    python3 -m arcturion_research --intent academic "retrieval evaluation"
    python3 -m arcturion_research --json --max-tokens 800 "<query>"
    python3 -m arcturion_research --explain "<query>"     # show the route plan only

Library:
    from arcturion_research import research
    result = research("query", intent=None, max_tokens=1200, ttl_h=24)

Pluggable models (both optional):
    set_classifier(fn)   fn(query, intents) -> intent | None
    set_summarizer(fn)   fn(text, max_words, system_prompt) -> str
Without them the router uses a keyword classifier and returns results with a
clearly labeled "no summarizer configured" note instead of a summary.

State (rate budgets + cache) lives in ARC_RESEARCH_STATE_DIR
(default ~/.cache/arcturion-research).
"""
from __future__ import annotations
import argparse, contextlib, fcntl, hashlib, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _FuturesTimeout
from pathlib import Path
from typing import Callable

STATE_DIR = Path(os.environ.get("ARC_RESEARCH_STATE_DIR") or "~/.cache/arcturion-research").expanduser()
CACHE_DIR = STATE_DIR / "cache"
BUDGET_FILE = STATE_DIR / "budget_state.json"
CACHE_TTL_DEFAULT_S = 24 * 3600
# Per-batch wall-clock ceiling. A slow/hung adapter is abandoned (never joined) so
# it can't block the caller; the adapter's own urllib timeout still caps the call.
TOOL_CALL_TIMEOUT_S = 25

# Per-tool deadline overrides for sources that are legitimately slower than a
# plain HTTP search. `agy` runs a full agent loop (multi-query Google Search
# grounding + redirect resolution), observed at 10-25s — right at the default,
# so it would be abandoned intermittently. A batch waits for the slowest tool
# in it, so only batches containing a slow tool pay the longer deadline.
TOOL_TIMEOUT_OVERRIDES = {"agy": 90}


def _batch_timeout(tools) -> float:
    return max([TOOL_CALL_TIMEOUT_S] +
               [TOOL_TIMEOUT_OVERRIDES.get(t, 0) for t in tools])
# Stop escalating down a tool stack once this many non-error results accumulate.
# Turns the ROUTES weights into a real "cheapest first, escalate only on miss"
# waterfall instead of firing every tier in parallel on every query.
WATERFALL_MIN_RESULTS = 6

# ============================================================
# Intent taxonomy + per-intent tool stacks
# ============================================================
INTENTS = ["web", "news", "academic", "video", "social", "image",
           "places", "factual", "code", "deep", "books"]

# Each stack is ordered: cheapest/fastest first, escalate only on miss.
# Each entry = (tool_name, max_results, weight)
ROUTES = {
    "books":    [("legal_books", 5, 1.0), ("archive_org", 5, 0.6)],
    "web":      [("tavily", 5, 1.0), ("jina", 3, 0.6), ("duckduckgo", 5, 0.4)],
    "news":     [("newsdata", 5, 1.0), ("tavily", 4, 0.8), ("gdelt", 5, 0.6),
                 ("hackernews", 3, 0.5)],
    "academic": [("openalex", 5, 1.0), ("arxiv", 5, 0.9), ("semantic_scholar", 4, 0.8),
                 ("wikipedia", 2, 0.4)],
    "video":    [("youtube_yt_dlp", 5, 1.0), ("serpapi_youtube", 5, 0.7)],
    "social":   [("reddit", 6, 1.0), ("hackernews", 5, 0.7), ("x_search", 5, 0.8)],
    "image":    [("wikimedia", 4, 0.9), ("unsplash", 4, 0.8), ("pexels", 4, 0.8),
                 ("pixabay", 4, 0.7), ("met", 3, 0.6), ("nasa_images", 3, 0.6)],
    "places":   [("osm_nominatim", 5, 1.0), ("wikipedia", 2, 0.4)],
    "factual":  [("wolfram", 1, 1.0), ("wikipedia", 2, 0.7), ("agy", 3, 0.9)],
    "code":     [("github_search", 5, 1.0), ("hackernews", 3, 0.5),
                 ("stackoverflow", 4, 0.8)],
    "deep":     [("agy", 6, 1.0), ("tavily", 5, 0.9), ("openalex", 4, 0.7)],
}

# `agy` (the Antigravity CLI, Google Search grounding) leads the factual and deep
# stacks when it is installed and signed in; it returns [] otherwise and the
# waterfall falls through to Tavily.
#
# `perplexity` is SHELVED, not removed: the adapter works, so re-enabling is a
# one-line change to a stack above. It is deliberately absent from every route
# because it bills per call, and its deep-research model is expensive enough
# (dollars per call, not fractions of a cent) that it should only ever be opted
# into by hand. If it comes back, keep it on the cheap `sonar` model.

# Per-source rate budgets (calls per rolling window). Soft caps — router
# downgrades to next tool if exceeded.
RATE_BUDGETS = {
    # source: (max_calls, window_seconds)
    "agy":             (300,  86400),  # cap guards the signed-in account's quota
    "tavily":          (1000, 86400),  # 1000/day free
    "perplexity":      (50,   86400),  # conservative (shelved — see ROUTES note)
    "newsdata":        (200,  86400),  # 200/day free
    "serpapi":         (3,    3600),   # 100/mo == ~3/hr
    "serpapi_youtube": (3,    3600),
    "wolfram":         (66,   86400),  # 2000/mo
    "openalex":        (10000, 86400), # unlimited but be polite
    "arxiv":           (200,  86400),
    "github_search":   (60,   3600),   # GitHub unauth
    "x_search":        (50,   86400),  # via browser/scrape conservative
}

# ============================================================
# Pluggable classifier + summarizer
# ============================================================
_CLASSIFIER: Callable[[str, list[str]], str | None] | None = None
_SUMMARIZER: Callable[[str, int, str], str] | None = None


def set_classifier(fn: Callable[[str, list[str]], str | None] | None) -> None:
    """Install fn(query, intents) -> intent. None restores the keyword classifier."""
    global _CLASSIFIER
    _CLASSIFIER = fn


def set_summarizer(fn: Callable[[str, int, str], str] | None) -> None:
    """Install fn(text, max_words, system_prompt) -> summary. None removes it."""
    global _SUMMARIZER
    _SUMMARIZER = fn


def _heuristic_intent(query: str) -> str:
    q = query.lower()
    if any(x in q for x in ("ebook", "audiobook", "books about", "book about")):
        return "books"
    # Specific phrases, not bare "study": that misrouted "study Python" and
    # "study tips" to the academic stack.
    if any(x in q for x in ["paper", "doi", "arxiv", "research finding",
                            "case study", "clinical study"]):
        return "academic"
    if any(x in q for x in ["youtube", "video", "watch"]):
        return "video"
    if any(x in q for x in ["reddit", "twitter", "x post"]):
        return "social"
    if any(x in q for x in ["image of", "photo of", "picture of"]):
        return "image"
    if any(x in q for x in ["near", "where is", "address"]):
        return "places"
    if any(x in q for x in ["latest", "today", "this week", "news"]):
        return "news"
    if any(x in q for x in ["github.com", "code for", "python library", "package"]):
        return "code"
    return "web"


def _classify_intent(query: str) -> str:
    """Installed classifier first; keyword heuristic on any failure."""
    if _CLASSIFIER is not None:
        try:
            got = _CLASSIFIER(query, INTENTS)
            if got in INTENTS:
                return got
        except Exception:
            pass
    return _heuristic_intent(query)


def _summarize(text: str, max_words: int = 250, system: str | None = None) -> str:
    """Distill the merged results with the installed summarizer.

    Never returns the raw input dressed up as a summary. With no summarizer, or
    when it fails, the caller gets a clearly labeled note and the full results.
    """
    if not text:
        return ""
    if len(text) < 300:
        return text  # genuinely too short to compress
    sys_msg = system or (
        "You are a research distillation assistant. Compress the input into "
        f"<= {max_words} words. Preserve facts, names, numbers, and source URLs. "
        "Bullet form. No preamble."
    )
    if _SUMMARIZER is None:
        return "[research: no summarizer configured; see results]"
    try:
        out = _SUMMARIZER(text, max_words, sys_msg)
        if out and out.strip():
            return out.strip()
        err = "empty summary"
    except Exception as e:
        err = type(e).__name__
    return f"[research: summarization failed ({err}); see results]"


# ============================================================
# Atomic IO helper (crash-safe JSON writes)
# ============================================================
def _atomic_write_json(path: Path, obj) -> None:
    """Write JSON crash-safely: serialize to a temp sibling then os.replace() it
    into place (atomic on the same filesystem). A crash mid-write leaves the old
    file intact instead of a truncated one that would parse-fail and silently
    reset the state to {}."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(json.dumps(obj, default=str))
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ============================================================
# Budget tracking (atomic check+charge — see _budget_reserve)
# ============================================================
_BUDGET_LOCK = threading.Lock()  # serializes check+charge within this process


def _load_budget() -> dict:
    if BUDGET_FILE.exists():
        try:
            return json.loads(BUDGET_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_budget(b: dict) -> None:
    try:
        _atomic_write_json(BUDGET_FILE, b)
    except Exception:
        pass


@contextlib.contextmanager
def _budget_file_lock():
    """Cross-process exclusion around the budget read-modify-write, via flock on a
    sidecar .lock file. Degrades to a no-op (the in-process _BUDGET_LOCK still
    holds) if the lock file can't be opened."""
    lock_path = BUDGET_FILE.with_name(f"{BUDGET_FILE.name}.lock")
    f = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except OSError:
        if f is not None:
            f.close()
        yield
        return
    try:
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def _budget_ok(source: str) -> bool:
    """Read-only rate check for plan()/--explain. The authoritative, race-free
    check happens in _budget_reserve()."""
    if source not in RATE_BUDGETS:
        return True
    cap, window = RATE_BUDGETS[source]
    now = time.time()
    fresh = [t for t in _load_budget().get(source, []) if now - t < window]
    return len(fresh) < cap


def _budget_reserve(source: str) -> bool:
    """Atomically check the rate cap and, if under it, charge one call.

    Returns True (permitted + charged) or False (rate-limited). Check and charge run
    in ONE critical section — threading.Lock for in-process parallelism plus an flock
    for cross-process safety — closing the TOCTOU/lost-update race where concurrent
    workers all read the same count, all pass the cap, and overwrite each other's
    charges (soft cap silently unenforced, file drifting below reality)."""
    if source not in RATE_BUDGETS:
        return True
    cap, window = RATE_BUDGETS[source]
    now = time.time()
    with _BUDGET_LOCK, _budget_file_lock():
        b = _load_budget()
        fresh = [t for t in b.get(source, []) if now - t < window]
        permitted = len(fresh) < cap
        if permitted:
            fresh.append(now)
        # Persist the pruned (GC'd, <=2x cap) list — even on rejection, so stale
        # timestamps outside the window don't accumulate unbounded.
        b[source] = fresh[-(cap * 2):]
        _save_budget(b)
        return permitted


# ============================================================
# Cache (sha256(intent+tool+query) → 24h)
# ============================================================
def _cache_key(query: str, intent: str, tool: str) -> str:
    # sha256 truncated to 32 hex (128-bit). The old sha1[:16] (64-bit) risked
    # birthday collisions across the cache at scale.
    return hashlib.sha256(f"{intent}|{tool}|{query}".encode("utf-8")).hexdigest()[:32]


def _cacheable(items: list[dict]) -> bool:
    """Only genuine, error-free, non-empty result sets are worth caching. Caching an
    error/skipped sentinel would suppress the tool for the full TTL after a transient
    failure; caching [] would suppress it after an empty fluke."""
    if not items:
        return False
    return not any(
        isinstance(it, dict) and ("error" in it or "skipped" in it)
        for it in items
    )


def _cache_get(key: str, ttl_s: int) -> dict | None:
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
    except Exception:
        return None
    if time.time() - rec.get("ts", 0) > ttl_s:
        return None
    return rec


def _cache_set(key: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{key}.json"
    payload = {"ts": time.time(), **payload}
    try:
        _atomic_write_json(p, payload)
    except Exception:
        pass


# ============================================================
# Tool dispatch — adapters live in ./tools/
# ============================================================
def _call_tool(tool: str, query: str, max_results: int) -> list[dict]:
    """Adapter dispatch. Returns list of {title, url, snippet, source}."""
    from . import registry  # local import: registry.py wires names to callables
    fn: Callable | None = registry.TOOLS.get(tool)
    if fn is None:
        return []
    try:
        return fn(query, max_results=max_results) or []
    except Exception as e:
        return [{"error": f"{tool}: {e}", "source": tool}]


# ============================================================
# Deduplication
# ============================================================
def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for idx, it in enumerate(items):
        url = (it.get("url") or "").rstrip("/").lower()
        title = (it.get("title") or "").strip().lower()[:100]
        if url:
            key = url
        elif title:
            key = f"t:{title}"
        else:
            # No url and no title: fall back to a snippet hash, then the index, so
            # distinct url/title-less items don't all collapse to a single "t:".
            snip = (it.get("snippet") or it.get("summary") or "").strip().lower()
            key = (f"s:{hashlib.sha256(snip.encode('utf-8')).hexdigest()[:16]}"
                   if snip else f"i:{idx}")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# ============================================================
# Core
# ============================================================
def plan(query: str, intent: str | None = None) -> dict:
    """Return the route plan without executing — useful for dry-run/explain."""
    intent = intent or _classify_intent(query)
    stack = ROUTES.get(intent, ROUTES["web"])
    plan_tools = [(t, n, w, _budget_ok(t)) for (t, n, w) in stack]
    return {"intent": intent, "stack": plan_tools, "query": query}


def research(
    query: str,
    intent: str | None = None,
    max_tokens: int = 1200,
    ttl_h: int = 24,
    parallel: int = 3,
    min_results: int = WATERFALL_MIN_RESULTS,
) -> dict:
    """Execute a research query with source-level outcome evidence.

    Tools run as a weighted waterfall: highest-weight (cheapest/primary) tools first,
    escalating down the stack in batches of `parallel` only until `min_results`
    non-error results accumulate ("escalate only on miss")."""
    t0 = time.time()
    p = plan(query, intent=intent)
    intent = p["intent"]
    stack = ROUTES.get(intent, ROUTES["web"])

    raw: list[dict] = []
    sources_used: list[str] = []
    source_status: list[dict[str, str]] = []
    ttl_s = ttl_h * 3600

    def run(tool: str, n: int) -> tuple[str, list[dict]]:
        # Cache first: a hit makes no API call, so it must not be gated by (or
        # counted against) the rate budget.
        ck = _cache_key(query, intent, tool)
        hit = _cache_get(ck, ttl_s)
        if hit is not None:
            return tool, hit.get("items", [])
        # Reserve a rate slot atomically *before* the call (closes the TOCTOU race,
        # and means an abandoned thread cannot charge budget after we've returned).
        if not _budget_reserve(tool):
            return tool, [{"skipped": "rate-limited", "source": tool}]
        items = _call_tool(tool, query, n)
        if _cacheable(items):  # never cache errors/empties — would suppress the tool
            _cache_set(ck, {"items": items, "tool": tool, "intent": intent, "query": query})
        return tool, items

    def run_batch(batch: list[tuple[str, int]]) -> list[tuple[str, list[dict]]]:
        # Bounded concurrent batch. Stragglers past the batch deadline are abandoned
        # (executor shut down with wait=False) so a hung adapter can never block the
        # caller — unlike the old `with ThreadPoolExecutor`, whose __exit__ joined
        # every worker regardless of the per-future timeout.
        results: list[tuple[str, list[dict]]] = []
        collected: set = set()
        ex = ThreadPoolExecutor(max_workers=max(1, parallel))
        futs = {ex.submit(run, t, n): t for (t, n) in batch}
        try:
            for f in as_completed(futs, timeout=_batch_timeout(t for (t, _n) in batch)):
                collected.add(f)
                try:
                    results.append(f.result())
                except Exception as e:
                    results.append((futs[f], [{"error": str(e), "source": futs[f]}]))
        except _FuturesTimeout:
            for f in futs:
                if f not in collected:
                    results.append((futs[f], [{"error": "timeout", "source": futs[f]}]))
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return results

    # Weighted waterfall: order by weight (desc), escalate in batches of `parallel`
    # only until we have enough non-error results.
    ordered = sorted(stack, key=lambda tnw: tnw[2], reverse=True)
    good = 0
    i = 0
    while i < len(ordered):
        batch = [(t, n) for (t, n, _w) in ordered[i:i + max(1, parallel)]]
        i += len(batch)
        for tool, items in run_batch(batch):
            real = [x for x in items if isinstance(x, dict)
                    and "error" not in x and "skipped" not in x]
            if real:
                sources_used.append(tool)
                source_status.append({"source": tool, "status": "used",
                                      "detail": f"{len(real)} usable result(s)"})
            elif any(isinstance(x, dict) and x.get("skipped") for x in items):
                source_status.append({"source": tool, "status": "skipped",
                                      "detail": str(next(x["skipped"] for x in items
                                                         if isinstance(x, dict) and x.get("skipped")))})
            elif any(isinstance(x, dict) and x.get("error") for x in items):
                source_status.append({"source": tool, "status": "error",
                                      "detail": str(next(x["error"] for x in items
                                                         if isinstance(x, dict) and x.get("error")))})
            else:
                source_status.append({"source": tool, "status": "empty",
                                      "detail": "no usable results returned"})
            for it in items:
                if isinstance(it, dict) and ("url" in it or "title" in it):
                    it.setdefault("source", tool)
                    raw.append(it)
            good += len(real)
        if good >= min_results:
            break

    deduped = _dedupe(raw)[:30]  # hard cap
    # Build a compact text blob for summarization
    blob_lines = []
    for it in deduped:
        title = it.get("title", "").strip()[:200]
        url = it.get("url", "").strip()
        snip = (it.get("snippet") or it.get("summary") or "").strip()[:400]
        if title or url or snip:
            blob_lines.append(f"- [{it.get('source')}] {title} | {url}\n  {snip}")
    blob = "\n".join(blob_lines)

    max_words = max(50, max_tokens // 4)
    summary = _summarize(blob, max_words=max_words) if blob else ""

    degraded = not bool(deduped)
    return {
        "query": query,
        "intent": intent,
        "sources_used": sources_used,
        "source_status": source_status,
        "results": deduped,
        "summary": summary,
        "degraded": degraded,
        "failure_reason": ("No configured source returned usable results; "
                           "inspect source_status before relying on this query."
                           if degraded else ""),
        "duration_s": round(time.time() - t0, 2),
        "ts": time.time(),
    }


# ============================================================
# CLI
# ============================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="arcturion_research", description="Multi-source research router.")
    ap.add_argument("query", nargs="+")
    ap.add_argument("--intent", choices=INTENTS, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--ttl-hours", type=int, default=24)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--min-results", type=int, default=WATERFALL_MIN_RESULTS,
                    help="Stop escalating down the tool stack after this many results")
    ap.add_argument("--explain", action="store_true",
                    help="Show route plan only, don't execute")
    args = ap.parse_args(argv)
    q = " ".join(args.query)

    if args.explain:
        out = plan(q, intent=args.intent)
        print(json.dumps(out, indent=2, default=str))
        return 0

    res = research(q, intent=args.intent,
                   max_tokens=args.max_tokens,
                   ttl_h=args.ttl_hours,
                   parallel=args.parallel,
                   min_results=args.min_results)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"\n📡 Intent: {res['intent']}  |  Sources: {', '.join(res['sources_used']) or '(none)'}  |  {res['duration_s']}s")
        for status in res["source_status"]:
            print(f"  [{status['status']}] {status['source']}: {status['detail']}")
        print("─" * 60)
        print(res["summary"] or "(empty)")
        print("─" * 60)
        print(f"📚 {len(res['results'])} unique results — top 5:")
        for it in res["results"][:5]:
            print(f"  • [{it.get('source')}] {it.get('title','')[:90]}")
            if it.get("url"):
                print(f"    {it['url']}")
    if res["degraded"]:
        print(f"⚠️ {res['failure_reason']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
