"""
ai_processor.py
----------------
Takes raw scraped items and uses Claude (Anthropic API) to clean,
categorize, and summarize them in batches.

Works with zero configuration: if no ANTHROPIC_API_KEY is set, it falls
back to a lightweight heuristic processor so the whole pipeline still
runs end-to-end for demo purposes. Set ANTHROPIC_API_KEY to unlock real
AI categorization + summaries.
"""

import os
import re
import json
import time
from typing import List, Dict

MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 10

try:
    import anthropic
    _client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
except ImportError:
    anthropic = None
    _client = None


def _heuristic_category(title: str) -> str:
    t = title.lower()
    rules = [
        (r"\b(love|romance|heart|wedding)\b", "Romance"),
        (r"\b(murder|crime|detective|mystery|thriller)\b", "Mystery & Thriller"),
        (r"\b(magic|dragon|witch|fantasy|kingdom)\b", "Fantasy"),
        (r"\b(war|history|historical|empire)\b", "History"),
        (r"\b(science|space|physics|biology)\b", "Science"),
        (r"\b(business|money|finance|economics)\b", "Business"),
        (r"\b(child|kids|young)\b", "Children's"),
        (r"\b(cook|food|recipe)\b", "Food & Cooking"),
        (r"\b(poem|poetry)\b", "Poetry"),
    ]
    for pattern, label in rules:
        if re.search(pattern, t):
            return label
    return "General Fiction"


def _heuristic_process(items: List[Dict]) -> List[Dict]:
    out = []
    for it in items:
        clean_title = re.sub(r"\s+", " ", it["title"]).strip()
        out.append({
            **it,
            "title": clean_title,
            "category": _heuristic_category(clean_title),
            "summary": f"{clean_title} — priced at {it.get('price', 'N/A')}, "
                       f"currently listed as {it.get('availability', 'unknown availability')}.",
            "ai_engine": "heuristic-fallback",
        })
    return out


def _ai_process_batch(items: List[Dict]) -> List[Dict]:
    """Send one batch to Claude asking for strict JSON back: cleaned title,
    category, and a one-sentence summary per item."""
    payload = [
        {"id": i, "title": it["title"], "price": it.get("price", ""),
         "availability": it.get("availability", ""), "rating_word": it.get("rating_word", "")}
        for i, it in enumerate(items)
    ]

    prompt = (
        "You are a data-cleaning assistant. For each item in this JSON array, "
        "produce a cleaned-up title (fix whitespace/casing issues), assign ONE "
        "concise category (e.g. Fantasy, Mystery & Thriller, Romance, History, "
        "Science, Business, Children's, Food & Cooking, Poetry, General Fiction, "
        "or another short genre label if clearly more accurate), and write a "
        "one-sentence, plain-English summary/listing blurb using the price and "
        "availability fields.\n\n"
        f"Items:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        "Respond with ONLY a JSON array, same length and order, of objects like:\n"
        '[{"id": 0, "clean_title": "...", "category": "...", "summary": "..."}]\n'
        "No prose, no markdown fences, just the JSON array."
    )

    resp = _client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text"))
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:].strip()

    parsed = json.loads(text)
    by_id = {p["id"]: p for p in parsed}

    out = []
    for i, it in enumerate(items):
        p = by_id.get(i, {})
        out.append({
            **it,
            "title": p.get("clean_title", it["title"]),
            "category": p.get("category", "Uncategorized"),
            "summary": p.get("summary", ""),
            "ai_engine": MODEL,
        })
    return out


def process_items(items: List[Dict], on_progress=None) -> List[Dict]:
    """Clean/categorize/summarize all items, batching requests to Claude.
    Falls back to heuristics per-batch if the API errors out, so a flaky
    network or missing key never kills the whole run."""
    if not items:
        return []

    if _client is None:
        result = _heuristic_process(items)
        if on_progress:
            on_progress(len(result), len(items))
        return result

    results: List[Dict] = []
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        try:
            processed = _ai_process_batch(batch)
        except Exception:
            processed = _heuristic_process(batch)
        results.extend(processed)
        if on_progress:
            on_progress(len(results), len(items))
        time.sleep(0.2)  # gentle pacing between batches

    return results
