"""Image and video search: ranking, dedupe, env-only keys. No network: each
test replaces the module's HTTP helper with canned data."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from arcturion_research.media import images, videos  # noqa: E402

KEYS = ["PEXELS_API_KEY", "PIXABAY_API_KEY", "UNSPLASH_ACCESS_KEY", "SERPAPI_API_KEY",
        "SERPAPI_KEY", "DPLA_API_KEY", "SMITHSONIAN_API_KEY", "DATA_GOV_API_KEY", "EUROPEANA_API_KEY"]


def no_keys():
    return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in KEYS}, clear=True)


class ImageHelpers(unittest.TestCase):
    def test_strip_html_and_license_labels(self):
        self.assertEqual(images._strip_html("<a>Jane &amp; Co</a>"), "Jane & Co")
        self.assertEqual(images._fmt_cc("cc0"), "CC0 (public domain)")
        self.assertEqual(images._fmt_cc("by-sa", "4.0"), "CC BY-SA 4.0")
        self.assertEqual(images._rights_label("https://creativecommons.org/licenses/by-nc/4.0/"), "CC BY-NC")
        self.assertEqual(images._rights_label("https://creativecommons.org/publicdomain/zero/1.0/"),
                         "CC0 (public domain)")

    def test_ranker_prefers_on_topic_clean_images(self):
        q = images._toks("knock out rose")
        good = images._mk("Knock Out rose shrub", "http://x/a.jpg", "wikimedia",
                          license="CC BY-SA 4.0", width=4000, height=3000)
        bad = images._mk("rose leaf with sawfly damage", "http://x/b.jpg", "openverse:flickr",
                         license="CC BY-NC 2.0", width=500, height=400)
        self.assertGreater(images._score(good, q), images._score(bad, q))
        self.assertTrue(images._score(bad, q) < 0 or images._score(good, q) - images._score(bad, q) >= 6)

    def test_no_local_or_personal_library_sources(self):
        self.assertTrue(images.KEYLESS <= set(images.SOURCES))
        for gone in ("local", "eagle"):
            self.assertNotIn(gone, images.SOURCES)
            self.assertNotIn(gone, images.EXTENDED)
        self.assertFalse(hasattr(images, "build_local_index"))
        self.assertNotIn("local", images._mk("t", "u", "s"))

    def test_keyed_sources_skip_without_env_keys(self):
        with no_keys(), mock.patch.object(images, "_get", side_effect=AssertionError("no HTTP expected")):
            for name in ("pexels", "pixabay", "unsplash", "serpapi", "dpla"):
                self.assertEqual(images.SOURCES[name]("rose", 3, "any"), [], name)

    def test_search_dedupes_on_image_url_and_reports_errors(self):
        def fake_get(url, headers=None, timeout=None):
            if "openverse" in url:
                return {"results": [
                    {"title": "Rose A", "url": "https://img/rose.jpg?x=1", "license": "cc0"},
                    {"title": "Rose A copy", "url": "https://img/rose.jpg?x=2", "license": "cc0"},
                    {"title": "Rose B", "url": "https://img/rose-b.jpg", "license": "by"}]}
            raise OSError("down")
        with mock.patch.object(images, "_get", fake_get):
            res = images.search("rose", sources=["openverse", "wikimedia"])
        self.assertEqual(res["count"], 2)
        self.assertIn("wikimedia", res["errors"])
        self.assertTrue(res["ranked"])

    def test_env_key_is_sent(self):
        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen["headers"] = headers
            return {"photos": [{"alt": "rose", "src": {"large": "https://p/l.jpg", "medium": "https://p/m.jpg"}}]}
        with mock.patch.dict(os.environ, {"PEXELS_API_KEY": "test-key"}), mock.patch.object(images, "_get", fake_get):
            out = images.src_pexels("rose", 1, "any")
        self.assertEqual(seen["headers"], {"Authorization": "test-key"})
        self.assertEqual(out[0]["image_url"], "https://p/l.jpg")


class VideoHelpers(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(videos._strip_html("<i>x &amp; y</i>"), "x & y")
        self.assertEqual(videos._fmt_secs(75), "1:15")
        self.assertEqual(videos._toks("Pruning Roses 2024"), {"pruning", "roses", "2024"})

    def test_ranker_prefers_clean_on_topic(self):
        q = videos._toks("rose pruning")
        good = videos._mk("How to prune roses (CC)", "http://w/a", "peertube", license="CC BY 4.0", duration="5:00")
        bad = videos._mk("rose pruning PRANK reaction compilation", "http://w/b", "searxng:youtube",
                         license="via SearXNG — verify rights")
        self.assertGreater(videos._score(good, q), videos._score(bad, q))
        self.assertTrue(videos.KEYLESS <= set(videos.SOURCES))

    def test_keyed_sources_skip_without_env_keys(self):
        with no_keys(), mock.patch.object(videos, "_get", side_effect=AssertionError("no HTTP expected")):
            for name in ("pexels", "pixabay", "serpapi"):
                self.assertEqual(videos.SOURCES[name]("rose", 3, "any"), [], name)

    def test_peertube_parse_and_watch_url_dedupe(self):
        payload = {"data": [
            {"name": "Rose pruning", "uuid": "u1", "channel": {"host": "tube.example", "displayName": "Ch"},
             "thumbnailPath": "/t.jpg", "duration": 90},
            {"name": "Rose pruning (dup)", "url": "https://tube.example/w/u1"}]}
        with mock.patch.object(videos, "_get", lambda url, headers=None, timeout=None: payload):
            res = videos.search("rose pruning", sources=["peertube"])
        self.assertEqual(res["count"], 1)
        r = res["results"][0]
        self.assertEqual((r["watch_url"], r["thumbnail_url"], r["duration"]),
                         ("https://tube.example/w/u1", "https://tube.example/t.jpg", "1:30"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
