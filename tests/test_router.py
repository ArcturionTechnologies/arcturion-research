#!/usr/bin/env python3
"""Regression tests for the router: pins eight previously fixed bugs.

Every test that exercises research() patches out _summarize and _call_tool, so
no model is loaded and no network call is made. Budget/cache state is redirected
to a temp dir.
"""
from __future__ import annotations
import hashlib, io, json, shutil, sys, tempfile, threading, time, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from arcturion_research import router as rr  # noqa: E402

REAL_SUMMARIZE = rr._summarize  # Base stubs the module attribute; keep the real one


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rr_test_"))
        self._orig_budget = rr.BUDGET_FILE
        self._orig_cache = rr.CACHE_DIR
        rr.BUDGET_FILE = self.tmp / "budget_state.json"
        rr.CACHE_DIR = self.tmp / "cache"
        # Hard guard: never let a test cold-load an LLM via the summarizer.
        p = mock.patch.object(rr, "_summarize", lambda *a, **k: "TEST_SUMMARY")
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self) -> None:
        rr.BUDGET_FILE = self._orig_budget
        rr.CACHE_DIR = self._orig_cache
        shutil.rmtree(self.tmp, ignore_errors=True)


# --- Bug 1: budget check+charge must be atomic under parallelism -------------
class TestBudgetReserve(Base):
    def test_reserve_respects_cap_under_contention(self):
        rr.RATE_BUDGETS["__test__"] = (5, 3600)
        try:
            results: list[bool] = []
            lock = threading.Lock()

            def worker():
                ok = rr._budget_reserve("__test__")
                with lock:
                    results.append(ok)

            threads = [threading.Thread(target=worker) for _ in range(40)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            granted = sum(1 for r in results if r)
            self.assertEqual(granted, 5, f"cap=5 but {granted} grants (TOCTOU race)")
            charged = len(json.loads(rr.BUDGET_FILE.read_text())["__test__"])
            self.assertGreaterEqual(charged, 5)
            self.assertLessEqual(charged, 10, "GC bound 2×cap exceeded")
        finally:
            rr.RATE_BUDGETS.pop("__test__", None)

    def test_unknown_source_always_permitted(self):
        self.assertTrue(rr._budget_reserve("not-a-real-source"))

    def test_reserve_blocks_when_full(self):
        rr.RATE_BUDGETS["__full__"] = (2, 3600)
        try:
            self.assertTrue(rr._budget_reserve("__full__"))
            self.assertTrue(rr._budget_reserve("__full__"))
            self.assertFalse(rr._budget_reserve("__full__"))
        finally:
            rr.RATE_BUDGETS.pop("__full__", None)


# --- Bug 2: never cache error/skipped/empty results -------------------------
class TestCacheHygiene(Base):
    def test_cacheable_predicate(self):
        self.assertFalse(rr._cacheable([]))
        self.assertFalse(rr._cacheable([{"error": "x", "source": "t"}]))
        self.assertFalse(rr._cacheable([{"skipped": "rate-limited", "source": "t"}]))
        self.assertTrue(rr._cacheable([{"title": "ok", "url": "http://x", "source": "t"}]))

    def test_research_does_not_cache_errors(self):
        def fake_call(tool, q, n):
            return [{"error": "boom", "source": tool}]

        with mock.patch.object(rr, "_call_tool", fake_call), \
             mock.patch.object(rr, "ROUTES", {"web": [("faketool", 3, 1.0)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            rr.research("hello world", intent="web", parallel=1)
        files = list(rr.CACHE_DIR.glob("*.json")) if rr.CACHE_DIR.exists() else []
        self.assertEqual(files, [], f"error result was cached: {files}")

    def test_research_caches_clean_results(self):
        def fake_call(tool, q, n):
            return [{"title": "real", "url": "http://real", "snippet": "s", "source": tool}]

        with mock.patch.object(rr, "_call_tool", fake_call), \
             mock.patch.object(rr, "ROUTES", {"web": [("faketool", 3, 1.0)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            rr.research("hello world", intent="web", parallel=1, min_results=1)
        files = list(rr.CACHE_DIR.glob("*.json"))
        self.assertEqual(len(files), 1, "clean result should have been cached")


class TestOutcomeEvidence(Base):
    def test_empty_sources_are_explicitly_degraded(self):
        with mock.patch.object(rr, "_call_tool", lambda *a: []), \
             mock.patch.object(rr, "ROUTES", {"web": [("empty", 3, 1.0)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            res = rr.research("q", intent="web", parallel=1, min_results=1)
        self.assertTrue(res["degraded"])
        self.assertIn("No configured source", res["failure_reason"])
        self.assertEqual(res["source_status"], [{
            "source": "empty", "status": "empty", "detail": "no usable results returned"
        }])

    def test_source_error_is_preserved_in_outcome_evidence(self):
        with mock.patch.object(rr, "_call_tool",
                               lambda tool, q, n: [{"error": "offline", "source": tool}]), \
             mock.patch.object(rr, "ROUTES", {"web": [("broken", 3, 1.0)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            res = rr.research("q", intent="web", parallel=1, min_results=1)
        self.assertEqual(res["source_status"], [{
            "source": "broken", "status": "error", "detail": "offline"
        }])

    def test_cli_returns_nonzero_for_degraded_research(self):
        result = {
            "intent": "web", "sources_used": [], "source_status": [{
                "source": "broken", "status": "error", "detail": "offline"
            }], "duration_s": 0.01, "summary": "", "results": [],
            "degraded": True, "failure_reason": "No configured source returned usable results"
        }
        with mock.patch.object(rr, "research", return_value=result), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(rr.main(["query"]), 2)


# --- Bug 3: a hung tool must not block research() ---------------------------
class TestNoHang(Base):
    def test_returns_despite_hanging_tool(self):
        def slow(tool, q, n):
            if tool == "slowtool":
                time.sleep(2)   # simulated hang; abandoned past TOOL_CALL_TIMEOUT_S
                return []        # non-cacheable, so the abandoned thread is inert
            return [{"title": "fast", "url": "http://fast", "snippet": "s", "source": tool}]

        with mock.patch.object(rr, "_call_tool", slow), \
             mock.patch.object(rr, "ROUTES",
                               {"web": [("fasttool", 3, 1.0), ("slowtool", 3, 0.9)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}), \
             mock.patch.object(rr, "TOOL_CALL_TIMEOUT_S", 1):
            t0 = time.time()
            res = rr.research("q", intent="web", parallel=2, min_results=99)
            dt = time.time() - t0
        self.assertLess(dt, 15, f"research() blocked on hung tool: {dt:.1f}s")
        self.assertTrue(any(r.get("url") == "http://fast" for r in res["results"]),
                        "fast tool result missing")


# --- Bug 4: ROUTES weights drive a real waterfall (escalate only on miss) ----
class TestWaterfall(Base):
    def test_short_circuits_when_primary_sufficient(self):
        calls: list[str] = []

        def fake(tool, q, n):
            calls.append(tool)
            return [{"title": f"{tool}-{k}", "url": f"http://{tool}/{k}", "source": tool}
                    for k in range(6)]

        with mock.patch.object(rr, "_call_tool", fake), \
             mock.patch.object(rr, "ROUTES", {"web": [
                 ("primary", 6, 1.0), ("secondary", 3, 0.5), ("tertiary", 3, 0.4)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            rr.research("q", intent="web", parallel=1, min_results=6)
        self.assertIn("primary", calls)
        self.assertNotIn("secondary", calls, "waterfall did not short-circuit")
        self.assertNotIn("tertiary", calls)

    def test_escalates_when_primary_insufficient(self):
        calls: list[str] = []

        def fake(tool, q, n):
            calls.append(tool)
            return [{"title": tool, "url": f"http://{tool}", "source": tool}]  # 1 each

        with mock.patch.object(rr, "_call_tool", fake), \
             mock.patch.object(rr, "ROUTES",
                               {"web": [("primary", 6, 1.0), ("secondary", 3, 0.5)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            rr.research("q", intent="web", parallel=1, min_results=6)
        self.assertIn("primary", calls)
        self.assertIn("secondary", calls, "did not escalate on miss")

    def test_highest_weight_runs_first(self):
        order: list[str] = []

        def fake(tool, q, n):
            order.append(tool)
            return []  # always miss so the whole stack runs, in weight order

        with mock.patch.object(rr, "_call_tool", fake), \
             mock.patch.object(rr, "ROUTES", {"web": [
                 ("low", 3, 0.4), ("high", 3, 1.0), ("mid", 3, 0.7)]}), \
             mock.patch.object(rr, "RATE_BUDGETS", {}):
            rr.research("q", intent="web", parallel=1, min_results=99)
        self.assertEqual(order, ["high", "mid", "low"], f"not weight-ordered: {order}")


# --- Bug 5: budget save must be atomic --------------------------------------
class TestAtomicSave(Base):
    def test_save_budget_valid_no_sidecar(self):
        rr._save_budget({"tavily": [1.0, 2.0]})
        self.assertEqual(json.loads(rr.BUDGET_FILE.read_text()), {"tavily": [1.0, 2.0]})
        leftovers = list(self.tmp.glob("*.tmp*"))
        self.assertEqual(leftovers, [], f"temp sidecar left behind: {leftovers}")

    def test_atomic_write_json_replaces(self):
        p = self.tmp / "x.json"
        rr._atomic_write_json(p, {"a": 1})
        rr._atomic_write_json(p, {"a": 2})
        self.assertEqual(json.loads(p.read_text()), {"a": 2})


# --- Bug 6: dedupe must not collapse distinct url/title-less items -----------
class TestDedupe(Base):
    def test_distinct_snippet_only_kept(self):
        items = [{"snippet": "alpha", "source": "a"}, {"snippet": "beta", "source": "b"}]
        self.assertEqual(len(rr._dedupe(items)), 2, "distinct snippets collapsed")

    def test_identical_snippet_only_deduped(self):
        items = [{"snippet": "same", "source": "a"}, {"snippet": "same", "source": "b"}]
        self.assertEqual(len(rr._dedupe(items)), 1)

    def test_fully_empty_items_kept(self):
        items = [{"source": "a"}, {"source": "b"}]
        self.assertEqual(len(rr._dedupe(items)), 2, "empty items collapsed to one")

    def test_url_dedupe_still_works(self):
        items = [{"url": "http://x/", "source": "a"}, {"url": "http://x", "source": "b"}]
        self.assertEqual(len(rr._dedupe(items)), 1, "url normalization broke")


# --- Bug 7: bare 'study' must not misroute to academic ----------------------
class TestClassify(Base):
    def _heuristic(self):
        # No classifier installed -> deterministic keyword path.
        return mock.patch.object(rr, "_CLASSIFIER", None)

    def test_study_verb_not_academic(self):
        with self._heuristic():
            self.assertNotEqual(rr._classify_intent("study Python"), "academic")
            self.assertNotEqual(rr._classify_intent("study tips for finals"), "academic")

    def test_case_study_is_academic(self):
        with self._heuristic():
            self.assertEqual(rr._classify_intent("Tesla case study analysis"), "academic")

    def test_paper_still_academic(self):
        with self._heuristic():
            self.assertEqual(rr._classify_intent("arxiv paper on transformers"), "academic")


# --- Bug 8: cache key must be sha256, >=32 hex ------------------------------
class TestCacheKey(Base):
    def test_length_and_charset(self):
        k = rr._cache_key("q", "web", "tavily")
        self.assertGreaterEqual(len(k), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in k))

    def test_matches_sha256(self):
        expected = hashlib.sha256(b"web|tavily|q").hexdigest()[:32]
        self.assertEqual(rr._cache_key("q", "web", "tavily"), expected)


# --- pluggable classifier + summarizer --------------------------------------
class TestPlugins(Base):
    def test_installed_classifier_wins_and_bad_answers_fall_back(self):
        try:
            rr.set_classifier(lambda q, intents: "academic")
            self.assertEqual(rr._classify_intent("anything"), "academic")
            rr.set_classifier(lambda q, intents: "not-an-intent")
            self.assertEqual(rr._classify_intent("latest news"), "news")
            rr.set_classifier(lambda q, intents: 1 / 0)
            self.assertEqual(rr._classify_intent("latest news"), "news")
        finally:
            rr.set_classifier(None)

    def test_summarizer_contract(self):
        long = "x" * 400
        try:
            rr.set_summarizer(None)
            self.assertIn("no summarizer configured", REAL_SUMMARIZE(long))
            rr.set_summarizer(lambda text, n, system: "SHORT")
            self.assertEqual(REAL_SUMMARIZE(long), "SHORT")
            rr.set_summarizer(lambda text, n, system: "   ")
            self.assertIn("summarization failed", REAL_SUMMARIZE(long))
            self.assertEqual(REAL_SUMMARIZE("short text"), "short text")
            self.assertEqual(REAL_SUMMARIZE(""), "")
            self.assertNotEqual(REAL_SUMMARIZE(long), long)  # never echoes input as a summary
        finally:
            rr.set_summarizer(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
