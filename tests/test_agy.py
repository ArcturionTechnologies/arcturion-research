"""Tests for the `agy` (Antigravity CLI) research source + its router wiring.

Pure logic only — the agy subprocess and redirect resolution are both stubbed, so
this suite never spends quota or touches the network.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arcturion_research import router as rr  # noqa: E402
from arcturion_research.sources import agy  # noqa: E402


def _envelope(items, status="SUCCESS"):
    return {"status": status, "structured_output": {"items": items}}


class TestAgyNormalization(unittest.TestCase):
    def setUp(self):
        # Identity resolver: isolate normalization from redirect following.
        self._resolve_all = agy._resolve_all
        agy._resolve_all = lambda urls: urls

    def tearDown(self):
        agy._resolve_all = self._resolve_all

    def _search(self, env, **kw):
        orig, agy._run = agy._run, lambda prompt: env
        try:
            return agy.search("q", **kw)
        finally:
            agy._run = orig

    def test_normalizes_and_tags_source(self):
        got = self._search(_envelope([
            {"title": "T", "url": "https://example.com/a", "snippet": "S"},
        ]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["source"], "agy")
        self.assertEqual(got[0]["url"], "https://example.com/a")

    def test_drops_non_http_urls(self):
        """Guards against the model emitting a placeholder instead of omitting."""
        got = self._search(_envelope([
            {"title": "bad", "url": "N/A", "snippet": "s"},
            {"title": "also bad", "url": "", "snippet": "s"},
            {"title": "good", "url": "https://ok.com", "snippet": "s"},
        ]))
        self.assertEqual([r["url"] for r in got], ["https://ok.com"])

    def test_respects_max_results(self):
        items = [{"title": f"t{i}", "url": f"https://e.com/{i}", "snippet": "s"}
                 for i in range(10)]
        self.assertEqual(len(self._search(_envelope(items), max_results=3)), 3)

    def test_clamps_absurd_max_results(self):
        items = [{"title": f"t{i}", "url": f"https://e.com/{i}", "snippet": "s"}
                 for i in range(50)]
        self.assertEqual(len(self._search(_envelope(items), max_results=999)), 10)

    def test_fails_soft_on_error_status(self):
        self.assertEqual(self._search(_envelope([], status="ERROR")), [])

    def test_fails_soft_when_agy_unavailable(self):
        self.assertEqual(self._search(None), [])

    def test_tolerates_malformed_items(self):
        got = self._search(_envelope(["not a dict", None,
                                      {"title": "ok", "url": "https://x.com", "snippet": "s"}]))
        self.assertEqual(len(got), 1)


class TestRedirectResolution(unittest.TestCase):
    def test_passthrough_for_non_redirect(self):
        url = "https://example.com/real"
        self.assertEqual(agy._resolve(url), url)

    def test_resolve_all_skips_work_when_no_redirects(self):
        urls = ["https://a.com", "https://b.com"]
        self.assertEqual(agy._resolve_all(urls), urls)

    def test_http_error_still_yields_destination(self):
        """A 403 from a bot-hostile destination must not lose the citation."""
        import urllib.error

        redirect = f"https://{agy._REDIRECT_HOST}/grounding-api-redirect/XYZ"
        err = urllib.error.HTTPError(
            url="https://realsite.com/article", code=403,
            msg="Forbidden", hdrs=None, fp=None,
        )
        orig, agy.urllib.request.urlopen = agy.urllib.request.urlopen, \
            lambda *a, **k: (_ for _ in ()).throw(err)
        try:
            self.assertEqual(agy._resolve(redirect), "https://realsite.com/article")
        finally:
            agy.urllib.request.urlopen = orig

    def test_unresolvable_redirect_keeps_original(self):
        redirect = f"https://{agy._REDIRECT_HOST}/grounding-api-redirect/XYZ"
        orig, agy.urllib.request.urlopen = agy.urllib.request.urlopen, \
            lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        try:
            self.assertEqual(agy._resolve(redirect), redirect)
        finally:
            agy.urllib.request.urlopen = orig


class TestRouterWiring(unittest.TestCase):
    def test_agy_is_registered_in_TOOLS(self):
        """_call_tool dispatches via registry.TOOLS — a missing entry fails silently."""
        from arcturion_research import registry
        self.assertIn("agy", registry.TOOLS)

    def test_agy_is_primary_deep_source(self):
        deep = rr.ROUTES["deep"]
        self.assertEqual(deep[0][0], "agy")
        self.assertEqual(max(w for _t, _n, w in deep), deep[0][2])

    def test_tavily_is_deep_fallback(self):
        self.assertIn("tavily", [t for t, _n, _w in rr.ROUTES["deep"]])

    def test_perplexity_is_shelved_everywhere(self):
        """Shelved means present in the registry but absent from every route."""
        from arcturion_research import registry
        self.assertIn("perplexity", registry.TOOLS)
        for intent, stack in rr.ROUTES.items():
            self.assertNotIn("perplexity", [t for t, _n, _w in stack],
                             f"perplexity resurfaced in the '{intent}' stack")

    def test_slow_tool_gets_extended_batch_deadline(self):
        self.assertGreater(rr._batch_timeout(["agy"]), rr.TOOL_CALL_TIMEOUT_S)

    def test_batch_deadline_is_max_not_sum(self):
        self.assertEqual(rr._batch_timeout(["agy", "tavily"]),
                         rr._batch_timeout(["agy"]))

    def test_fast_only_batch_keeps_default_deadline(self):
        self.assertEqual(rr._batch_timeout(["tavily", "jina"]), rr.TOOL_CALL_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main(verbosity=2)
