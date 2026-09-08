import argparse
import json
import logging
import os
import sys
import time
import zipfile
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
    
    rows = parser.css("tr.grid-row, .jurisprudence-item, table.table-striped tbody tr, .search-result-item")
    for row in rows:
        link_elem = row.css_first("a[href*='Detalii-jurisprudenta'], a[href*='jurisprudenta'], a[href*='Detalii']")
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
    
    meta_dict = {}
    for dt, dd in zip(parser.css("dt, .field-label, th"), parser.css("dd, .field-value, td")):
        meta_dict[dt.text(strip=True).rstrip(":")] = dd.text(strip=True)
        
    content_elem = parser.css_first(".decision-body, #content, .continut-jurisprudenta, .article-content, .panel-body")
    full_content = content_elem.text(strip=True) if content_elem else parser.text(strip=True)
    
    return {
        "metadata": meta_dict,
        "content": full_content,
        "raw_html": resp.text,
    }


def ingest_from_dump_zip(dump_path: Path) -> int:
    """Ingest decisions/cases from an official ÎCCJ open data zip archive (JSON/XML)."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    
    logger.info(f"Opening open data archive: {dump_path}")
    with zipfile.ZipFile(dump_path, 'r') as zf:
        for file_info in zf.infolist():
            if file_info.filename.endswith(".json"):
                with zf.open(file_info) as f:
                    try:
                        data = json.load(f)
                        items = data if isinstance(data, list) else [data]
                        for it in items:
                            scj_id = str(it.get("id") or it.get("numarDosar") or it.get("numarDocument"))
                            records.append({
                                "scj_id": scj_id,
                                "title": it.get("titlu") or it.get("obiect") or f"Dosar {scj_id}",
                                "link": it.get("link") or f"https://www.scj.ro/detalii?id={scj_id}",
                                "metadata_json": json.dumps(it),
                                "content": it.get("continut") or it.get("sumar") or it.get("text") or "",
                                "raw_html": "",
                                "extracted_at": datetime.now(),
                            })
                    except Exception as e:
                        logger.warning(f"Error parsing {file_info.filename}: {e}")
                        
    if records:
        out_file = RAW_DATA_DIR / f"raw_dump_{int(time.time())}.parquet"
        pl.DataFrame(records).write_parquet(out_file, compression="zstd")
        logger.info(f"Ingested {len(records)} records from dump into {out_file}")
    return len(records)


def run_extraction(
    shard_id: int = 0, 
    total_shards: int = 1, 
    department: str | None = None, 
    from_date: str | None = None,
    to_date: str | None = None,
    max_pages: int = 500,
    dump_path: str | None = None
):
    if dump_path:
        p = Path(dump_path)
        if p.exists():
            ingest_from_dump_zip(p)
            return

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    swept_ids_file = CACHE_DIR / "swept_ids.parquet"
    swept_ids = set()
    if swept_ids_file.exists():
        try:
            swept_ids = set(pl.read_parquet(swept_ids_file)["scj_id"].to_list())
            logger.info(f"Loaded {len(swept_ids)} existing IDs from cache.")
        except Exception:
            pass

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
        if from_date:
            params["CustomQuery[3].Key"] = "DecisionDate"
            params["CustomQuery[3].Value"] = from_date

        try:
            resp = fetch_with_retry(client, BASE_URL, params=params)
            page_items = parse_search_results(resp.text)
            
            if not page_items:
                logger.info(f"No more items on page {page}. Finishing shard.")
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
                    "extracted_at": datetime.now(),
                })
                swept_ids.add(item["scj_id"])
                time.sleep(0.5)  # respectful delay
                
        except Exception as e:
            logger.error(f"Error on page {page}: {e}")
            time.sleep(2)
            continue
            
    if records:
        out_file = RAW_DATA_DIR / f"raw_shard_{shard_id}_{int(time.time())}.parquet"
        pl.DataFrame(records).write_parquet(out_file, compression="zstd")
        logger.info(f"Saved {len(records)} raw records to {out_file}")
        
        # Update swept_ids
        pl.DataFrame({"scj_id": list(swept_ids)}).write_parquet(swept_ids_file, compression="zstd")
    else:
        logger.info("No new records found to extract.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract ÎCCJ Case Law")
    parser.add_argument("--shard", type=int, default=0, help="Shard index")
    parser.add_argument("--total-shards", type=int, default=1, help="Total shards")
    parser.add_argument("--dept", type=str, default=None, help="Target department")
    parser.add_argument("--from-date", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to-date", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--max-pages", type=int, default=100, help="Max search pages")
    parser.add_argument("--dump-path", type=str, default=None, help="Path to official zip dump")
    args = parser.parse_args()
    
    run_extraction(
        shard_id=args.shard,
        total_shards=args.total_shards,
        department=args.dept,
        from_date=args.from_date,
        to_date=args.to_date,
        max_pages=args.max_pages,
        dump_path=args.dump_path
    )
