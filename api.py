import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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
    title="Romanian Court Decisions API (ÎCCJ Jurisprudence)",
    description="High-performance legal intelligence API over Romanian High Court of Cassation and Justice rulings, paragraphs, and bidirectional citation graphs.",
    version="0.1.0",
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
    has_parquet: bool


class StatsResponse(BaseModel):
    total_decisions: int
    total_paragraphs: int
    total_citations: int


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "has_fts": FTS_DB_PATH.exists(),
        "has_parquet": (DATA_DIR / "decisions.parquet").exists(),
    }


@app.get("/api/v1/stats", response_model=StatsResponse, tags=["Metadata"])
def get_stats():
    db = get_db_connection()
    dec_file = DATA_DIR / "decisions.parquet"
    para_file = DATA_DIR / "decision_paragraphs.parquet"
    rel_file = DATA_DIR / "relationships.parquet"

    if not dec_file.exists():
        return {"total_decisions": 0, "total_paragraphs": 0, "total_citations": 0}

    try:
        dec_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{dec_file}')").fetchone()[0]
        para_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{para_file}')").fetchone()[0] if para_file.exists() else 0
        rel_cnt = db.execute(f"SELECT count(*) FROM read_parquet('{rel_file}')").fetchone()[0] if rel_file.exists() else 0
        
        return {
            "total_decisions": dec_cnt,
            "total_paragraphs": para_cnt,
            "total_citations": rel_cnt,
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"total_decisions": 0, "total_paragraphs": 0, "total_citations": 0}


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

    # Check if FTS index table exists
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
