"""
Incremental Extractor & Parser for Romanian Legislation from legislatie.just.ro.

Features:
- Automatic watermark resume (from June 2026 to present) across base and incremental parquet shards.
- Robust ASP.NET CSRF token and cookie session handling with auto-refresh.
- Polite request pacing with random jitter and exponential backoff to protect against IP blocks / rate limiting.
- High-precision DOM parser for articles, alineate, litere, puncte.
- Sharded incremental commits to data/incremental/ for zero-RAM overhead, non-blocking I/O and crash resilience.
- Atomic consolidation into single Parquets whenever requested.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
import polars as pl
from selectolax.parser import HTMLParser
from tqdm import tqdm

from etl.parser_dom import RomanianLawDomParser, clean_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BASE_URL = "https://legislatie.just.ro"
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
INCREMENTAL_DIR = DATA_DIR / "incremental"

DOCS_PARQUET = DATA_DIR / "documents.parquet"
ARTS_PARQUET = DATA_DIR / "articles.parquet"
PARAS_PARQUET = DATA_DIR / "paragraphs.parquet"
STATE_FILE = CACHE_DIR / "legislatie_crawler_state.json"
SWEPT_FILE = CACHE_DIR / "swept_legislatie_ids.parquet"


def get_all_doc_parquet_sources() -> List[str]:
    sources = []
    if DOCS_PARQUET.exists() and DOCS_PARQUET.stat().st_size > 0:
        sources.append(str(DOCS_PARQUET))
    for f in sorted(INCREMENTAL_DIR.glob("documents_*.parquet")):
        if f.stat().st_size > 0:
            sources.append(str(f))
    return sources


def get_all_art_parquet_sources() -> List[str]:
    sources = []
    if ARTS_PARQUET.exists() and ARTS_PARQUET.stat().st_size > 0:
        sources.append(str(ARTS_PARQUET))
    for f in sorted(INCREMENTAL_DIR.glob("articles_*.parquet")):
        if f.stat().st_size > 0:
            sources.append(str(f))
    return sources


def get_all_para_parquet_sources() -> List[str]:
    sources = []
    if PARAS_PARQUET.exists() and PARAS_PARQUET.stat().st_size > 0:
        sources.append(str(PARAS_PARQUET))
    for f in sorted(INCREMENTAL_DIR.glob("paragraphs_*.parquet")):
        if f.stat().st_size > 0:
            sources.append(str(f))
    return sources


def get_watermark_date(default_from: str = "2026-06-10") -> date:
    """Reads latest date from state file or scanning all document parquets."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                last_dt = data.get("last_processed_date")
                if last_dt:
                    return datetime.strptime(last_dt, "%Y-%m-%d").date() + timedelta(days=1)
        except Exception as e:
            logger.warning(f"Could not read state file: {e}")

    sources = get_all_doc_parquet_sources()
    if sources:
        try:
            df = pl.scan_parquet(sources)
            max_adopted = df.select(pl.col("adopted_at").max()).collect().item()
            max_published = df.select(pl.col("published_at").max()).collect().item()
            latest = max(filter(None, [max_adopted, max_published]), default=None)
            if latest:
                return latest + timedelta(days=1)
        except Exception as e:
            logger.warning(f"Could not read max date from document parquets: {e}")

    return datetime.strptime(default_from, "%Y-%m-%d").date()


def get_max_ids() -> Tuple[int, int, int]:
    """Returns max (doc_id, art_id, para_id) across all base and incremental files."""
    max_doc_id = 0
    max_art_id = 0
    max_para_id = 0

    doc_sources = get_all_doc_parquet_sources()
    if doc_sources:
        try:
            max_doc_id = pl.scan_parquet(doc_sources).select(pl.col("id").max()).collect().item() or 0
        except Exception:
            pass

    art_sources = get_all_art_parquet_sources()
    if art_sources:
        try:
            max_art_id = pl.scan_parquet(art_sources).select(pl.col("id").max()).collect().item() or 0
        except Exception:
            pass

    para_sources = get_all_para_parquet_sources()
    if para_sources:
        try:
            max_para_id = pl.scan_parquet(para_sources).select(pl.col("id").max()).collect().item() or 0
        except Exception:
            pass

    return max_doc_id, max_art_id, max_para_id


class LegislatieJustClient:
    """HTTP Client with CSRF token management, polite rate-limiting, jitter and backoff."""

    def __init__(self, base_delay: float = 0.8, jitter_range: float = 0.4):
        self.base_delay = base_delay
        self.jitter_range = jitter_range
        self.session = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://legislatie.just.ro/",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        self.token: Optional[str] = None
        self._refresh_token()

    def _sleep_politely(self, multiplier: float = 1.0):
        sleep_duration = (self.base_delay + random.uniform(0.0, self.jitter_range)) * multiplier
        time.sleep(sleep_duration)

    def _refresh_token(self):
        logger.info("Initializing session & obtaining CSRF __RequestVerificationToken...")
        for attempt in range(1, 5):
            try:
                resp = self.session.get(BASE_URL)
                resp.raise_for_status()
                html = HTMLParser(resp.text)
                token_elem = html.css_first('input[name="__RequestVerificationToken"]')
                if token_elem:
                    self.token = token_elem.attributes.get("value")
                    logger.info(f"Session established. Token: {self.token[:20]}...")
                    self._sleep_politely()
                    return
                else:
                    logger.warning("CSRF token input not found in HTML")
            except Exception as e:
                logger.error(f"Attempt {attempt} failed to refresh token: {e}")
                time.sleep(3 * attempt)
        raise RuntimeError("Failed to obtain CSRF token from legislatie.just.ro")

    def search_date_range(self, from_dt: date, to_dt: date, max_pages: int = 250, target_count: Optional[int] = None) -> List[Dict[str, Any]]:
        from_str = from_dt.strftime("%Y/%m/%d")
        to_str = to_dt.strftime("%Y/%m/%d")
        logger.info(f"Searching acts from {from_str} to {to_str}...")

        if not self.token:
            self._refresh_token()

        payload = {
            "__RequestVerificationToken": self.token,
            "DataSemnariiTextFrom": from_str,
            "DataSemnariiTextTo": to_str,
            "actiontype": "Căutare",
        }

        try:
            resp = self.session.post(BASE_URL + "/", data=payload)
            if "__RequestVerificationToken" in resp.text and "RezultateCautare" not in resp.text:
                self._refresh_token()
                payload["__RequestVerificationToken"] = self.token
                resp = self.session.post(BASE_URL + "/", data=payload)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Search POST failed: {e}")
            return []

        items = []
        html = HTMLParser(resp.text)
        page_items = self._parse_search_page(html)
        items.extend(page_items)

        if target_count and len(items) >= target_count:
            return items[:target_count]

        max_page = 1
        for a in html.css("a[href*='RezultateCautare?page=']"):
            href = a.attributes.get("href", "")
            m = re.search(r"page=(\d+)", href)
            if m:
                p_num = int(m.group(1))
                if p_num > max_page:
                    max_page = p_num

        total_pages = min(max_page, max_pages)
        logger.info(f"Found {len(items)} items on page 1. Total search pages available: {max_page} (crawling up to {total_pages})")

        for p in range(2, total_pages + 1):
            if target_count and len(items) >= target_count:
                break
            self._sleep_politely()
            page_url = f"{BASE_URL}/Public/RezultateCautare?page={p}&semnatinceputtext={from_str}&semnatsfarsittext={to_str}"
            try:
                p_resp = self.session.get(page_url)
                if p_resp.status_code == 200:
                    p_html = HTMLParser(p_resp.text)
                    p_items = self._parse_search_page(p_html)
                    if not p_items:
                        break
                    items.extend(p_items)
                elif p_resp.status_code == 429:
                    logger.warning("Search rate limited (429), pausing 15s...")
                    time.sleep(15)
            except Exception as e:
                logger.warning(f"Error fetching page {p}: {e}")
                time.sleep(2)

        # Deduplicate
        seen = set()
        deduped = []
        for it in items:
            if it["portal_id"] not in seen:
                seen.add(it["portal_id"])
                deduped.append(it)

        logger.info(f"Identified {len(deduped)} distinct documents in date range [{from_str}, {to_str}].")
        return deduped

    def _parse_search_page(self, html: HTMLParser) -> List[Dict[str, Any]]:
        items = []
        for a in html.css('a[href*="/Public/DetaliiDocument/"]'):
            href = a.attributes.get("href", "")
            label = clean_text(a.text(strip=True))
            if not href or label == "Vizualizeaza":
                continue
            doc_id_match = re.search(r"/Public/DetaliiDocument/(\d+)", href)
            if doc_id_match:
                doc_id = doc_id_match.group(1)
                items.append({
                    "portal_id": doc_id,
                    "title": label,
                    "href": f"/Public/DetaliiDocument/{doc_id}",
                    "url": f"{BASE_URL}/Public/DetaliiDocument/{doc_id}",
                })
        return items

    def fetch_document_html(self, detail_url: str) -> Optional[str]:
        self._sleep_politely()
        for attempt in range(1, 5):
            try:
                resp = self.session.get(detail_url)
                if resp.status_code == 200:
                    return resp.text
                elif resp.status_code in (429, 503):
                    cool_down = 12 * attempt
                    logger.warning(f"HTTP {resp.status_code} received on {detail_url}. Cool-down for {cool_down}s to avoid blocking...")
                    time.sleep(cool_down)
                elif resp.status_code == 404:
                    logger.warning(f"Document not found (404): {detail_url}")
                    return None
            except Exception as e:
                logger.warning(f"Network error on {detail_url} (attempt {attempt}): {e}")
                time.sleep(2 * attempt)
        return None


def commit_incremental_shard(
    new_docs: List[Dict[str, Any]],
    new_arts: List[Dict[str, Any]],
    new_paras: List[Dict[str, Any]],
    raw_records: List[Dict[str, Any]],
    swept_ids: Set[str],
    last_processed_date: date,
):
    """Saves newly parsed batch cleanly to incremental parquet shards without locking base files."""
    if not new_docs:
        return

    ts = int(time.time() * 1000)
    logger.info(f"Writing incremental shard #{ts}: +{len(new_docs)} documents, +{len(new_arts)} articles, +{len(new_paras)} paragraphs...")

    # 1. Raw records
    if raw_records:
        shard_file = RAW_DIR / f"raw_legislatie_{ts}.parquet"
        pl.DataFrame(raw_records).write_parquet(shard_file, compression="zstd")
        raw_records.clear()

    # 2. Swept IDs cache
    pl.DataFrame({"portal_id": list(swept_ids)}).write_parquet(SWEPT_FILE, compression="zstd")

    # 3. Incremental Shards
    doc_shard = INCREMENTAL_DIR / f"documents_{ts}.parquet"
    art_shard = INCREMENTAL_DIR / f"articles_{ts}.parquet"
    para_shard = INCREMENTAL_DIR / f"paragraphs_{ts}.parquet"

    pl.DataFrame(new_docs).write_parquet(doc_shard, compression="zstd")
    pl.DataFrame(new_arts).write_parquet(art_shard, compression="zstd")
    pl.DataFrame(new_paras).write_parquet(para_shard, compression="zstd")

    # 4. Checkpoint state
    with open(STATE_FILE, "w") as f:
        json.dump({
            "last_processed_date": last_processed_date.strftime("%Y-%m-%d"),
            "last_run_timestamp": datetime.now().isoformat(),
            "latest_shard_timestamp": ts,
        }, f, indent=2)

    new_docs.clear()
    new_arts.clear()
    new_paras.clear()
    logger.info(f"Incremental shard #{ts} committed successfully.")


def consolidate_parquets():
    """Consolidates base files + incremental shards into single unified base parquets atomically."""
    logger.info("Consolidating incremental shards into base Parquet files...")
    doc_sources = get_all_doc_parquet_sources()
    art_sources = get_all_art_parquet_sources()
    para_sources = get_all_para_parquet_sources()

    if len(doc_sources) > 1:
        tmp_docs = DATA_DIR / "documents.parquet.tmp"
        logger.info(f"Merging {len(doc_sources)} document files...")
        pl.scan_parquet(doc_sources).unique(subset=["id"]).sink_parquet(tmp_docs, compression="zstd")
        tmp_docs.replace(DOCS_PARQUET)

    if len(art_sources) > 1:
        tmp_arts = DATA_DIR / "articles.parquet.tmp"
        logger.info(f"Merging {len(art_sources)} article files...")
        pl.scan_parquet(art_sources).unique(subset=["id"]).sink_parquet(tmp_arts, compression="zstd")
        tmp_arts.replace(ARTS_PARQUET)

    if len(para_sources) > 1:
        tmp_paras = DATA_DIR / "paragraphs.parquet.tmp"
        logger.info(f"Merging {len(para_sources)} paragraph files...")
        pl.scan_parquet(para_sources).unique(subset=["id"]).sink_parquet(tmp_paras, compression="zstd")
        tmp_paras.replace(PARAS_PARQUET)

    # Cleanup merged incremental shards
    for f in INCREMENTAL_DIR.glob("*.parquet"):
        try:
            f.unlink()
        except Exception:
            pass

    logger.info("Consolidation complete!")


def run_pipeline(
    from_date_str: Optional[str] = None,
    to_date_str: Optional[str] = None,
    max_pages: int = 250,
    base_delay: float = 0.8,
    batch_size: int = 40,
    dry_run: bool = False,
    limit: Optional[int] = None,
    consolidate: bool = False,
):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INCREMENTAL_DIR.mkdir(parents=True, exist_ok=True)

    if from_date_str:
        from_dt = datetime.strptime(from_date_str, "%Y-%m-%d").date()
    else:
        from_dt = get_watermark_date()

    if to_date_str:
        to_dt = datetime.strptime(to_date_str, "%Y-%m-%d").date()
    else:
        to_dt = date.today()

    logger.info(f"Target date interval: {from_dt} -> {to_dt}")
    if from_dt > to_dt:
        logger.info("Watermark is already up-to-date with target date. Nothing to fetch.")
        if consolidate:
            consolidate_parquets()
        return

    swept_ids: Set[str] = set()
    if SWEPT_FILE.exists():
        try:
            swept_ids = set(pl.read_parquet(SWEPT_FILE)["portal_id"].to_list())
            logger.info(f"Loaded {len(swept_ids)} previously crawled IDs from cache.")
        except Exception:
            pass

    client = LegislatieJustClient(base_delay=base_delay, jitter_range=0.4)
    doc_entries = client.search_date_range(from_dt, to_dt, max_pages=max_pages, target_count=limit)

    to_crawl = [d for d in doc_entries if str(d["portal_id"]) not in swept_ids]
    if limit and limit > 0:
        to_crawl = to_crawl[:limit]
        logger.info(f"Limit applied: crawling first {len(to_crawl)} acts.")

    logger.info(f"Documents to crawl and parse: {len(to_crawl)} (out of {len(doc_entries)} found)")

    if not to_crawl:
        logger.info("No new documents to process.")
        if consolidate:
            consolidate_parquets()
        return

    if dry_run:
        logger.info(f"Dry run enabled. Would crawl {len(to_crawl)} documents. Exiting.")
        return

    dom_parser = RomanianLawDomParser()
    raw_records: List[Dict[str, Any]] = []

    # Retrieve max IDs across all existing shards
    max_doc_id, max_art_id, max_para_id = get_max_ids()

    new_docs: List[Dict[str, Any]] = []
    new_arts: List[Dict[str, Any]] = []
    new_paras: List[Dict[str, Any]] = []

    curr_doc_id = max_doc_id
    curr_art_id = max_art_id
    curr_para_id = max_para_id

    for idx, item in enumerate(tqdm(to_crawl, desc="Fetching & parsing legislation"), start=1):
        p_id = str(item["portal_id"])
        url = item["url"]

        html_text = client.fetch_document_html(url)
        if not html_text:
            continue

        raw_records.append({
            "portal_id": p_id,
            "title": item["title"],
            "url": url,
            "html": html_text,
            "fetched_at": datetime.now(),
        })

        parsed_doc = dom_parser.parse_html(html_text, url=url)
        if not parsed_doc:
            continue

        curr_doc_id += 1
        assigned_doc_id = curr_doc_id

        doc_cit = item["title"]
        if parsed_doc.document_type and parsed_doc.document_number:
            prefix = "Legea" if "LEGE" in parsed_doc.document_type else ("HG" if "HOTĂRÂRE" in parsed_doc.document_type else parsed_doc.document_type.title())
            doc_cit = f"{prefix} {parsed_doc.document_number}/{from_dt.year}"

        new_docs.append({
            "id": assigned_doc_id,
            "type": parsed_doc.document_type,
            "document_number": parsed_doc.document_number,
            "document_citation": doc_cit,
            "issuer": parsed_doc.issuer or "PARLAMENTUL ROMÂNIEI",
            "title": parsed_doc.title or item["title"],
            "content": parsed_doc.full_text,
            "adopted_at": from_dt,
            "published_at": from_dt,
            "effective_at": from_dt,
            "gazette_number": None,
            "status": "în vigoare",
            "link": url,
            "synced_at": datetime.now(),
        })

        for art in parsed_doc.articles:
            curr_art_id += 1
            assigned_art_id = curr_art_id

            new_arts.append({
                "id": assigned_art_id,
                "document_id": assigned_doc_id,
                "article_number": art.article_number,
                "article_variant": art.article_variant,
                "article_citation": art.article_citation,
                "content": art.content,
            })

            for p in art.paragraphs:
                curr_para_id += 1
                new_paras.append({
                    "id": curr_para_id,
                    "article_id": assigned_art_id,
                    "paragraph_number": p.paragraph_number,
                    "paragraph_citation": p.paragraph_citation,
                    "content": p.content,
                })

        swept_ids.add(p_id)

        # Commit incremental shard every batch_size items
        if idx % batch_size == 0:
            commit_incremental_shard(new_docs, new_arts, new_paras, raw_records, swept_ids, to_dt)

    # Final batch commit
    if new_docs:
        commit_incremental_shard(new_docs, new_arts, new_paras, raw_records, swept_ids, to_dt)

    if consolidate:
        consolidate_parquets()

    logger.info("Pipeline run complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incremental Romanian Legislation Extractor")
    parser.add_argument("--from-date", type=str, default=None, help="Start date (YYYY-MM-DD), defaults to watermark")
    parser.add_argument("--to-date", type=str, default=None, help="End date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--max-pages", type=int, default=250, help="Max search pages per run")
    parser.add_argument("--delay", type=float, default=0.8, help="Base polite delay between requests (default: 0.8s)")
    parser.add_argument("--batch-size", type=int, default=40, help="Batch commit size (default: 40)")
    parser.add_argument("--limit", type=int, default=None, help="Limit total acts to crawl in this run")
    parser.add_argument("--dry-run", action="store_true", help="Only search and display counts without fetching")
    parser.add_argument("--consolidate", action="store_true", help="Consolidate incremental shards into base files")
    args = parser.parse_args()

    run_pipeline(
        from_date_str=args.from_date,
        to_date_str=args.to_date,
        max_pages=args.max_pages,
        base_delay=args.delay,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        limit=args.limit,
        consolidate=args.consolidate,
    )
