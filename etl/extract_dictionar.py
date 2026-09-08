import argparse
import hashlib
import logging
import re
import time
from datetime import datetime
from pathlib import Path
import polars as pl
from selectolax.parser import HTMLParser
from tqdm import tqdm

from etl.client import get_http_client, fetch_with_retry
from etl.citations import extract_citations
from etl.schemas import DictionaryTermSchema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://legeaz.net"
CATALOG_URL = "https://legeaz.net/dictionar-juridic/"
DATA_DIR = Path("data")
CACHE_DIR = Path("data/cache")


def parse_catalog_page(html_text: str) -> tuple[str, list[dict]]:
    """Parse list of terms and active letter from dictionary catalog page."""
    parser = HTMLParser(html_text)
    items = []
    
    # Extract letter header if present
    letter_elem = parser.css_first("td.sectiontableheader h2, .sectiontableheader")
    current_letter = letter_elem.text(strip=True).upper() if letter_elem else "A"

    rows = parser.css("table.lista_art tr.sectiontableentry1, table.lista_art tr.sectiontableentry2, tr.sectiontableentry1, tr.sectiontableentry2")
    for row in rows:
        link_elem = row.css_first("a[href*='/dictionar-juridic/']")
        if not link_elem:
            continue
        href = link_elem.attributes.get("href", "")
        # Avoid self-referencing catalog links
        if href in ["/dictionar-juridic/", "/dictionar-juridic"]:
            continue
            
        term = link_elem.text(strip=True)
        slug = href.strip("/").split("/")[-1]
        
        # Determine starting letter
        first_letter = term[0].upper() if term else current_letter
        
        items.append({
            "term": term,
            "slug": slug,
            "letter": first_letter,
            "link": f"{BASE_URL}{href}" if href.startswith("/") else href,
        })
    return current_letter, items


def parse_term_detail(html_text: str) -> dict:
    """Extract clean definition text, removing ads, comments, and sidebars."""
    parser = HTMLParser(html_text)
    
    # Strip ads and extra widgets
    for sel in [
        ".adsbygoogle", "#acatsus", "#acat1", "#acat2", ".social", 
        "table.pagenav2", ".reltable", "#jc", "#comments", "script", "style"
    ]:
        for node in parser.css(sel):
            node.decompose()

    # Title / Term
    title_elem = parser.css_first("h1.l-postheader, h1")
    term = title_elem.text(strip=True) if title_elem else "Termen Juridic"

    # Main definition content
    article_elem = parser.css_first(".l-article, .item-page, .l-postcontent")
    definition = article_elem.text(strip=True) if article_elem else parser.text(strip=True)

    return {
        "term": term,
        "definition": definition,
    }


def run_dictionar_extraction(max_pages: int = 104):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    swept_file = CACHE_DIR / "swept_dictionar_urls.parquet"
    swept_urls = set()
    if swept_file.exists():
        try:
            swept_urls = set(pl.read_parquet(swept_file)["url"].to_list())
            logger.info(f"Loaded {len(swept_urls)} cached dictionary term URLs.")
        except Exception:
            pass

    client = get_http_client()
    term_records = []
    relationships = []

    logger.info(f"Starting crawl of legeaz.net/dictionar-juridic/ (up to {max_pages} pages)...")

    for page_num in range(1, max_pages + 1):
        page_url = CATALOG_URL if page_num == 1 else f"{CATALOG_URL}pagina-{page_num}"
        logger.info(f"Fetching dictionary page {page_num}: {page_url}")

        try:
            resp = fetch_with_retry(client, page_url)
            current_letter, items = parse_catalog_page(resp.text)
            
            if not items:
                logger.info(f"No more terms found on page {page_num}. Terminating crawl.")
                break

            for it in tqdm(items, desc=f"Page {page_num} ({current_letter})"):
                link = it["link"]
                if link in swept_urls:
                    continue

                try:
                    detail_resp = fetch_with_retry(client, link)
                    parsed = parse_term_detail(detail_resp.text)
                    
                    term_id = int(hashlib.sha256(it["slug"].encode()).hexdigest()[:12], 16)
                    
                    term_records.append({
                        "id": term_id,
                        "slug": it["slug"],
                        "term": parsed["term"] or it["term"],
                        "letter": it["letter"],
                        "definition": parsed["definition"],
                        "link": link,
                        "synced_at": datetime.now(),
                    })
                    
                    # Extract legal citations mentioned in the definition
                    rels = extract_citations(term_id, parsed["definition"])
                    relationships.extend(rels)

                    swept_urls.add(link)
                    time.sleep(0.3)  # polite rate limit
                except Exception as e:
                    logger.error(f"Error fetching definition {link}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error fetching catalog page {page_num}: {e}")
            break

    if term_records:
        df = pl.DataFrame(term_records)
        validated_df = DictionaryTermSchema.validate(df)
        
        out_path = DATA_DIR / "dictionar_juridic.parquet"
        if out_path.exists():
            existing = pl.read_parquet(out_path)
            combined = pl.concat([existing, validated_df]).unique(subset=["slug"])
            combined.write_parquet(out_path, compression="zstd")
            logger.info(f"Updated {out_path} (total {len(combined)} dictionary definitions).")
        else:
            validated_df.write_parquet(out_path, compression="zstd")
            logger.info(f"Saved {len(validated_df)} dictionary definitions to {out_path}.")

        pl.DataFrame({"url": list(swept_urls)}).write_parquet(swept_file, compression="zstd")
    else:
        logger.info("No new dictionary terms found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract Romanian Legal Dictionary from legeaz.net")
    parser.add_argument("--max-pages", type=int, default=104, help="Maximum dictionary pages to crawl (1-104)")
    args = parser.parse_args()
    
    run_dictionar_extraction(max_pages=args.max_pages)
