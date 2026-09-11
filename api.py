import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from etl.citations import extract_citations

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Storage configuration
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
FTS_DB_PATH = DATA_DIR / "fts.duckdb"
VIEWS_SQL_PATH = Path(os.getenv("VIEWS_SQL_PATH", "create_views.sql"))

# Cloudflare R2 / S3 Configuration
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "https://f85d069965caebe0bd1d0ae16e3e8afc.r2.cloudflarestorage.com")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "f0fb17605c1a39679b10933b1639756e")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "7761a3c7d440baff2ebf17fab4b3f80b450012b28e3866519eee3abc48460ef6")
R2_BUCKET = os.getenv("R2_BUCKET", "lawchat-documents")
R2_PREFIX = os.getenv("R2_PREFIX", "legislatie").strip("/")
FILE_STORAGE_PROVIDER = os.getenv("FILE_STORAGE_PROVIDER", "r2").lower()
USE_R2 = FILE_STORAGE_PROVIDER in ("r2", "s3", "cloudflare", "cloudflare_r2")

TABLE_NAMES = [
    "documents",
    "articles",
    "paragraphs",
    "decisions",
    "decision_paragraphs",
    "relationships",
    "ccr_decisions",
    "modele_documente",
    "dictionar_juridic",
]


class SafeResult:
    def __init__(self, pl_data, rows):
        self._pl_data = pl_data
        self._rows = rows

    def pl(self):
        class PlWrapper:
            def __init__(self, data):
                self.data = data
            def to_dicts(self):
                return self.data
        return PlWrapper(self._pl_data)

    def fetchone(self):
        return self._rows[0] if self._rows else (0,)

    def fetchall(self):
        return self._rows


class ThreadSafeDuckDB:
    _lock = threading.Lock()

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn

    def execute(self, query: str, parameters: Any = None) -> SafeResult:
        with self._lock:
            if parameters is not None:
                cur = self._conn.execute(query, parameters)
            else:
                cur = self._conn.execute(query)
            
            try:
                pl_data = cur.pl().to_dicts()
                rows = [tuple(d.values()) for d in pl_data]
            except Exception:
                rows = cur.fetchall()
                pl_data = []
            
            return SafeResult(pl_data, rows)


_root_conn: duckdb.DuckDBPyConnection | None = None
_thread_safe_db: ThreadSafeDuckDB | None = None
_conn_lock = threading.Lock()


def _init_root_connection() -> ThreadSafeDuckDB:
    global _root_conn, _thread_safe_db
    with _conn_lock:
        if _thread_safe_db is not None:
            return _thread_safe_db
        logger.info("Initializing DuckDB connection (Storage: %s)...", "Cloudflare R2 Native" if USE_R2 else "Local Disk")
        conn = duckdb.connect(":memory:")

        if USE_R2 and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY:
            try:
                conn.execute("INSTALL httpfs; LOAD httpfs;")
                endpoint_host = R2_ENDPOINT.replace("https://", "").replace("http://", "").strip("/")
                conn.execute(f"""
                    SET s3_region='auto';
                    SET s3_endpoint='{endpoint_host}';
                    SET s3_access_key_id='{R2_ACCESS_KEY_ID}';
                    SET s3_secret_access_key='{R2_SECRET_ACCESS_KEY}';
                    SET s3_url_style='path';
                """)
                logger.info(f"DuckDB httpfs configured for Cloudflare R2 bucket: s3://{R2_BUCKET}/{R2_PREFIX}")
            except Exception as e:
                logger.error(f"Failed to initialize DuckDB R2 httpfs: {e}")

        for table in TABLE_NAMES:
            r2_uri = f"s3://{R2_BUCKET}/{R2_PREFIX}/{table}.parquet"
            local_file = DATA_DIR / f"{table}.parquet"
            created = False

            if USE_R2 and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY:
                try:
                    conn.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{r2_uri}');")
                    conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                    logger.info(f"Created R2 View: {table} -> {r2_uri}")
                    created = True
                except Exception as ve:
                    logger.warning(f"Could not verify R2 view for {table}: {ve}")

            if not created and local_file.exists():
                try:
                    conn.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{local_file}');")
                    logger.info(f"Created Local View: {table} -> {local_file}")
                    created = True
                except Exception as le:
                    logger.warning(f"Could not create local view for {table}: {le}")

        # Attach local FTS database if exists
        if FTS_DB_PATH.exists():
            try:
                conn.execute(f"ATTACH '{FTS_DB_PATH}' AS fts_db (READ_ONLY);")
                logger.info(f"Attached FTS database from {FTS_DB_PATH}")
            except Exception as e:
                logger.warning(f"Could not attach FTS database: {e}")

        _root_conn = conn
        _thread_safe_db = ThreadSafeDuckDB(_root_conn)
        return _thread_safe_db



def _table_exists(db: Any, table_name: str) -> bool:
    try:
        db.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
        return True
    except Exception:
        return False


def _get_table_count(db: Any, table_name: str) -> int:
    try:
        row = db.execute(f"SELECT count(*) FROM {table_name}").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0

def get_db_connection() -> ThreadSafeDuckDB:
    global _thread_safe_db
    if _thread_safe_db is None:
        _init_root_connection()
    return _thread_safe_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_root_connection()
    yield
    global _root_conn, _thread_safe_db
    if _root_conn:
        _root_conn.close()
        _root_conn = None
        _thread_safe_db = None


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
    has_documents: bool


class StatsResponse(BaseModel):
    storage_provider: str = "local_disk"
    total_decisions: int = 0
    total_decision_paragraphs: int = 0
    total_paragraphs: int = 0
    total_articles: int = 0
    total_citations: int = 0
    total_modele: int = 0
    total_dictionar_terms: int = 0
    total_ccr_decisions: int = 0
    total_documents: int = 0


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "has_fts": FTS_DB_PATH.exists(),
        "has_decisions": (DATA_DIR / "decisions.parquet").exists(),
        "has_modele": (DATA_DIR / "modele_documente.parquet").exists(),
        "has_dictionar": (DATA_DIR / "dictionar_juridic.parquet").exists(),
        "has_ccr": (DATA_DIR / "ccr_decisions.parquet").exists(),
        "has_documents": (DATA_DIR / "documents.parquet").exists(),
    }


@app.get("/api/v1/stats", response_model=StatsResponse, tags=["Metadata"])
def get_stats():
    db = get_db_connection()
    return {
        "storage_provider": "cloudflare_r2" if USE_R2 else "local_disk",
        "total_decisions": _get_table_count(db, "decisions"),
        "total_decision_paragraphs": _get_table_count(db, "decision_paragraphs"),
        "total_paragraphs": _get_table_count(db, "paragraphs"),
        "total_articles": _get_table_count(db, "articles"),
        "total_citations": _get_table_count(db, "relationships"),
        "total_modele": _get_table_count(db, "modele_documente"),
        "total_dictionar_terms": _get_table_count(db, "dictionar_juridic"),
        "total_ccr_decisions": _get_table_count(db, "ccr_decisions"),
        "total_documents": _get_table_count(db, "documents"),
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

    count_params = list(params)
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
    total = db.execute(f"SELECT count(*) FROM read_parquet('{dec_file}') WHERE {where_clause}", count_params).fetchone()[0]
    return {"total": total, "count": len(results), "page": page, "limit": limit, "data": results}


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

    count_params = list(params)
    query = f"""
        SELECT id, slug, title, category, legal_basis, source_attribution, link, content
        FROM read_parquet('{mod_file}')
        WHERE {where_clause}
        ORDER BY title ASC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    total = db.execute(f"SELECT count(*) FROM read_parquet('{mod_file}') WHERE {where_clause}", count_params).fetchone()[0]
    return {"total": total, "count": len(results), "page": page, "limit": limit, "data": results}


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

    count_params = list(params)
    query = f"""
        SELECT id, slug, term, letter, definition, link
        FROM read_parquet('{dict_file}')
        WHERE {where_clause}
        ORDER BY term ASC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    total = db.execute(f"SELECT count(*) FROM read_parquet('{dict_file}') WHERE {where_clause}", count_params).fetchone()[0]
    return {"total": total, "count": len(results), "page": page, "limit": limit, "data": results}


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

    count_params = list(params)
    query = f"""
        SELECT id, slug, title, act_type, act_number, act_year, decision_date, category, publication_notice, summary, pdf_url
        FROM read_parquet('{ccr_file}')
        WHERE {where_clause}
        ORDER BY decision_date DESC NULLS LAST, id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    total = db.execute(f"SELECT count(*) FROM read_parquet('{ccr_file}') WHERE {where_clause}", count_params).fetchone()[0]
    return {"total": total, "count": len(results), "page": page, "limit": limit, "data": results}


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


# =============================================================================
# Primary Legislation Endpoints (Corpus Legislativ: Legi, OUG, HG, Decrete)
# =============================================================================

@app.get("/api/v1/documents", tags=["Legislație Primară"])
def list_legislation_documents(
    type: str | None = Query(None, description="e.g. 'LEGE', 'ORDONANȚĂ DE URGENȚĂ', 'HOTĂRÂRE', 'DECIZIE'"),
    issuer: str | None = Query(None, description="e.g. 'PARLAMENTUL', 'GUVERNUL', 'CURTEA CONSTITUȚIONALĂ'"),
    document_number: str | None = Query(None, description="e.g. '62', '287', '562'"),
    year: int | None = Query(None, description="Year adopted (e.g. 2024, 2025)"),
    q: str | None = Query(None, description="Search keyword in title or citation"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=100),
    page_size: int | None = Query(None, ge=1, le=100),
):
    """List and filter primary Romanian legislation from documents (over 251,000 acts)."""
    eff_limit = page_size or limit
    db = get_db_connection()
    if not _table_exists(db, "documents"):
        return {"total": 0, "count": 0, "page": page, "limit": eff_limit, "data": []}

    conditions = ["1=1"]
    params = []

    if type and type != "ALL":
        conditions.append("type ILIKE ?")
        params.append(f"%{type}%")
    if issuer:
        conditions.append("issuer ILIKE ?")
        params.append(f"%{issuer}%")
    if document_number:
        conditions.append("document_number = ?")
        params.append(str(document_number))
    if year:
        conditions.append("EXTRACT(year FROM adopted_at) = ?")
        params.append(year)
    if q:
        conditions.append("(title ILIKE ? OR document_citation ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    where_clause = " AND ".join(conditions)
    offset = (page - 1) * eff_limit

    count_params = list(params)
    query = f"""
        SELECT id, type, document_number, document_citation, issuer, title, adopted_at, published_at, effective_at, gazette_number, status, link
        FROM documents
        WHERE {where_clause}
        ORDER BY adopted_at DESC NULLS LAST, id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([eff_limit, offset])

    results = db.execute(query, params).pl().to_dicts()
    if where_clause == "1=1":
        total = _get_table_count(db, "documents")
    else:
        total = db.execute(f"SELECT count(*) FROM documents WHERE {where_clause}", count_params).fetchone()[0]

    return {"total": total, "count": len(results), "page": page, "limit": eff_limit, "data": results}


@app.get("/api/v1/documents/{document_id}", tags=["Legislație Primară"])
def get_legislation_document(document_id: int):
    """Retrieve full text and metadata for a specific primary legislative act."""
    db = get_db_connection()
    doc_file = DATA_DIR / "documents.parquet"
    if not doc_file.exists():
        raise HTTPException(status_code=404, detail="Legislation dataset not loaded")

    results = db.execute(f"SELECT * FROM read_parquet('{doc_file}') WHERE id = ?", [document_id]).pl().to_dicts()
    if not results:
        raise HTTPException(status_code=404, detail="Document not found")

    return results[0]


@app.get("/api/v1/documents/{document_id}/articles", tags=["Legislație Primară"])
def get_document_articles(document_id: int):
    """Retrieve all structured articles for a specific legislative act."""
    db = get_db_connection()
    art_file = DATA_DIR / "articles.parquet"
    if not art_file.exists():
        raise HTTPException(status_code=404, detail="Articles dataset not loaded")

    results = db.execute(f"""
        SELECT id, article_number, article_variant, article_citation, content
        FROM read_parquet('{art_file}')
        WHERE document_id = ?
        ORDER BY id ASC
    """, [document_id]).pl().to_dicts()

    return {"document_id": document_id, "count": len(results), "articles": results}



