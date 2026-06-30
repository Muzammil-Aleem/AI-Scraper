"""
scraper.py
----------
Handles the actual web scraping: pagination, polite delays, retries,
user-agent rotation, and graceful failure handling (anti-bot measures).

Two extraction modes:
  - "specialized": hand-tuned CSS selectors for books.toscrape.com
    (the default demo target — a site built for scraping practice).
  - "generic": works on (most) other listing-style pages by detecting
    repeated card-like structures on the page automatically, no
    site-specific selectors required. Used whenever the user enters a
    custom URL.

Pagination in generic mode is discovered live by looking for a "next
page" link on each page (rel=next, common next-link text, or common
?page=N query patterns), rather than assuming a fixed URL pattern.
"""

import re
import time
import random
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import Callable, Optional

DEFAULT_URL = "https://books.toscrape.com/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

PRICE_RE = re.compile(r"[$£€]\s?\d[\d,]*\.?\d*|\d[\d,]*\.?\d*\s?(USD|EUR|GBP)")


def _headers(referer: str):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": referer,
    }


def _fetch(url: str, referer: str, max_retries: int = 4) -> Optional[requests.Response]:
    """Fetch a URL with retry + exponential backoff + jitter, handling
    rate limiting (429) and transient server errors gracefully."""
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=_headers(referer), timeout=12)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                time.sleep(delay + random.uniform(0.5, 1.5))
                delay *= 2
                continue
            if resp.status_code == 404:
                return None
            time.sleep(delay)
            delay *= 1.7
        except requests.RequestException:
            time.sleep(delay)
            delay *= 1.7
    return None


# ---------------------------------------------------------------- specialized

def parse_page_books(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("article.product_pod"):
        title_tag = card.select_one("h3 a")
        title = title_tag.get("title", "").strip() if title_tag else "Untitled"
        price_tag = card.select_one(".price_color")
        price = price_tag.text.strip() if price_tag else ""
        avail_tag = card.select_one(".availability")
        availability = avail_tag.text.strip() if avail_tag else ""
        rating_tag = card.select_one("p.star-rating")
        rating_word = rating_tag.get("class", ["", ""])[1] if rating_tag else "Zero"
        link = title_tag.get("href", "") if title_tag else ""
        img_tag = card.select_one("img")
        img = img_tag.get("src", "") if img_tag else ""
        items.append({
            "title": title,
            "price": price,
            "availability": availability,
            "rating_word": rating_word,
            "url": urljoin(base_url, link) if link else "",
            "image": urljoin(base_url, img) if img else "",
        })
    return items


def has_next_books(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one("li.next a") is not None


# -------------------------------------------------------------------- generic

CARD_TAGS = ("article", "li", "div", "section")
MIN_GROUP_SIZE = 3


def _signature(tag):
    classes = tag.get("class") or []
    return tag.name + "." + ".".join(sorted(classes)) if classes else None


def _find_repeated_groups(soup):
    """Find the most likely 'listing card' group: elements sharing the same
    tag+class signature that appear 3+ times, scored by how much they look
    like real content cards (has a heading/link + reasonable text length)."""
    counts = {}
    for tag in soup.find_all(CARD_TAGS):
        sig = _signature(tag)
        if not sig:
            continue
        counts.setdefault(sig, []).append(tag)

    candidates = [(sig, els) for sig, els in counts.items() if len(els) >= MIN_GROUP_SIZE]
    if not candidates:
        return []

    def score(pair):
        sig, els = pair
        sample = els[: min(len(els), 8)]
        pts = 0
        for el in sample:
            if el.find(["h1", "h2", "h3", "h4", "a"]):
                pts += 1
            text_len = len(el.get_text(strip=True))
            if 15 <= text_len <= 600:
                pts += 1
        return pts / max(len(sample), 1), len(els)

    candidates.sort(key=score, reverse=True)
    return candidates[0][1]


def generic_extract_items(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    groups = _find_repeated_groups(soup)
    items = []

    for el in groups:
        heading = el.find(["h1", "h2", "h3", "h4"]) or el.find("a")
        title = heading.get_text(strip=True) if heading else None
        if not title:
            continue

        link_tag = el.find("a", href=True)
        link = urljoin(base_url, link_tag["href"]) if link_tag else ""

        img_tag = el.find("img")
        img_src = (img_tag.get("src") or img_tag.get("data-src")) if img_tag else None
        img = urljoin(base_url, img_src) if img_src else ""

        full_text = el.get_text(" ", strip=True)
        price_match = PRICE_RE.search(full_text)
        price = price_match.group(0) if price_match else ""

        snippet = full_text.replace(title, "", 1).strip()
        snippet = re.sub(r"\s+", " ", snippet)[:240]

        items.append({
            "title": title[:200],
            "price": price,
            "availability": snippet,   # repurposed as "extra detail" for generic sites
            "rating_word": "",
            "url": link,
            "image": img,
        })

    # de-duplicate by title, keep order
    seen = set()
    deduped = []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        deduped.append(it)
    return deduped


NEXT_TEXT_PATTERNS = re.compile(r"^\s*(next|next page|older|more|»|>)\s*$", re.I)


def find_next_link(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    rel_next = soup.find("a", rel=lambda v: v and "next" in v)
    if rel_next and rel_next.get("href"):
        return urljoin(base_url, rel_next["href"])

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text and NEXT_TEXT_PATTERNS.match(text):
            return urljoin(base_url, a["href"])

    return None


# ----------------------------------------------------------------------- job

@dataclass
class ScrapeJob:
    """Mutable state object so the API layer can report live progress."""
    max_pages: int = 5
    target_url: str = DEFAULT_URL
    delay_range: tuple = (0.6, 1.4)
    status: str = "idle"          # idle | running | done | error
    current_page: int = 0
    total_items: int = 0
    log: list = field(default_factory=list)
    raw_items: list = field(default_factory=list)
    error: Optional[str] = None

    def emit(self, msg: str):
        self.log.append(msg)
        if len(self.log) > 200:
            self.log.pop(0)


def _is_books_site(url: str) -> bool:
    return "books.toscrape.com" in urlparse(url).netloc


def run_scrape(job: ScrapeJob, on_update: Optional[Callable[[], None]] = None):
    """Crawl paginated listing pages until max_pages reached or no next page.
    Designed to run inside a background thread; mutates `job` in place so
    the web layer can poll progress."""
    job.status = "running"
    target = (job.target_url or DEFAULT_URL).strip()
    if not target.startswith("http"):
        target = "https://" + target

    specialized = _is_books_site(target)
    mode = "specialized (books.toscrape.com)" if specialized else "generic auto-detect"
    job.emit(f"Starting crawl of {target}  [mode: {mode}]")

    if specialized:
        url = urljoin(target, "catalogue/page-1.html")
    else:
        url = target

    page_num = 1

    try:
        while url and page_num <= job.max_pages:
            job.emit(f"Fetching page {page_num}: {url}")
            resp = _fetch(url, referer=target)
            if resp is None:
                job.emit(f"Page {page_num} unreachable after retries — stopping crawl gracefully.")
                break

            if specialized:
                items = parse_page_books(resp.text, target)
            else:
                items = generic_extract_items(resp.text, url)
                if not items:
                    job.emit("No repeated card-like structures detected on this page.")

            job.raw_items.extend(items)
            job.total_items = len(job.raw_items)
            job.current_page = page_num
            job.emit(f"Page {page_num}: extracted {len(items)} items (total {job.total_items}).")
            if on_update:
                on_update()

            if page_num >= job.max_pages:
                job.emit("Page limit reached. Crawl complete.")
                break

            if specialized:
                has_more = has_next_books(resp.text)
                next_url = urljoin(target, f"catalogue/page-{page_num + 1}.html") if has_more else None
            else:
                next_url = find_next_link(resp.text, url)
                if next_url == url:
                    next_url = None

            if not next_url:
                job.emit("No further pages found. Crawl complete.")
                break

            time.sleep(random.uniform(*job.delay_range))
            page_num += 1
            url = next_url

        job.status = "done"
        job.emit(f"Done. {job.total_items} raw items collected across {job.current_page} page(s).")
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = str(e)
        job.emit(f"Crawl failed: {e}")
    finally:
        if on_update:
            on_update()
