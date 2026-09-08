import argparse
import datetime
import io
import logging
import os
import re
from pathlib import Path
from typing import Any
import unicodedata

import httpx
import polars as pl
import pypdf
from selectolax.parser import HTMLParser
from tqdm import tqdm

from etl.client import get_http_client, fetch_with_retry
from etl.schemas import CCRDecisionSchema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CCR_PARQUET = DATA_DIR / "ccr_decisions.parquet"

CCR_SECTIONS = [
    {"category": "Decizii de admitere", "url": "https://www.ccr.ro/jurisprudenta/jurisprudenta-decizii-de-admitere/"},
    {"category": "Decizii relevante", "url": "https://www.ccr.ro/jurisprudenta/decizii-relevante/"},
    {"category": "Hotărâri de admitere", "url": "https://www.ccr.ro/jurisprudenta/hotarari-de-admitere/"},
    {"category": "Hotărâri relevante", "url": "https://www.ccr.ro/jurisprudenta/hotarari-relevante/"},
    {"category": "Avize consultative", "url": "https://www.ccr.ro/jurisprudenta/avize-consultative/"},
]

RO_MONTHS = {
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4,
    "mai": 5, "iunie": 6, "iulie": 7, "august": 8,
    "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12
}

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)[:100]

def parse_romanian_date(date_str: str | None) -> datetime.date | None:
    if not date_str:
        return None
    date_str = date_str.strip().lower()
    
    # Format: 17 august 2026 or 11 februarie 2026
    m = re.search(r"(\d{1,2})\s+([a-zăîâșț]+)\s+(\d{4})", date_str)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2), int(m.group(3))
        if month_name in RO_MONTHS:
            try:
                return datetime.date(year, RO_MONTHS[month_name], day)
            except ValueError:
                pass

    # Format: 11.02.2025 or 11/02/2025
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", date_str)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(year, month, day)
        except ValueError:
            pass

    return None

def parse_ccr_title(title_text: str) -> dict[str, Any]:
    """Extract act_type, act_number, date, and act_year from title string."""
    title_clean = " ".join(title_text.split())
    
    act_type = "DECIZIE"
    if "HOTĂRÂRE" in title_clean.upper() or "HOTARARE" in title_clean.upper():
        act_type = "HOTĂRÂRE"
    elif "AVIZ" in title_clean.upper():
        act_type = "AVIZ CONSULTATIV"
        
    num_match = re.search(r"nr\.?\s*([0-9A-Za-z\-_/]+)", title_clean, re.IGNORECASE)
    act_number = num_match.group(1).strip() if num_match else "0"
    
    date_match = re.search(r"din\s+(.+)$", title_clean, re.IGNORECASE)
    date_str = date_match.group(1).strip() if date_match else None
    
    parsed_date = parse_romanian_date(date_str)
    act_year = parsed_date.year if parsed_date else None
    
    if not act_year:
        year_match = re.search(r"20\d\d|19\d\d", title_clean)
        if year_match:
            act_year = int(year_match.group(0))

    return {
        "act_type": act_type,
        "act_number": act_number,
        "decision_date": parsed_date,
        "act_year": act_year,
    }

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Decompress and extract text from raw PDF bytes."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text.strip())
        return "\n\n".join(pages_text)
    except Exception as e:
        logger.warning(f"Failed to extract PDF text: {e}")
        return ""

def fetch_section_decisions(client: httpx.Client, section_meta: dict[str, str], existing_by_url: dict[str, dict], max_pages: int | None = None) -> list[dict[str, Any]]:
    category = section_meta["category"]
    base_url = section_meta["url"]
    logger.info(f"Extracting CCR section: {category} ({base_url})")

    try:
        resp = fetch_with_retry(client, base_url)
    except Exception as e:
        logger.warning(f"Could not fetch {base_url}: {e}")
        return []

    tree = HTMLParser(resp.text)
    
    pagination_ul = tree.css_first("ul.pt-cv-pagination")
    total_pages = 1
    if pagination_ul and "data-totalpages" in pagination_ul.attributes:
        try:
            total_pages = int(pagination_ul.attributes["data-totalpages"])
        except ValueError:
            total_pages = 1

    if max_pages and max_pages < total_pages:
        total_pages = max_pages

    logger.info(f"Section '{category}' will crawl {total_pages} pages")
    records = []
    
    for page_num in range(1, total_pages + 1):
        page_url = base_url if page_num == 1 else f"{base_url}?_page={page_num}"
        logger.info(f"Fetching {category} - page {page_num}/{total_pages}")
        
        try:
            page_resp = fetch_with_retry(client, page_url)
        except Exception as e:
            logger.warning(f"Failed to fetch {page_url}: {e}")
            continue
            
        page_tree = HTMLParser(page_resp.text)
        items = page_tree.css(".pt-cv-content-item")
        
        for item in items:
            title_el = item.css_first(".pt-cv-title a")
            if not title_el:
                continue
                
            title = title_el.text().strip()
            raw_pdf_url = title_el.attributes.get("href", "").strip()
            pdf_url = raw_pdf_url.split("#")[0].strip()
            if not pdf_url:
                continue
            if not pdf_url.startswith("http"):
                pdf_url = "https://www.ccr.ro" + pdf_url
                
            content_el = item.css_first(".pt-cv-content")
            summary = ""
            if content_el:
                rm_btn = content_el.css_first(".pt-cv-rmwrap")
                if rm_btn:
                    rm_btn.decompose()
                summary = " ".join(content_el.text().split())
                
            pub_el = item.css_first(".pt-cv-ctf-value")
            pub_notice = pub_el.text().strip() if pub_el else None
            
            parsed_info = parse_ccr_title(title)
            slug = slugify(f"{parsed_info['act_type']}-{parsed_info['act_number']}-{parsed_info['act_year'] or ''}-{title}")
            
            content = ""
            if pdf_url in existing_by_url and existing_by_url[pdf_url].get("content"):
                content = existing_by_url[pdf_url]["content"]
            else:
                try:
                    pdf_resp = fetch_with_retry(client, pdf_url)
                    content = extract_text_from_pdf(pdf_resp.content)
                except Exception as e:
                    logger.warning(f"Could not download PDF from {pdf_url}: {e}")
                    
            record = {
                "id": len(records) + 1,
                "slug": slug,
                "title": title,
                "act_type": parsed_info["act_type"],
                "act_number": str(parsed_info["act_number"]),
                "act_year": parsed_info["act_year"],
                "decision_date": parsed_info["decision_date"],
                "category": category,
                "publication_notice": pub_notice,
                "summary": summary,
                "content": content,
                "pdf_url": pdf_url,
                "synced_at": datetime.datetime.now(datetime.timezone.utc),
            }
            records.append(record)
            
    return records

def run(max_pages: int | None = None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_by_url = {}
    
    if CCR_PARQUET.exists():
        try:
            df_exist = pl.read_parquet(CCR_PARQUET)
            for row in df_exist.iter_rows(named=True):
                existing_by_url[row["pdf_url"]] = row
            logger.info(f"Loaded {len(existing_by_url)} existing CCR decisions for caching")
        except Exception as e:
            logger.warning(f"Could not read existing CCR parquet: {e}")

    client = get_http_client(timeout=45.0)
    all_decisions = []
    seen_urls = set()

    for section in CCR_SECTIONS:
        try:
            section_records = fetch_section_decisions(client, section, existing_by_url, max_pages=max_pages)
            for r in section_records:
                if r["pdf_url"] not in seen_urls:
                    seen_urls.add(r["pdf_url"])
                    all_decisions.append(r)
        except Exception as e:
            logger.error(f"Error fetching section {section['category']}: {e}", exc_info=True)

    if not all_decisions:
        logger.warning("No CCR decisions extracted.")
        return

    # Assign sequential IDs
    for idx, d in enumerate(all_decisions, start=1):
        d["id"] = idx

    df = pl.DataFrame(all_decisions)
    df = CCRDecisionSchema.validate(df)
    
    df.write_parquet(CCR_PARQUET, compression="zstd")
    logger.info(f"Successfully saved {len(df)} CCR decisions to {CCR_PARQUET}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract CCR jurisprudence decisions")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit pages per category for testing")
    args = parser.parse_args()
    run(max_pages=args.max_pages)
