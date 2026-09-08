import ast
import hashlib
import logging
import re
from datetime import datetime, date
from pathlib import Path
import polars as pl

from etl.schemas import DecisionSchema, ParagraphSchema, RelationshipSchema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
RAW_DATA_DIR = Path("data/raw")

CITATION_REGEXES = [
    (r"(?:art\.|articolul)\s*(\d+)\s*(?:alin\.|alineatul\s*\(\d+\))?\s*din\s*(Legea|OUG|OG|Hotărârea\s*Guvernului|Codul\s*penal|Codul\s*civil|Codul\s*fiscal|Codul\s*de\s*procedură\s*civilă|Codul\s*de\s*procedură\s*penală)(?:\s*nr\.\s*(\d+/\d+))?", "LAW"),
    (r"Decizi(?:a|ei)\s*(?:Curții\s*Constituționale|C\.C\.R\.|CCR)\s*nr\.\s*(\d+/\d+|\d+)", "CCR_DECISION"),
    (r"Decizi(?:a|ei)\s*(?:Î\.C\.C\.J\.|ÎCCJ|Înaltei\s*Curți)\s*nr\.\s*(\d+/\d+|\d+)", "ICCJ_DECISION"),
    (r"Cauza\s*([A-Z][a-zăîșțâ]+(?:\s*[a-zăîșțâ]+)*\s*(?:împotriva|v\.)\s*României)", "ECHR"),
]


def parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def extract_relationships(decision_id: int, text: str) -> list[dict]:
    rels = []
    for pattern, target_type in CITATION_REGEXES:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            citation_text = match.group(0).strip()
            rels.append({
                "source_decision_id": decision_id,
                "target_type": target_type,
                "target_citation": citation_text,
                "relationship_type": "applies",
            })
    return rels


def run_transform():
    raw_files = list(RAW_DATA_DIR.glob("*.parquet"))
    if not raw_files:
        logger.warning("No raw parquet files found to transform.")
        return

    logger.info(f"Reading {len(raw_files)} raw files...")
    raw_df = pl.concat([pl.read_parquet(f) for f in raw_files]).unique(subset=["scj_id"])

    decisions_list = []
    paragraphs_list = []
    relationships_list = []

    global_para_id = 1
    global_rel_id = 1

    for row in raw_df.iter_rows(named=True):
        scj_id = row.get("scj_id")
        # Deterministic int ID from scj_id
        dec_id = int(hashlib.sha256(str(scj_id).encode()).hexdigest()[:12], 16)
        
        meta = {}
        if row.get("metadata_json"):
            try:
                meta = ast.literal_eval(row["metadata_json"])
            except Exception:
                pass

        content = row.get("content", "") or ""
        
        # Build Decision record
        decision_rec = {
            "id": dec_id,
            "scj_id": str(scj_id),
            "decision_number": meta.get("Număr decizie") or meta.get("Numar document") or row.get("title"),
            "decision_date": parse_date(meta.get("Data deciziei") or meta.get("Data pronunțării")),
            "docket_number": meta.get("Număr dosar") or meta.get("Dosar"),
            "department": meta.get("Secția") or meta.get("Departament"),
            "document_type": meta.get("Tip document") or "Decizie",
            "matter_type": meta.get("Materie") or meta.get("Obiect"),
            "solution_type": meta.get("Tip soluție") or meta.get("Soluție"),
            "keywords": meta.get("Cuvinte cheie") or "",
            "legal_grounds": meta.get("Temei juridic") or "",
            "summary": meta.get("Sumar speță") or meta.get("Rezumat") or "",
            "content": content,
            "link": row.get("link"),
            "synced_at": datetime.utcnow(),
        }
        decisions_list.append(decision_rec)

        # Build paragraph records (split on newlines / sections)
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        for p_idx, p_text in enumerate(lines, start=1):
            paragraphs_list.append({
                "id": global_para_id,
                "decision_id": dec_id,
                "section_type": "Considerente" if p_idx < len(lines) - 2 else "Dispozitiv",
                "paragraph_number": p_idx,
                "content": p_text,
            })
            global_para_id += 1

        # Extract legal citations & relationships
        rels = extract_relationships(dec_id, content)
        for r in rels:
            r["id"] = global_rel_id
            relationships_list.append(r)
            global_rel_id += 1

    # Convert to Polars and Validate
    decisions_df = pl.DataFrame(decisions_list)
    paragraphs_df = pl.DataFrame(paragraphs_list) if paragraphs_list else pl.DataFrame({
        "id": pl.Series(dtype=pl.Int64),
        "decision_id": pl.Series(dtype=pl.Int64),
        "section_type": pl.Series(dtype=pl.Utf8),
        "paragraph_number": pl.Series(dtype=pl.Int64),
        "content": pl.Series(dtype=pl.Utf8),
    })
    relationships_df = pl.DataFrame(relationships_list) if relationships_list else pl.DataFrame({
        "id": pl.Series(dtype=pl.Int64),
        "source_decision_id": pl.Series(dtype=pl.Int64),
        "target_type": pl.Series(dtype=pl.Utf8),
        "target_citation": pl.Series(dtype=pl.Utf8),
        "relationship_type": pl.Series(dtype=pl.Utf8),
    })

    # Validate with Pandera
    logger.info("Validating schemas with Pandera...")
    validated_decisions = DecisionSchema.validate(decisions_df)
    validated_paragraphs = ParagraphSchema.validate(paragraphs_df)
    validated_relationships = RelationshipSchema.validate(relationships_df)

    # Save to compressed parquet
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    validated_decisions.write_parquet(DATA_DIR / "decisions.parquet", compression="zstd")
    validated_paragraphs.write_parquet(DATA_DIR / "decision_paragraphs.parquet", compression="zstd")
    validated_relationships.write_parquet(DATA_DIR / "relationships.parquet", compression="zstd")

    logger.info(f"Transform complete! Saved {len(validated_decisions)} decisions, {len(validated_paragraphs)} paragraphs, {len(validated_relationships)} relationships.")


if __name__ == "__main__":
    run_transform()
