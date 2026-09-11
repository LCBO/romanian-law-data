import logging
from pathlib import Path
import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
FTS_DB_PATH = DATA_DIR / "fts.duckdb"


def build_fts():
    articles_parquet = DATA_DIR / "articles.parquet"
    decisions_parquet = DATA_DIR / "decisions.parquet"

    if not articles_parquet.exists() and not decisions_parquet.exists():
        logger.error("Neither articles.parquet nor decisions.parquet found to build FTS index.")
        return

    temp_db_path = DATA_DIR / "fts.duckdb.tmp"
    if temp_db_path.exists():
        temp_db_path.unlink()

    logger.info(f"Connecting to DuckDB ({temp_db_path}) and building FTS index...")
    conn = duckdb.connect(str(temp_db_path))
    conn.execute("INSTALL fts; LOAD fts;")

    # 1. Articles FTS Index
    if articles_parquet.exists():
        logger.info("Creating articles_fts source table...")
        conn.execute(f"""
            CREATE TABLE articles_fts AS 
            SELECT id, content 
            FROM read_parquet('{articles_parquet}')
            WHERE content IS NOT NULL AND trim(content) != '';
        """)
        logger.info("Building BM25 index on articles_fts.content...")
        conn.execute("""
            PRAGMA create_fts_index(
                'articles_fts', 
                'id', 
                'content', 
                stemmer='romanian', 
                stopwords='none', 
                strip_accents=1, 
                lower=1
            );
        """)
        row_count = conn.execute("SELECT count(*) FROM articles_fts").fetchone()[0]
        logger.info(f"Built articles_fts index for {row_count} articles.")

    # 2. Decisions FTS Index
    if decisions_parquet.exists():
        logger.info("Creating decisions_fts source table...")
        conn.execute(f"""
            CREATE TABLE decisions_fts AS 
            SELECT id, content 
            FROM read_parquet('{decisions_parquet}')
            WHERE content IS NOT NULL AND trim(content) != '';
        """)
        logger.info("Building BM25 index on decisions_fts.content...")
        conn.execute("""
            PRAGMA create_fts_index(
                'decisions_fts', 
                'id', 
                'content', 
                stemmer='romanian', 
                stopwords='none', 
                strip_accents=1, 
                lower=1
            );
        """)
        row_count = conn.execute("SELECT count(*) FROM decisions_fts").fetchone()[0]
        logger.info(f"Built decisions_fts index for {row_count} decisions.")

    conn.close()

    # Atomic swap
    if FTS_DB_PATH.exists():
        FTS_DB_PATH.unlink()
    temp_db_path.rename(FTS_DB_PATH)
    logger.info(f"DuckDB FTS database successfully written to {FTS_DB_PATH} ({FTS_DB_PATH.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    build_fts()
