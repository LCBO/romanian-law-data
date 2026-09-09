import logging
from pathlib import Path
import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
FTS_DB_PATH = DATA_DIR / "fts.duckdb"


def build_fts():
    decisions_parquet = DATA_DIR / "decisions.parquet"
    if not decisions_parquet.exists():
        logger.error(f"{decisions_parquet} does not exist. Run transform first.")
        return

    if FTS_DB_PATH.exists():
        FTS_DB_PATH.unlink()

    logger.info("Connecting to DuckDB and building FTS index...")
    conn = duckdb.connect(str(FTS_DB_PATH))
    
    # Install and load Full Text Search extension
    conn.execute("INSTALL fts; LOAD fts;")

    # Create FTS source table
    conn.execute("""
        CREATE TABLE decisions_fts AS 
        SELECT id, content 
        FROM read_parquet('data/decisions.parquet')
        WHERE content IS NOT NULL AND trim(content) != '';
    """)

    # Create BM25 Index
    logger.info("Creating BM25 index on decisions_fts.content...")
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
    logger.info(f"FTS index built successfully for {row_count} decisions at {FTS_DB_PATH}!")
    conn.close()


if __name__ == "__main__":
    build_fts()
