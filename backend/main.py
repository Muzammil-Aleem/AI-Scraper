"""
main.py
-------
FastAPI server that:
  - Serves the dashboard (frontend/index.html)
  - Starts/monitors scrape jobs in a background thread
  - Runs AI cleaning/categorization/summarization on results
  - Exports results as CSV or JSON

Run with:  uvicorn main:app --reload --port 8000
Then open: http://127.0.0.1:8000
"""

import os
import io
import csv
import json
import threading
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import scraper
import ai_processor

app = FastAPI(title="Scrape & Synthesize")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

STATE = {
    "job": None,            # scraper.ScrapeJob
    "processed": [],        # list[dict] after AI step
    "ai_status": "idle",    # idle | running | done | error
    "ai_progress": (0, 0),
}
LOCK = threading.Lock()


class ScrapeRequest(BaseModel):
    max_pages: int = 5
    target_url: str = scraper.DEFAULT_URL


def _background_pipeline(max_pages: int, target_url: str):
    job = scraper.ScrapeJob(max_pages=max_pages, target_url=target_url)
    with LOCK:
        STATE["job"] = job
        STATE["processed"] = []
        STATE["ai_status"] = "idle"
        STATE["ai_progress"] = (0, 0)

    scraper.run_scrape(job)

    if job.status != "done" or not job.raw_items:
        return

    with LOCK:
        STATE["ai_status"] = "running"

    def progress_cb(done, total):
        with LOCK:
            STATE["ai_progress"] = (done, total)

    try:
        processed = ai_processor.process_items(job.raw_items, on_progress=progress_cb)
        with LOCK:
            STATE["processed"] = processed
            STATE["ai_status"] = "done"
    except Exception as e:  # noqa: BLE001
        job.emit(f"AI processing failed: {e}")
        with LOCK:
            STATE["ai_status"] = "error"


@app.post("/api/scrape")
def start_scrape(req: ScrapeRequest):
    with LOCK:
        if STATE["job"] is not None and STATE["job"].status == "running":
            return {"ok": False, "message": "A job is already running."}
    t = threading.Thread(target=_background_pipeline, args=(req.max_pages, req.target_url), daemon=True)
    t.start()
    return {"ok": True, "message": "Crawl started."}


@app.get("/api/status")
def status():
    with LOCK:
        job = STATE["job"]
        if job is None:
            return {"scrape_status": "idle", "ai_status": "idle"}
        return {
            "scrape_status": job.status,
            "current_page": job.current_page,
            "max_pages": job.max_pages,
            "target_url": job.target_url,
            "total_items": job.total_items,
            "log": job.log[-40:],
            "error": job.error,
            "ai_status": STATE["ai_status"],
            "ai_progress": STATE["ai_progress"],
            "result_count": len(STATE["processed"]),
        }


@app.get("/api/results")
def results(category: Optional[str] = None, q: Optional[str] = None):
    with LOCK:
        items = list(STATE["processed"])
    if category and category != "all":
        items = [i for i in items if i.get("category") == category]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i.get("title", "").lower()]
    categories = sorted({i.get("category", "Uncategorized") for i in STATE["processed"]})
    return {"items": items, "categories": categories}


@app.get("/api/export/json")
def export_json():
    with LOCK:
        items = list(STATE["processed"])
    buf = io.BytesIO(json.dumps(items, indent=2, ensure_ascii=False).encode("utf-8"))
    return StreamingResponse(
        buf, media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=scraped_results.json"},
    )


@app.get("/api/export/csv")
def export_csv():
    with LOCK:
        items = list(STATE["processed"])
    buf = io.StringIO()
    fieldnames = ["title", "category", "price", "availability", "rating_word",
                  "summary", "url", "image", "ai_engine"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for it in items:
        writer.writerow(it)
    out = io.BytesIO(buf.getvalue().encode("utf-8"))
    return StreamingResponse(
        out, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scraped_results.csv"},
    )


# Serve the dashboard
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
