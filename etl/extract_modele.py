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
from etl.schemas import DocumentTemplateSchema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://legeaz.net"
CATALOG_URL = "https://legeaz.net/modele/"
DATA_DIR = Path("data")
CACHE_DIR = Path("data/cache")


def classify_category(title: str, content: str) -> str:
    title_lower = title.lower()
    if any(w in title_lower for w in ["contract", "pact de opţiune", "pact de optiune", "cesiune", "promisiune", "dare în plată", "dare in plata"]):
        return "Contracte"
    if any(w in title_lower for w in ["succesiu", "moşteni", "mosteni", "testament", "executor testamentar"]):
        return "Succesiuni & Testamente"
    if any(w in title_lower for w in ["conventi", "convenţi", "matrimonial", "soţi", "soti", "divorţ", "divort", "tutel"]):
        return "Dreptul Familiei"
    if any(w in title_lower for w in ["ipotec", "gaj", "superficie", "uzufruct", "uz", "abita", "trecere", "proprietate"]):
        return "Drepturi Reale & Garanții"
    if any(w in title_lower for w in ["cerere", "chemare în judecată", "chemare in judecata", "acțiune", "actiune"]):
        return "Cereri & Acțiuni în Justiție"
    if any(w in title_lower for w in ["plângere", "plangere", "contestație", "contestatie"]):
        return "Plângeri & Contestații"
    if any(w in title_lower for w in ["mandat", "procur", "împuternicire", "imputernicire"]):
        return "Procuri & Mandate"
    if any(w in title_lower for w in ["declaraţi", "declarati"]):
        return "Declarații"
    if any(w in title_lower for w in ["munc", "salariat", "angajator", "concedi"]):
        return "Dreptul Muncii"
    return "Diverse Acte Juridice"


def parse_catalog_page(html_text: str) -> list[dict]:
    """Parse links to document templates from catalog page."""
    parser = HTMLParser(html_text)
    items = []
    
    rows = parser.css("table.lista_art tr.sectiontableentry1, table.lista_art tr.sectiontableentry2, tr.sectiontableentry1, tr.sectiontableentry2")
    for row in rows:
        link_elem = row.css_first("a[href*='/modele/']")
        if not link_elem:
            continue
        href = link_elem.attributes.get("href", "")
        title = link_elem.text(strip=True)
        slug = href.strip("/").split("/")[-1]
        
        items.append({
            "title": title,
            "slug": slug,
            "link": f"{BASE_URL}{href}" if href.startswith("/") else href,
        })
    return items


def parse_template_detail(html_text: str) -> dict:
    """Extract clean template text, legal basis, footnotes, and source."""
    parser = HTMLParser(html_text)
    
    # Strip unwanted elements (ads, comments, social bars, related links)
    for sel in [
        ".adsbygoogle", "#acatsus", "#acat1", "#acat2", ".social", 
        "table.pagenav2", ".reltable", "#jc", "#comments", "script", "style"
    ]:
        for node in parser.css(sel):
            node.decompose()

    # Title
    title_elem = parser.css_first("h1.l-postheader, h1")
    title = title_elem.text(strip=True) if title_elem else "Model Document"

    # Main content article
    article_elem = parser.css_first(".l-article, .item-page, .l-postcontent")
    content = article_elem.text(strip=True) if article_elem else parser.text(strip=True)

    # Footnotes / Source attribution
    source_attr = ""
    ftn_elem = parser.css_first("#ftn1, .footnote, div[id^='ftn']")
    if ftn_elem:
        source_attr = ftn_elem.text(strip=True)

    # Legal basis detection from title & text
    legal_basis_match = re.search(r"\((?:în\s+condițiile|în\s+condiţiile|conform|potrivit)\s+([^\)]+)\)", title, re.IGNORECASE)
    legal_basis = legal_basis_match.group(1).strip() if legal_basis_match else ""

    return {
        "title": title,
        "content": content,
        "legal_basis": legal_basis,
        "source_attribution": source_attr or "Uniunea Națională a Notarilor Publici / Modele Juridice",
    }


def run_modele_extraction(max_pages: int = 18):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    swept_file = CACHE_DIR / "swept_modele_urls.parquet"
    swept_urls = set()
    if swept_file.exists():
        try:
            swept_urls = set(pl.read_parquet(swept_file)["url"].to_list())
            logger.info(f"Loaded {len(swept_urls)} cached template URLs.")
        except Exception:
            pass

    client = get_http_client()
    template_records = []
    relationships = []

    logger.info(f"Starting crawl of legeaz.net/modele/ (up to {max_pages} pages)...")

    for page_num in range(1, max_pages + 1):
        page_url = CATALOG_URL if page_num == 1 else f"{CATALOG_URL}pagina-{page_num}"
        logger.info(f"Fetching catalog page {page_num}: {page_url}")

        try:
            resp = fetch_with_retry(client, page_url)
            items = parse_catalog_page(resp.text)
            
            if not items:
                logger.info(f"No more items found on page {page_num}. Terminating catalog crawl.")
                break

            for it in tqdm(items, desc=f"Page {page_num} templates"):
                link = it["link"]
                if link in swept_urls:
                    continue

                try:
                    detail_resp = fetch_with_retry(client, link)
                    parsed = parse_template_detail(detail_resp.text)
                    
                    doc_id = int(hashlib.sha256(it["slug"].encode()).hexdigest()[:12], 16)
                    category = classify_category(parsed["title"], parsed["content"])
                    
                    template_records.append({
                        "id": doc_id,
                        "slug": it["slug"],
                        "title": parsed["title"],
                        "category": category,
                        "legal_basis": parsed["legal_basis"],
                        "content": parsed["content"],
                        "source_attribution": parsed["source_attribution"],
                        "link": link,
                        "synced_at": datetime.now(),
                    })
                    
                    # Extract citations linking template to legislation
                    rels = extract_citations(doc_id, parsed["content"])
                    relationships.extend(rels)

                    swept_urls.add(link)
                    time.sleep(0.3)  # polite rate limit
                except Exception as e:
                    logger.error(f"Error fetching template {link}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error fetching catalog page {page_num}: {e}")
            break

    if template_records:
        df = pl.DataFrame(template_records)
        validated_df = DocumentTemplateSchema.validate(df)
        
        out_path = DATA_DIR / "modele_documente.parquet"
        if out_path.exists():
            existing = pl.read_parquet(out_path)
            combined = pl.concat([existing, validated_df]).unique(subset=["slug"])
            combined.write_parquet(out_path, compression="zstd")
            logger.info(f"Updated {out_path} (total {len(combined)} templates).")
        else:
            validated_df.write_parquet(out_path, compression="zstd")
            logger.info(f"Saved {len(validated_df)} templates to {out_path}.")

        pl.DataFrame({"url": list(swept_urls)}).write_parquet(swept_file, compression="zstd")
    else:
        logger.info("No new templates found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract Legal Document Templates from legeaz.net")
    parser.add_argument("--max-pages", type=int, default=18, help="Maximum catalog pages to crawl")
    args = parser.parse_args()
    
    run_modele_extraction(max_pages=args.max_pages)
