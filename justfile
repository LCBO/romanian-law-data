# Default target
default:
    @just --list

# Install all dependencies with uv
sync:
    uv sync --all-extras

# Run full ETL extraction (scrapes and saves to data/raw)
extract:
    uv run python -m etl.extract

# Run ETL transform (cleans, parses citations, validates with Pandera, outputs parquet)
transform:
    uv run python -m etl.transform

# Build DuckDB BM25 Full-Text Search index
fts:
    uv run python -m etl.fts

# Run test suite
test:
    uv run pytest -v

# Run interactive DuckDB CLI on local data
duckdb:
    duckdb -init create_views.sql

# Full pipeline run
pipeline: extract transform fts test
