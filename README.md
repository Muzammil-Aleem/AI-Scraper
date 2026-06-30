# The Dispatch — Web Scraper + AI Data Extractor

A scraper that pulls paginated listings from a live website, hands the
raw data to Claude for cleaning/categorization/summarization, and
displays the result in a wire-service-styled live dashboard — with
one-click CSV/JSON export.

```
scraper-ai-project/
├── backend/
│   ├── main.py            FastAPI server (routes, job orchestration, serves the UI)
│   ├── scraper.py          Pagination, retries, user-agent rotation, polite delays
│   ├── ai_processor.py     Claude-based cleaning/categorization/summarization (+ offline fallback)
│   └── requirements.txt
└── frontend/
    └── index.html          The dashboard (vanilla HTML/CSS/JS, no build step)
```

## What it does

1. **Scrape** — crawls a target listing page (any URL you paste in —
   see sample URLs below) page by page. Handles pagination
   automatically, rotates user agents, retries with exponential backoff
   on rate limiting/server errors, and stops gracefully instead of
   crashing if a page is unreachable.
2. **AI clean + categorize + summarize** — sends the raw items to Claude
   in small batches, which fixes messy titles, assigns a genre/category,
   and writes a one-sentence summary for each item. If no API key is
   set, an offline heuristic step does the same job so the whole
   pipeline still runs with zero configuration.
3. **Dashboard** — a live "wire feed" shows the crawl happening in real
   time; results land as cards you can filter by category or search by
   title.
4. **Export** — download everything as CSV or JSON with one click.

## Setup (run on your own PC)

**Requirements:** Python 3.10+

```bash
cd scraper-ai-project/backend
pip install -r requirements.txt
```

> **Windows / PowerShell note:** if `pip install` fails trying to
> compile `pydantic-core` from source (a Rust/MSVC linker error), it
> usually means your Python version is newer than the pinned package
> versions have prebuilt wheels for. The `requirements.txt` in this
> repo already uses loose `>=` version constraints to avoid that, but
> if you still hit it, either update Python to 3.12/3.13 or install
> "Build Tools for Visual Studio" with the "Desktop development with
> C++" workload.

### (Optional, recommended) Enable real AI processing

Without an API key the app still works end-to-end using a built-in
fallback categorizer. To use actual Claude-powered cleaning and
summaries, get an API key from https://console.anthropic.com and set
it as an environment variable before starting the server:

```bash
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-...your-key..."

# Windows PowerShell (current session only — set again each time you open a new terminal)
$env:ANTHROPIC_API_KEY="sk-ant-...your-key..."

# Windows PowerShell (persists across sessions — close and reopen PowerShell after running this)
setx ANTHROPIC_API_KEY "sk-ant-...your-key..."
```

### Start the server

```bash
# macOS / Linux / WSL
uvicorn main:app --reload --port 8000

# Windows PowerShell — if `uvicorn` isn't on PATH, run it as a module instead:
python -m uvicorn main:app --reload --port 8000
# (use `py -m uvicorn ...` instead of `python -m uvicorn ...` if `python` isn't recognized either)
```

Then open **http://127.0.0.1:8000** in your browser. The FastAPI app
serves the dashboard directly, so there's nothing else to start.

## Using it

1. Paste a target URL into the **Target URL** field (or use one of the
   sample URLs below), set how many pages to crawl (default 4), and
   click **Run Crawl**.
2. Watch the live wire feed on the left — it shows each page being
   fetched and how many items were found.
3. Once scraping finishes, the AI step kicks off automatically
   ("Synthesizing x/y" in the status box).
4. Browse results as cards, filter by category, or search by title.
5. Click **CSV** or **JSON** at any time to download everything
   collected so far.

### Sample URLs to try

These are all server-rendered (no JavaScript required) and either
built for scraping practice or commonly used for it, so none of them
will block the crawler:

| URL | Mode used | What you'll see |
|---|---|---|
| `https://books.toscrape.com/` | Specialized parser | Book titles, prices, star ratings, in-stock status |
| `https://quotes.toscrape.com/` | Generic auto-detect | Quotes with authors/tags — good test of the generic extractor |
| `https://webscraper.io/test-sites/e-commerce/allinone` | Generic auto-detect | Fake e-commerce product listing with prices |

`books.toscrape.com` uses the hand-tuned parser built into the app;
the other two are picked up automatically by the generic card-detection
logic, which is a good way to see that path in action.

## Pointing it at a different site

You don't need to edit any code for most listing-style pages — just
paste the URL into the dashboard's Target URL field. The app will:

- Use the specialized `books.toscrape.com` parser only if that's the
  domain you entered.
- Otherwise fall back to **generic mode**, which auto-detects repeated
  "card" elements on the page (same tag + class appearing 3+ times with
  a heading/link) and pulls out title, price, link, image, and a text
  snippet from each, then follows whatever "Next" link it can find.

Generic mode works well on simple, server-rendered listing pages. It
won't see anything on sites that render their content with JavaScript
(this scraper uses plain HTTP requests, not a real browser), and it
will get blocked by sites with serious anti-bot systems regardless of
the politeness measures below (Cloudflare challenges, LinkedIn, Amazon,
etc.). For those, you'd need a headless-browser-based fetch step
(e.g. Playwright) in place of `requests` — worth a follow-up if you hit
that wall.

If you want to hand-tune extraction for a specific site instead of
relying on auto-detection, open `backend/scraper.py` and add a new
specialized parser function alongside `parse_page_books()`, following
the same pattern (CSS selectors → list of item dicts with
`title`/`price`/`availability`/`url`/`image` keys).

Everything else (AI processing, dashboard, exports) works unchanged
since it all operates on that same generic dict structure.

## Anti-bot / politeness measures already built in

- Rotates between several realistic browser User-Agent strings per request
- Randomized delay between page fetches (0.6–1.4s)
- Retries with exponential backoff + jitter on `429`/`503` responses
- Treats unreachable pages as a graceful stop, not a crash, so partial
  results are never lost
- Caps total pages per run (set in the dashboard) to avoid runaway crawls

## Notes

- All scraped + processed data lives in memory for the running server
  process — restart the server to clear it, or just run a new crawl.
- For production use beyond a local demo, you'd want to add persistent
  storage (SQLite/Postgres), authentication on the export routes, and
  respect for `robots.txt` on whatever site you point it at.
