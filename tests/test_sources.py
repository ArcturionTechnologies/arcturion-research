"""Adapter and registry tests against canned API responses. No network: every
HTTP call is replaced with a fake that returns a fixed payload."""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import requests  # noqa: E402

from arcturion_research import http, registry, router  # noqa: E402
from arcturion_research.sources import (archive_org, arxiv, books, duckduckgo, hackernews,  # noqa: E402
                                        jina_reader, met, nasa_images, news, newsdata, openalex,
                                        osm_nominatim, perplexity, pexels, pixabay, reddit,
                                        research_corpora, semantic_scholar, serpapi, tavily, unsplash,
                                        wikimedia, wikipedia, wolfram)

SOURCES_DIR = REPO / "arcturion_research" / "sources"
ALL_KEYS = ["TAVILY_API_KEY", "NEWSDATA_API_KEY", "OPENALEX_API_KEY", "JINA_API_KEY",
            "PERPLEXITY_API_KEY", "UNSPLASH_ACCESS_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY",
            "WOLFRAM_APP_ID", "SERPAPI_API_KEY", "SERPAPI_KEY"]


class FakeResponse:
    def __init__(self, payload=None, text="", status=200, content=b""):
        self._payload, self.text, self.status_code, self.content = payload, text, status, content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class Recorder:
    """Stands in for requests.get/post; routes by URL substring."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        for needle, resp in self.routes.items():
            if needle in url:
                return resp(url, kw) if callable(resp) else resp
        raise AssertionError(f"unexpected URL in test: {url}")


def fake_http(routes):
    rec = Recorder(routes)
    return rec, mock.patch.multiple(requests, get=rec, post=rec)


def no_keys():
    env = {k: v for k, v in os.environ.items() if k not in ALL_KEYS}
    return mock.patch.dict(os.environ, env, clear=True)


class HttpSettings(unittest.TestCase):
    def test_default_user_agent_has_no_personal_contact(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            ua = http.user_agent()
        self.assertTrue(ua.startswith("ArcturionResearch/"))
        self.assertNotRegex(ua, r"[\w.+-]+@[\w-]+\.\w+")

    def test_user_agent_override(self):
        with mock.patch.dict(os.environ, {"ARC_RESEARCH_USER_AGENT": "me/1.0"}):
            self.assertEqual(http.headers()["User-Agent"], "me/1.0")

    def test_require_key_raises_missing_key(self):
        with no_keys():
            with self.assertRaises(http.MissingKey):
                http.require_key("TAVILY_API_KEY")

    def test_no_adapter_reads_a_credential_store_or_hardcodes_contact(self):
        bad = re.compile(r"get_secret|op_client|\bop read\b|1Password|keychain|/Users/|@gmail|tokens\.env", re.I)
        for f in sorted(SOURCES_DIR.glob("*.py")) + [REPO / "arcturion_research" / "registry.py"]:
            self.assertIsNone(bad.search(f.read_text()), f.name)


class Adapters(unittest.TestCase):
    def test_archive_org(self):
        rec, patch = fake_http({"archive.org/advancedsearch": FakeResponse(
            {"response": {"docs": [{"identifier": "abc", "title": "T", "description": ["a", "b"]}]}})})
        with patch:
            out = archive_org.search("q", num=3, mediatype="texts")
        self.assertEqual(out[0]["url"], "https://archive.org/details/abc")
        self.assertEqual(out[0]["snippet"], "a b")
        self.assertIn("mediatype:texts", rec.calls[0][1]["params"]["q"])

    def test_duckduckgo(self):
        _, patch = fake_http({"duckduckgo": FakeResponse({
            "AbstractText": "An abstract", "Heading": "H", "AbstractURL": "https://x",
            "RelatedTopics": [{"Text": "Rel", "FirstURL": "https://r"},
                              {"Topics": [{"Text": "Nested", "FirstURL": "https://n"}]}]})})
        with patch:
            out = duckduckgo.search("q")
        self.assertEqual([o["url"] for o in out], ["https://x", "https://r", "https://n"])

    def test_hackernews_and_fail_soft(self):
        _, patch = fake_http({"hn.algolia": FakeResponse({"hits": [{"title": "S", "objectID": "9"}]})})
        with patch:
            out = hackernews.search("q")
        self.assertEqual(out[0]["url"], "https://news.ycombinator.com/item?id=9")
        _, patch = fake_http({"hn.algolia": FakeResponse(status=500)})
        with patch:
            self.assertEqual(hackernews.search("q"), [])

    def test_arxiv_via_corpora(self):
        atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<id>http://arxiv.org/abs/2106.09685v2</id><title>LoRA</title>'
                b'<summary>Low-rank adaptation</summary><published>2021-06-17T00:00:00Z</published>'
                b'<author><name>A</name></author></entry></feed>')
        _, patch = fake_http({"export.arxiv.org": FakeResponse(content=atom)})
        with patch:
            out = arxiv.search("lora", limit=1)
        self.assertEqual(out[0]["url"], "https://arxiv.org/abs/2106.09685")
        self.assertEqual(out[0]["published"], "2021")

    def test_met_skips_imageless_objects(self):
        objs = {1: {"primaryImage": "", "title": "no image"},
                2: {"primaryImage": "https://img/2.jpg", "title": "Vase", "isPublicDomain": True}}
        _, patch = fake_http({
            "/search": FakeResponse({"objectIDs": [1, 2]}),
            "/objects/": lambda url, kw: FakeResponse(objs[int(url.rsplit("/", 1)[1])]),
        })
        with patch:
            out = met.search_images("vase", num=2)
        self.assertEqual([o["title"] for o in out], ["Vase"])
        self.assertIn("Public Domain", out[0]["license"])

    def test_nasa_images_and_videos(self):
        payload = {"collection": {"items": [{"data": [{"title": "Moon", "nasa_id": "m1"}],
                                             "links": [{"href": "https://thumb"}]}]}}
        _, patch = fake_http({"images-api.nasa.gov": FakeResponse(payload)})
        with patch:
            img = nasa_images.search_images("moon")
            vid = nasa_images.search_videos("moon")
        self.assertEqual(img[0]["url"], "https://thumb")
        self.assertEqual(vid[0]["url"], "https://images.nasa.gov/details/m1")

    def test_gdelt_news(self):
        _, patch = fake_http({"gdeltproject": FakeResponse({"articles": [
            {"title": "N", "url": "https://n", "seendate": "20260101", "domain": "ex.org"}]})})
        with patch:
            out = news.search("q")
        self.assertEqual(out[0]["snippet"], "20260101 ex.org")

    def test_keyed_adapters_send_env_key(self):
        env = {"NEWSDATA_API_KEY": "nd", "OPENALEX_API_KEY": "oa", "UNSPLASH_ACCESS_KEY": "us",
               "PEXELS_API_KEY": "px", "PIXABAY_API_KEY": "pb", "WOLFRAM_APP_ID": "wa",
               "TAVILY_API_KEY": "tv", "SERPAPI_API_KEY": "sp", "JINA_API_KEY": "jn"}
        rec, patch = fake_http({
            "newsdata.io": FakeResponse({"results": [{"title": "N", "link": "https://n"}]}),
            "openalex.org": FakeResponse({"results": [{"title": "P", "doi": "https://doi"}]}),
            "unsplash.com": FakeResponse({"results": [{"description": "U", "urls": {"regular": "https://u"}}]}),
            "pexels.com": FakeResponse({"photos": [{"alt": "P", "src": {"large": "https://p"}}]}),
            "pixabay.com": FakeResponse({"hits": [{"tags": "B", "largeImageURL": "https://b"}]}),
            "wolframalpha.com": FakeResponse({"queryresult": {"pods": [
                {"title": "Result", "subpods": [{"plaintext": "42"}]}]}}),
            "tavily.com": FakeResponse({"results": [{"title": "T", "url": "https://t", "content": "c"}],
                                        "answer": "ans"}),
            "serpapi.com": FakeResponse({"organic_results": [{"title": "S", "link": "https://s"}]}),
            "s.jina.ai": FakeResponse({"data": [{"title": "J", "url": "https://j", "content": "c"}]}),
        })
        with mock.patch.dict(os.environ, env), patch:
            self.assertEqual(newsdata.search("q")[0]["url"], "https://n")
            self.assertEqual(openalex.search("q")[0]["url"], "https://doi")
            self.assertEqual(unsplash.search("q")[0]["url"], "https://u")
            self.assertEqual(pexels.search_images("q")[0]["url"], "https://p")
            self.assertEqual(pixabay.search_images("q")[0]["url"], "https://b")
            self.assertEqual(wolfram.query("q")[0]["snippet"], "42")
            self.assertEqual(tavily.normalize(tavily.search("q"))[0]["url"], "https://t")
            self.assertEqual(serpapi.normalize(serpapi.google("q"))[0]["url"], "https://s")
            self.assertEqual(jina_reader.search("q")[0]["url"], "https://j")
        sent = json.dumps([c[1] for c in rec.calls], default=str)
        for key in ("nd", "oa", "Client-ID us", "px", "pb", "wa", "tv", "sp", "Bearer jn"):
            self.assertIn(key, sent)

    def test_keyed_adapters_without_keys_raise_missing_key(self):
        with no_keys():
            for fn in (lambda: newsdata.search("q"), lambda: unsplash.search("q"),
                       lambda: pexels.search_images("q"), lambda: pixabay.search_images("q"),
                       lambda: wolfram.query("q"), lambda: tavily.search("q"),
                       lambda: serpapi.google("q")):
                with self.assertRaises(http.MissingKey):
                    fn()

    def test_optional_keys_are_optional(self):
        rec, patch = fake_http({"openalex.org": FakeResponse({"results": []}),
                                "s.jina.ai": FakeResponse({"data": []})})
        with no_keys(), patch:
            self.assertEqual(openalex.search("q"), [])
            self.assertEqual(jina_reader.search("q"), [])
        self.assertNotIn("api_key", rec.calls[0][1]["params"])
        self.assertNotIn("Authorization", rec.calls[1][1]["headers"])

    def test_osm_nominatim_rate_limit_and_ua(self):
        rec, patch = fake_http({"nominatim": FakeResponse([
            {"display_name": "Paris, France", "lat": "1", "lon": "2", "osm_type": "node", "osm_id": 7,
             "address": {"country": "France", "city": "Paris"}}])})
        with patch, mock.patch.object(osm_nominatim.time, "sleep") as slept:
            out = osm_nominatim.search("paris")
        slept.assert_called_once_with(1.0)
        self.assertEqual(out[0]["title"], "Paris")
        self.assertTrue(rec.calls[0][1]["headers"]["User-Agent"])

    def test_perplexity_reports_missing_key_as_error_item(self):
        with no_keys():
            out = perplexity.search("q")
        self.assertEqual(out[0]["type"], "error")
        self.assertIn("PERPLEXITY_API_KEY", out[0]["snippet"])

    def test_reddit(self):
        _, patch = fake_http({"reddit.com": FakeResponse({"data": {"children": [
            {"data": {"title": "R", "permalink": "/r/x/1", "subreddit": "x"}}]}})})
        with patch:
            out = reddit.search("q")
        self.assertEqual(out[0]["url"], "https://www.reddit.com/r/x/1")

    def test_semantic_scholar_retries_once_on_429(self):
        seq = [FakeResponse(status=429), FakeResponse({"data": [{"title": "Paper", "url": "https://s2"}]})]
        _, patch = fake_http({"semanticscholar": lambda url, kw: seq.pop(0)})
        with patch, mock.patch.object(semantic_scholar.time, "sleep"):
            out = semantic_scholar.search("q")
        self.assertEqual(out[0]["url"], "https://s2")

    def test_wikimedia_and_wikipedia(self):
        _, patch = fake_http({
            "commons.wikimedia.org": FakeResponse({"query": {"pages": {"1": {
                "title": "File:Rose.jpg", "imageinfo": [{"url": "https://w/rose.jpg",
                                                         "extmetadata": {"Artist": {"value": "<b>Ann</b>"}}}]}}}}),
            "rest_v1/page/summary": FakeResponse({"extract": "A rose.", "content_urls": {
                "desktop": {"page": "https://en.wikipedia.org/wiki/Rose"}}}),
            "w/api.php": FakeResponse({"query": {"search": [{"title": "Rose"}]}}),
        })
        with patch:
            img = wikimedia.search_images("rose")
            wp = wikipedia.search("rose")
        self.assertEqual((img[0]["title"], img[0]["author"]), ("Rose.jpg", "Ann"))
        self.assertEqual(wp[0]["url"], "https://en.wikipedia.org/wiki/Rose")

    def test_books_dedupes_and_never_lists_piracy_sources(self):
        for name in books._SOURCES:
            self.assertFalse(any(bad in name for bad in books._FORBIDDEN_SOURCES), name)
        merged = books._dedupe([
            {"title": "Meditations", "author": "Marcus Aurelius", "formats": ["txt"]},
            {"title": "meditations", "author": "marcus aurelius", "formats": ["txt", "epub"]},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["formats"], ["txt", "epub"])

    def test_irs_index_is_offline(self):
        out = research_corpora.irs("home office deduction", 2)
        self.assertTrue(out)
        self.assertTrue(all(o["url"].startswith("https://www.irs.gov/") for o in out))


class Registry(unittest.TestCase):
    def test_every_route_tool_is_registered(self):
        for intent, stack in router.ROUTES.items():
            for tool, _n, _w in stack:
                self.assertIn(tool, registry.TOOLS, f"{intent}: {tool}")

    def test_intents_have_routes(self):
        self.assertEqual(set(router.INTENTS), set(router.ROUTES))

    def test_missing_key_becomes_skipped_not_error(self):
        with no_keys():
            for name in ("tavily", "newsdata", "unsplash", "pexels", "pixabay", "wolfram",
                         "serpapi_google", "serpapi_youtube"):
                out = registry.TOOLS[name]("q", 3)
                self.assertEqual(len(out), 1, name)
                self.assertIn("skipped", out[0], name)

    def test_image_tools_call_the_image_functions(self):
        # Regression: image adapters expose search_images(), not search().
        with mock.patch.object(registry._wikimedia, "search_images",
                               return_value=[{"title": "W", "url": "https://w", "snippet": ""}]):
            self.assertEqual(registry.TOOLS["wikimedia"]("q", 1)[0]["url"], "https://w")

    def test_norm_shapes(self):
        out = registry._norm({"results": [{"name": "N", "link": "https://l", "description": "d"}, "junk"]}, "s")
        self.assertEqual(out, [{"title": "N", "url": "https://l", "snippet": "d", "source": "s"}])

    def test_router_end_to_end_with_fake_tools(self):
        fake = {"one": lambda q, max_results=5: [{"title": "A", "url": "https://a", "snippet": "s", "source": "one"},
                                                 {"title": "A again", "url": "https://a/", "snippet": "s", "source": "one"}]}
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(registry.TOOLS, fake, clear=True), \
                mock.patch.object(router, "ROUTES", {"web": [("one", 5, 1.0)]}), \
                mock.patch.object(router, "RATE_BUDGETS", {}), \
                mock.patch.object(router, "CACHE_DIR", Path(tmp) / "cache"), \
                mock.patch.object(router, "BUDGET_FILE", Path(tmp) / "budget.json"):
            res = router.research("q", intent="web", min_results=1)
        self.assertEqual(len(res["results"]), 1)  # trailing-slash duplicate merged
        self.assertEqual(res["sources_used"], ["one"])
        self.assertFalse(res["degraded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
