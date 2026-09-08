import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from etl.citations import extract_citations


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
FTS_DB_PATH = DATA_DIR / "fts.duckdb"
VIEWS_SQL_PATH = Path(os.getenv("VIEWS_SQL_PATH", "create_views.sql"))

conn: duckdb.DuckDBPyConnection | None = None


def get_db_connection() -> duckdb.DuckDBPyConnection:
    global conn
    if conn is None:
        logger.info("Initializing DuckDB connection...")
        conn = duckdb.connect(":memory:")

        # Attach FTS database if it exists
        if FTS_DB_PATH.exists():
            try:
                conn.execute(f"ATTACH '{FTS_DB_PATH}' AS fts_db (READ_ONLY);")
                logger.info(f"Attached FTS database from {FTS_DB_PATH}")
            except Exception as e:
                logger.warning(f"Could not attach FTS database: {e}")

        # Load SQL views if parquet files exist
        if VIEWS_SQL_PATH.exists() and (DATA_DIR / "decisions.parquet").exists():
            try:
                logger.info(f"Loading SQL views from {VIEWS_SQL_PATH}")
                conn.execute(open(VIEWS_SQL_PATH).read())
            except Exception as e:
                logger.warning(f"Failed to load views: {e}")
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_db_connection()
    yield
    global conn
    if conn:
        conn.close()
        conn = None


app = FastAPI(
    title="Romanian Judicial Case Law, Legal Templates & Dictionary API",
    description="High-performance legal intelligence API over Romanian High Court rulings, bidirectional legislation liaison graph, document templates, and legal dictionary definitions from legeaz.net.",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    has_fts: bool
    has_decisions: bool
    has_modele: bool
    has_dictionar: bool
    has_ccr: bool


class StatsResponse(BaseModel):
    total_decisions: int
    total_paragraphs: int
    total_citations: int
    total_modele: int
    total_dictionar_terms: int
    total_ccr_decisions: int


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "has_fts": FTS_DB_PATH.exists(),
        "has_decisions": (DATA_DIR / "decisions.parquet").exists(),
        "has_modele": (DATA_DIR / "modele_documente.parquet").exists(),
        "has_dictionar": (DATA_DIR / "dictionar_juridic.parquet").exists(),
        "has_ccr": (DATA_DIR / "ccr_decisions.parquet").exists(),
    }


@app.get("/api/v1/stats", response_model=StatsResponse, tags=["Metadata"])
def get_stats():
    db = get_db_connection()
    dec_file = DATA_DIR / "decisions.parquet"
    para_file = DATA_DIR / "decision_paragraphs.parquet"
    rel_file = DATA_DIR / "relationships.parquet"
    mod_file = DATA_DIR / "modele_documente.parquet"
    dict_file = DATA_DIR / "dictionar_juridic.parquet"
    ccr_file = DATA_DIR / "ccr_decisions.parquet"

    dec_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{dec_file}')").fetchone()[0] if dec_file.exists() else 0
    para_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{para_file}')").fetchone()[0] if para_file.exists() else 0
    rel_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{rel_file}')").fetchone()[0] if rel_file.exists() else 0
    mod_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{mod_file}')").fetchone()[0] if mod_file.exists() else 0
    dict_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{dict_file}')").fetchone()[0] if dict_file.exists() else 0
    ccr_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{ccr_file}')").fetchone()[0] if ccr_file.exists() else 0

    return {
        "total_decisions": dec_cnt,
        "total_paragraphs": para_cnt,
        "total_citations": rel_cnt,
        "total_modele": mod_cnt,
        "total_dictionar_terms": dict_cnt,
        "total_ccr_decisions": ccr_cnt,
    }



# =============================================================================
# Court Decisions Endpoints
# =============================================================================

@app.get("/api/v1/decisions", tags=["Decisions"])
def list_decisions(
    department: str | None = Query(None, description="Filter by section (e.g. 'Secția Penală')"),
    matter_type: str | None = Query(None, description="Filter by legal matter (e.g. 'Penal', 'Civil')"),
    solution_type: str | None = Query(None, description="Filter by solution (e.g. 'Admis', 'Respins')"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List court decisions with multi-criteria filtering."""
    db = get_db_connection()
    dec_file = DATA_DIR / "decisions.parquet"
    if not dec_file.exists():
        return {"count": 0, "page": page, "limit": limit, "data": []}

    conditions = ["1=1"]
    params = []

    if department:
        conditions.append("department ILIKE ?")
        params.append(f"%{department}%")
    if matter_type:
        conditions.append("matter_type ILIKE ?")
        params.append(f"%{matter_type}%")
    if solution_type:
        conditions.append("solution_type ILIKE ?")
        params.append(f"%{solution_type}%")
    if from_date:
        conditions.append("decision_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("decision_date <= ?")
        params.append(to_date)

    where_clause = " AND ".join(conditions)
    offset = (page - 1) * limit

    query = f"""
        SELECT 
            id, scj_id, decision_number, decision_date, docket_number,
            department, document_type, matter_type, solution_type,
            keywords, summary, link
        FROM read_parquet('{dec_file}')
        WHERE {where_clause}
        ORDER BY decision_date DESC NULLS LAST, id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    return {"count": len(results), "page": page, "limit": limit, "data": results}


@app.get("/api/v1/decisions/{decision_id}", tags=["Decisions"])
def get_decision_detail(decision_id: int):
    """Retrieve full details, full text, segmented paragraphs, and citations of a single ruling."""
    db = get_db_connection()
    dec_file = DATA_DIR / "decisions.parquet"
    para_file = DATA_DIR / "decision_paragraphs.parquet"
    rel_file = DATA_DIR / "relationships.parquet"

    if not dec_file.exists():
        raise HTTPException(status_code=404, detail="Dataset not loaded")

    decision = db.execute(f"SELECT * FROM read_parquet('{dec_file}') WHERE id = ?", [decision_id]).pl().to_dicts()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")

    paragraphs = []
    if para_file.exists():
        paragraphs = db.execute(
            f"SELECT paragraph_number, section_type, content FROM read_parquet('{para_file}') WHERE decision_id = ? ORDER BY paragraph_number ASC",
            [decision_id],
        ).pl().to_dicts()

    citations = []
    if rel_file.exists():
        citations = db.execute(
            f"SELECT canonical_citation, target_type, act_type, act_number, act_year, article_number, paragraph_number, relationship_type FROM read_parquet('{rel_file}') WHERE source_decision_id = ?",
            [decision_id],
        ).pl().to_dicts()

    result = decision[0]
    result["paragraphs"] = paragraphs
    result["citations"] = citations
    return result


@app.get("/api/v1/decisions/{decision_id}/citations", tags=["Legislation Liaison"])
def get_decision_citations(decision_id: int):
    """Decision -> Legislation: Get all laws, codes, and articles referenced in a given decision."""
    db = get_db_connection()
    rel_file = DATA_DIR / "relationships.parquet"
    if not rel_file.exists():
        return {"decision_id": decision_id, "citations": []}

    citations = db.execute(
        f"""
        SELECT 
            canonical_citation, target_type, act_type, act_number, 
            act_year, article_number, paragraph_number, annex, chapter, relationship_type
        FROM read_parquet('{rel_file}')
        WHERE source_decision_id = ?
        """,
        [decision_id],
    ).pl().to_dicts()

    return {"decision_id": decision_id, "count": len(citations), "citations": citations}


@app.get("/api/v1/legislation/search", tags=["Legislation Liaison"])
def search_decisions_by_legislation(
    act_type: str | None = Query(None, description="e.g. 'OUG', 'LEGE', 'COD', 'ORDIN'"),
    act_number: str | None = Query(None, description="e.g. '62', '153', 'Codul Civil'"),
    act_year: int | None = Query(None, description="e.g. 2024, 2017"),
    article_number: str | None = Query(None, description="e.g. '2', '13', '188'"),
    canonical_query: str | None = Query(None, description="e.g. 'OUG 62/2024' or 'Codul Penal'"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Legislation -> Decisions: Find all court rulings that cite, interpret, or apply a specific law or article.
    """
    db = get_db_connection()
    dec_file = DATA_DIR / "decisions.parquet"
    rel_file = DATA_DIR / "relationships.parquet"

    if not dec_file.exists() or not rel_file.exists():
        return {"count": 0, "data": []}

    conditions = ["1=1"]
    params = []

    if act_type:
        conditions.append("r.act_type = ?")
        params.append(act_type.upper())
    if act_number:
        conditions.append("r.act_number ILIKE ?")
        params.append(f"%{act_number}%")
    if act_year:
        conditions.append("r.act_year = ?")
        params.append(act_year)
    if article_number:
        conditions.append("r.article_number = ?")
        params.append(str(article_number))
    if canonical_query:
        conditions.append("r.canonical_citation ILIKE ?")
        params.append(f"%{canonical_query}%")

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT 
            r.canonical_citation,
            r.act_type,
            r.act_number,
            r.act_year,
            r.article_number,
            r.paragraph_number,
            d.id AS decision_id,
            d.decision_number,
            d.decision_date,
            d.docket_number,
            d.department,
            d.solution_type,
            d.summary,
            d.link
        FROM read_parquet('{rel_file}') r
        JOIN read_parquet('{dec_file}') d ON r.source_decision_id = d.id
        WHERE {where_clause}
        ORDER BY d.decision_date DESC NULLS LAST
        LIMIT ?
    """
    params.append(limit)

    results = db.execute(query, params).pl().to_dicts()
    return {"count": len(results), "data": results}


@app.get("/api/v1/search/fts", tags=["Search"])
def full_text_search(
    q: str = Query(..., min_length=2, description="Search query or legal terms"),
    department: str | None = Query(None, description="Optional court section filter"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Full-Text Search across the corpus using DuckDB FTS or fast columnar ILIKE.
    """
    db = get_db_connection()
    dec_file = DATA_DIR / "decisions.parquet"

    if not dec_file.exists():
        return {"query": q, "mode": "none", "count": 0, "data": []}

    has_fts_table = False
    try:
        tables = [t[0] for t in db.execute("SHOW TABLES").fetchall()]
        has_fts_table = "decisions_fts" in tables or "fts_db.decisions_fts" in tables
    except Exception:
        pass

    if has_fts_table:
        dept_clause = ""
        params = [q, q]
        if department:
            dept_clause = "AND d.department ILIKE ?"
            params.append(f"%{department}%")
        params.append(limit)

        query = f"""
            WITH fts_results AS (
                SELECT id, match_bm25(id, ?) AS score
                FROM decisions_fts
                WHERE match_bm25(id, ?) IS NOT NULL
                ORDER BY score DESC
            )
            SELECT 
                round(f.score, 2) AS relevance_score,
                d.id,
                d.decision_number,
                d.decision_date,
                d.docket_number,
                d.department,
                d.matter_type,
                d.solution_type,
                d.summary,
                d.link,
                d.content
            FROM fts_results f
            JOIN read_parquet('{dec_file}') d ON f.id = d.id
            WHERE 1=1 {dept_clause}
            ORDER BY f.score DESC
            LIMIT ?
        """
        try:
            results = db.execute(query, params).pl().to_dicts()
            return {"query": q, "mode": "bm25_fts", "count": len(results), "data": results}
        except Exception as e:
            logger.warning(f"FTS Query failed, falling back to ILIKE: {e}")

    # Fallback to Columnar ILIKE
    dept_clause = ""
    params = [f"%{q}%"]
    if department:
        dept_clause = "AND department ILIKE ?"
        params.append(f"%{department}%")
    params.append(limit)

    res = db.execute(f"""
        SELECT id, decision_number, decision_date, department, matter_type, summary, content, link
        FROM read_parquet('{dec_file}')
        WHERE content ILIKE ? {dept_clause}
        ORDER BY decision_date DESC NULLS LAST
        LIMIT ?
    """, params).pl().to_dicts()

    return {"query": q, "mode": "columnar_search", "count": len(res), "data": res}


# =============================================================================
# Document Templates Endpoints (Modele de Acte / Contracte)
# =============================================================================

@app.get("/api/v1/modele/categories", tags=["Modele de Documente"])
def list_template_categories():
    """List all available legal document template categories and their counts."""
    db = get_db_connection()
    mod_file = DATA_DIR / "modele_documente.parquet"
    if not mod_file.exists():
        return {"categories": []}

    results = db.execute(f"""
        SELECT category, count(*) as count
        FROM read_parquet('{mod_file}')
        GROUP BY category
        ORDER BY count DESC
    """).pl().to_dicts()

    return {"categories": results}


@app.get("/api/v1/modele", tags=["Modele de Documente"])
def list_document_templates(
    category: str | None = Query(None, description="e.g. 'Contracte', 'Dreptul Familiei', 'Succesiuni & Testamente'"),
    q: str | None = Query(None, description="Search term in title or content"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List and search legal document templates with category filtering."""
    db = get_db_connection()
    mod_file = DATA_DIR / "modele_documente.parquet"
    if not mod_file.exists():
        return {"count": 0, "page": page, "limit": limit, "data": []}

    conditions = ["1=1"]
    params = []

    if category:
        conditions.append("category ILIKE ?")
        params.append(f"%{category}%")
    if q:
        conditions.append("(title ILIKE ? OR content ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    where_clause = " AND ".join(conditions)
    offset = (page - 1) * limit

    query = f"""
        SELECT id, slug, title, category, legal_basis, source_attribution, link, content
        FROM read_parquet('{mod_file}')
        WHERE {where_clause}
        ORDER BY title ASC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    return {"count": len(results), "page": page, "limit": limit, "data": results}


@app.get("/api/v1/modele/{template_id}", tags=["Modele de Documente"])
def get_document_template(template_id: int):
    """Retrieve full text, legal basis, fillable blanks, and source for a specific document template."""
    db = get_db_connection()
    mod_file = DATA_DIR / "modele_documente.parquet"
    if not mod_file.exists():
        raise HTTPException(status_code=404, detail="Templates dataset not loaded")

    results = db.execute(f"SELECT * FROM read_parquet('{mod_file}') WHERE id = ?", [template_id]).pl().to_dicts()
    if not results:
        raise HTTPException(status_code=404, detail="Document template not found")

    return results[0]


# =============================================================================
# Legal Dictionary Endpoints (Dicționar Juridic DEX)
# =============================================================================

@app.get("/api/v1/dictionar/letters", tags=["Dicționar Juridic"])
def list_dictionary_letters():
    """List all available letters in the legal dictionary and term counts."""
    db = get_db_connection()
    dict_file = DATA_DIR / "dictionar_juridic.parquet"
    if not dict_file.exists():
        return {"letters": []}

    results = db.execute(f"""
        SELECT letter, count(*) as count
        FROM read_parquet('{dict_file}')
        GROUP BY letter
        ORDER BY letter ASC
    """).pl().to_dicts()

    return {"letters": results}


@app.get("/api/v1/dictionar", tags=["Dicționar Juridic"])
def list_dictionary_terms(
    letter: str | None = Query(None, max_length=2, description="Alphabet letter filter (e.g. 'A', 'B', 'C')"),
    q: str | None = Query(None, description="Search term in name or definition"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """List and search legal dictionary definitions."""
    db = get_db_connection()
    dict_file = DATA_DIR / "dictionar_juridic.parquet"
    if not dict_file.exists():
        return {"count": 0, "page": page, "limit": limit, "data": []}

    conditions = ["1=1"]
    params = []

    if letter:
        conditions.append("letter = ?")
        params.append(letter.upper())
    if q:
        conditions.append("(term ILIKE ? OR definition ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    where_clause = " AND ".join(conditions)
    offset = (page - 1) * limit

    query = f"""
        SELECT id, slug, term, letter, definition, link
        FROM read_parquet('{dict_file}')
        WHERE {where_clause}
        ORDER BY term ASC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    return {"count": len(results), "page": page, "limit": limit, "data": results}


@app.get("/api/v1/dictionar/{term_id_or_slug}", tags=["Dicționar Juridic"])
def get_dictionary_term_detail(term_id_or_slug: str):
    """Retrieve full definition for a specific legal dictionary term."""
    db = get_db_connection()
    dict_file = DATA_DIR / "dictionar_juridic.parquet"
    if not dict_file.exists():
        raise HTTPException(status_code=404, detail="Dictionary dataset not loaded")

    if term_id_or_slug.isdigit():
        results = db.execute(f"SELECT * FROM read_parquet('{dict_file}') WHERE id = ?", [int(term_id_or_slug)]).pl().to_dicts()
    else:
        results = db.execute(f"SELECT * FROM read_parquet('{dict_file}') WHERE slug = ?", [term_id_or_slug]).pl().to_dicts()

    if not results:
        raise HTTPException(status_code=404, detail="Dictionary term not found")

    return results[0]


# =============================================================================
# Curtea Constituțională a României (CCR) Endpoints
# =============================================================================

@app.get("/api/v1/ccr/categories", tags=["Curtea Constituțională (CCR)"])
def list_ccr_categories():
    """List all CCR decision categories and count of decisions per category."""
    db = get_db_connection()
    ccr_file = DATA_DIR / "ccr_decisions.parquet"
    if not ccr_file.exists():
        return {"categories": []}

    results = db.execute(f"""
        SELECT category, count(*) as count
        FROM read_parquet('{ccr_file}')
        GROUP BY category
        ORDER BY count DESC
    """).pl().to_dicts()

    return {"categories": results}


@app.get("/api/v1/ccr", tags=["Curtea Constituțională (CCR)"])
def list_ccr_decisions(
    category: str | None = Query(None, description="e.g. 'Decizii de admitere', 'Decizii relevante', 'Hotărâri de admitere'"),
    act_type: str | None = Query(None, description="e.g. 'DECIZIE', 'HOTĂRÂRE', 'AVIZ CONSULTATIV'"),
    year: int | None = Query(None, description="Filter by decision year (e.g. 2026, 2025)"),
    q: str | None = Query(None, description="Search term in title, summary, or publication notice"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List and filter decisions, rulings, and advisory opinions from the Constitutional Court of Romania (CCR)."""
    db = get_db_connection()
    ccr_file = DATA_DIR / "ccr_decisions.parquet"
    if not ccr_file.exists():
        return {"count": 0, "page": page, "limit": limit, "data": []}

    conditions = ["1=1"]
    params = []

    if category:
        conditions.append("category ILIKE ?")
        params.append(f"%{category}%")
    if act_type:
        conditions.append("act_type = ?")
        params.append(act_type.upper())
    if year:
        conditions.append("act_year = ?")
        params.append(year)
    if q:
        conditions.append("(title ILIKE ? OR summary ILIKE ? OR publication_notice ILIKE ? OR content ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])

    where_clause = " AND ".join(conditions)
    offset = (page - 1) * limit

    query = f"""
        SELECT id, slug, title, act_type, act_number, act_year, decision_date, category, publication_notice, summary, pdf_url
        FROM read_parquet('{ccr_file}')
        WHERE {where_clause}
        ORDER BY decision_date DESC NULLS LAST, id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    return {"count": len(results), "page": page, "limit": limit, "data": results}


@app.get("/api/v1/ccr/{decision_id_or_slug}", tags=["Curtea Constituțională (CCR)"])
def get_ccr_decision_detail(decision_id_or_slug: str):
    """Retrieve full text, PDF download link, metadata, and extracted citations for a CCR ruling."""
    db = get_db_connection()
    ccr_file = DATA_DIR / "ccr_decisions.parquet"
    if not ccr_file.exists():
        raise HTTPException(status_code=404, detail="CCR dataset not loaded")

    if decision_id_or_slug.isdigit():
        results = db.execute(f"SELECT * FROM read_parquet('{ccr_file}') WHERE id = ?", [int(decision_id_or_slug)]).pl().to_dicts()
    else:
        results = db.execute(f"SELECT * FROM read_parquet('{ccr_file}') WHERE slug = ?", [decision_id_or_slug]).pl().to_dicts()

    if not results:
        raise HTTPException(status_code=404, detail="CCR decision not found")

    decision = results[0]
    content = decision.get("content", "")
    citations = extract_citations(decision["id"], content) if content else []
    decision["citations"] = citations
    return decision


