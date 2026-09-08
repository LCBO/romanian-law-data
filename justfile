# Default target
default:
    @just --list

# Install all dependencies with uv
sync:
    uv sync --all-extras

# Run full ETL extraction from live scj.ro portal
extract:
    uv run python -m etl.extract

# Ingest from official open data dump zip
extract-dump path:
    uv run python -m etl.extract --dump-path {{path}}

# Extract legal document templates from legeaz.net
extract-modele pages="18":
    uv run python -m etl.extract_modele --max-pages {{pages}}

# Extract legal dictionary definitions from legeaz.net
extract-dictionar pages="104":
    uv run python -m etl.extract_dictionar --max-pages {{pages}}

# Run ETL transform (cleans, parses citations, validates with Pandera, outputs parquet)
transform:
    uv run python -m etl.transform

# Build DuckDB BM25 Full-Text Search index
fts:
    uv run python -m etl.fts

# Sync parquet dataset and FTS to Cloudflare R2
sync-r2:
    uv run python -m etl.r2_sync

# Run FastAPI REST API server locally with reload
serve port="8000":
    uv run uvicorn api:app --host 0.0.0.0 --port {{port}} --reload

# Run interactive DuckDB CLI with pre-loaded views
duckdb:
    duckdb -init create_views.sql

# Run pytest test suite
test:
    uv run pytest -v

# Full end-to-end pipeline run
pipeline: extract transform fts test
