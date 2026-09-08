import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import polars as pl
from selectolax.parser import HTMLParser
from tqdm import tqdm

from etl.client import get_http_client, fetch_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://www.scj.ro/736/Cautare-jurisprudenta"
RAW_DATA_DIR = Path("data/raw")
CACHE_DIR = Path("data/cache")


def parse_search_results(html_text: str) -> list[dict]:
    """Parse decision cards/table from search results page."""
    parser = HTMLParser(html_text)
    items = []
    
    # Select result rows
    rows = parser.css("tr.grid-row, .jurisprudence-item, table.table-striped tbody tr")
    for row in rows:
        link_elem = row.css_first("a[href*='Detalii-jurisprudenta'], a[href*='jurisprudenta']")
        if not link_elem:
            continue
            
        href = link_elem.attributes.get("href", "")
        scj_id = href.split("id=")[-1].split("&")[0] if "id=" in href else href
        
        cells = [c.text(strip=True) for c in row.css("td")]
        title = link_elem.text(strip=True)
        
        items.append({
            "scj_id": scj_id,
            "title": title,
            "link": f"https://www.scj.ro{href}" if href.startswith("/") else href,
            "cells": cells,
            "raw_html": row.html,
        })
    return items


def extract_decision_detail(client, detail_url: str) -> dict:
    """Fetch and parse complete text and metadata of a single ruling."""
    resp = fetch_with_retry(client, detail_url)
    parser = HTMLParser(resp.text)
    
    # Extract metadata blocks
    meta_dict = {}
    for dt, dd in zip(parser.css("dt, .field-label, th"), parser.css("dd, .field-value, td")):
        meta_dict[dt.text(strip=True).rstrip(":")] = dd.text(strip=True)
        
    content_elem = parser.css_first(".decision-body, #content, .continut-jurisprudenta, .article-content")
    full_content = content_elem.text(strip=True) if content_elem else parser.text(strip=True)
    
    return {
        "metadata": meta_dict,
        "content": full_content,
        "raw_html": resp.text,
    }


def run_extraction(shard_id: int = 0, total_shards: int = 1, department: str | None = None, max_pages: int = 500):
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    swept_ids_file = CACHE_DIR / "swept_ids.parquet"
    swept_ids = set()
    if swept_ids_file.exists():
        swept_ids = set(pl.read_parquet(swept_ids_file)["scj_id"].to_list())
        logger.info(f"Loaded {len(swept_ids)} existing IDs from cache.")

    records = []
    client = get_http_client()
    
    logger.info(f"Starting extraction for shard {shard_id}/{total_shards} (dept: {department or 'ALL'})...")
    
    for page in tqdm(range(1, max_pages + 1), desc="Crawling pages"):
        if page % total_shards != shard_id:
            continue
            
        params = {
            "formTypeId": 6,
            "CustomQuery[10].Key": "Language",
            "CustomQuery[10].Value": 1,
            "CustomQuery[11].Key": "SolutionType",
            "CustomQuery[11].Value": 1,
            "page": page,
        }
        if department:
            params["CustomQuery[1].Key"] = "Department"
            params["CustomQuery[1].Value"] = department

        try:
            resp = fetch_with_retry(client, BASE_URL, params=params)
            page_items = parse_search_results(resp.text)
            
            if not page_items:
                logger.info(f"No more items found on page {page}. Finishing shard.")
                break
                
            for item in page_items:
                if item["scj_id"] in swept_ids:
                    continue
                    
                detail = extract_decision_detail(client, item["link"])
                records.append({
                    "scj_id": item["scj_id"],
                    "title": item["title"],
                    "link": item["link"],
                    "metadata_json": str(detail["metadata"]),
                    "content": detail["content"],
                    "raw_html": detail["raw_html"],
                    "extracted_at": datetime.utcnow(),
                })
                swept_ids.add(item["scj_id"])
                time.sleep(0.5)  # respectful delay between detail pages
                
        except Exception as e:
            logger.error(f"Error on page {page}: {e}")
            time.sleep(2)
            continue
            
    if records:
        out_file = RAW_DATA_DIR / f"raw_shard_{shard_id}_{int(time.time())}.parquet"
        pl.DataFrame(records).write_parquet(out_file, compression="zstd")
        logger.info(f"Saved {len(records)} raw records to {out_file}")
    else:
        logger.info("No new records to save.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract ÎCCJ Case Law")
    parser.add_argument("--shard", type=int, default=0, help="Shard index")
    parser.add_argument("--total-shards", type=int, default=1, help="Total shards")
    parser.add_argument("--dept", type=str, default=None, help="Target department")
    parser.add_argument("--max-pages", type=int, default=100, help="Max search pages")
    args = parser.parse_args()
    
    run_extraction(shard_id=args.shard, total_shards=args.total_shards, department=args.dept, max_pages=args.max_pages)
