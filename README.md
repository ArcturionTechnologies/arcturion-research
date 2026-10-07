# ArcturionResearch

Multi-source research router: one query fans out to the right free-first sources and comes back merged.

Ask one question and ArcturionResearch decides what kind of question it is
(web, news, academic, books, places, images, video, social, code, factual or
deep), runs that intent's sources in cheapest-first order, stops once it has
enough good results, removes duplicates, and returns one ranked list plus a
per-source status report. It ships with 25 source adapters and separate image
and video search tools that rank results by relevance and license.

Python 3.10+. One dependency: `requests`.

> **Portfolio project.** This is an open-source sample of the tooling behind
> Arcturion's multi-agent setup. It is not a commercial product and makes no
> claims about revenue or customers.

## Quickstart

```bash
git clone https://github.com/ArcturionTechnologies/arcturion-research.git
cd arcturion-research
python3 -m pip install -r requirements.txt

# See the route plan without calling anything
python3 -m arcturion_research --explain "latest research on retrieval evaluation"

# Run it. Keyless sources work immediately; keyed ones are skipped until you set a key.
python3 -m arcturion_research "arxiv papers on retrieval evaluation"
python3 -m arcturion_research --intent places --json "lighthouses in Maine"

# Images and video, ranked by relevance and license
python3 -m arcturion_research.media.images "rosa rugosa flower" --limit 8
python3 -m arcturion_research.media.videos "rose pruning" --limit 5
```

As a library:

```python
from arcturion_research import research
result = research("retrieval evaluation", intent="academic", max_tokens=800)
for item in result["results"]:
    print(item["source"], item["title"], item["url"])
print(result["source_status"])   # used / empty / skipped / error, per source
```

## How a query runs

```
query ─► classify intent ─► that intent's stack, sorted by weight
                             │
                             ├─ batch 1 (highest weight) ─┐
                             ├─ batch 2 ... only if fewer than `min_results` (6) good results so far
                             └─ ...                        │
                                                           ▼
       cache hit? skip the call · rate budget full? skip · hung? abandon after the deadline
                                                           ▼
                   dedupe (URL, then title, then snippet hash) ─► top 30 ─► summary ─► result
```

- **Waterfall, not a blast.** Highest-weight tools run first, in batches of
  `--parallel` (3). The router escalates only while results are thin, so a good
  first source means the rest are never called.
- **Rate budgets.** Each metered source has a soft cap per rolling window
  (for example SerpAPI 3/hour, Wolfram 66/day). Check-and-charge is atomic across
  threads and processes, so parallel workers can't overshoot a cap.
- **Cache.** Clean, non-empty results are cached for 24 hours by
  `sha256(intent|tool|query)`. Errors, skips and empty results are never cached,
  so one bad minute doesn't hide a source for a day.
- **No hangs.** A batch has a wall-clock deadline (25 s, 90 s for the slower
  `agy` agent). Stragglers are abandoned, not waited on.
- **Honest failure.** If nothing usable comes back, the result is marked
  `degraded` with a reason, and the CLI exits `2`.
- **Pluggable models.** `set_classifier(fn)` and `set_summarizer(fn)` let you
  plug in any LLM. Without them, a keyword classifier picks the intent and the
  summary field says plainly that no summarizer is configured. It never passes
  the raw results off as a summary.

## Sources

| Intent | Stack (highest weight first) |
| --- | --- |
| web | tavily, jina, duckduckgo |
| news | newsdata, tavily, gdelt, hackernews |
| academic | openalex, arxiv, semantic_scholar, wikipedia |
| books | legal_books (Gutenberg, Open Library, Standard Ebooks, Internet Archive, CourtListener, SEC EDGAR, arXiv, IRS), archive_org |
| video | youtube_yt_dlp, serpapi_youtube |
| social | reddit, x_search, hackernews |
| image | wikimedia, unsplash, pexels, pixabay, met, nasa_images |
| places | osm_nominatim, wikipedia |
| factual | wolfram, agy, wikipedia |
| code | github_search, stackoverflow, hackernews |
| deep | agy, tavily, openalex |

The 25 adapters in `arcturion_research/sources/`: agy, archive_org, arxiv,
books, duckduckgo, hackernews, jina_reader, met, nasa_images, news (GDELT),
newsdata, openalex, osm_nominatim, perplexity, pexels, pixabay, reddit,
research_corpora (CourtListener, SEC EDGAR, arXiv, IRS publications),
semantic_scholar, serpapi, tavily, unsplash, wikimedia, wikipedia, wolfram.

Notes:
- `books` only uses public-domain and open-access sources. The module keeps an
  explicit list of piracy mirrors it must never use.
- `perplexity` works but is left out of every route on purpose: it bills per
  call. Add it to a stack yourself if you want it.
- `agy` drives the Antigravity CLI (Google Search grounding) when it's installed
  and signed in. It runs in a throwaway folder with no extra permissions and
  returns nothing if the CLI is missing.
- `youtube_yt_dlp` and `github_search` use the `yt-dlp` and `gh` CLIs when
  they're installed.

## Configuration

API keys come from environment variables only. The code never reads a password
manager, keychain or secrets file.

| Variable | Used by | Needed? |
| --- | --- | --- |
| `TAVILY_API_KEY` | tavily | for web/news/deep |
| `NEWSDATA_API_KEY` | newsdata | for news |
| `SERPAPI_API_KEY` (or `SERPAPI_KEY`) | serpapi_*, image/video search | optional |
| `WOLFRAM_APP_ID` | wolfram | optional |
| `UNSPLASH_ACCESS_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY` | image + video sources | optional |
| `OPENALEX_API_KEY`, `JINA_API_KEY` | openalex, jina | optional (higher limits) |
| `PERPLEXITY_API_KEY` | perplexity | optional, off by default |
| `SMITHSONIAN_API_KEY`, `EUROPEANA_API_KEY`, `DPLA_API_KEY` | image search | optional (demo keys used where allowed) |
| `ARC_RESEARCH_USER_AGENT` | every HTTP call | recommended: `your-app/1.0 (you@example.org)` |
| `ARC_RESEARCH_STATE_DIR` | cache + rate budgets | default `~/.cache/arcturion-research` |
| `SEARXNG_URL`, `AGY_BIN` | optional local tools | default `http://127.0.0.1:8888`, `agy` on PATH |

A keyed source with no key shows up as `skipped` in `source_status`, not as an
error. OpenStreetMap Nominatim and SEC EDGAR ask for an identifying User-Agent
with a contact address, so set `ARC_RESEARCH_USER_AGENT` before heavy use. The
default names this project and nothing else.

## Project layout

```
arcturion_research/
  router.py          intent routing, waterfall, budgets, cache, dedupe, CLI
  registry.py        one uniform callable per source
  http.py            User-Agent + env-var key helpers
  sources/           25 adapters
  media/images.py    14-database image search, ranked and license-tagged
  media/videos.py    7-database video search, ranked and license-tagged
tests/               81 tests, stdlib unittest, all HTTP stubbed
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Every HTTP call is replaced with canned responses, so the suite needs no keys
and no network.

## License

MIT. See [LICENSE](LICENSE). Each source's own terms of use still apply to the
data you fetch from it.

Implementation is AI-assisted; architecture, requirements, and testing directed by Robert Lingoes.
