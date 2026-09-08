# Romanian Court Decisions Corpus (ÎCCJ Jurisprudence Data)

Zstandard-compressed Parquet dataset of the Romanian High Court of Cassation and Justice (*Înalta Curte de Casație și Justiție* - ÎCCJ) rulings, court sections, legal citations, and relationships, optimized for **[DuckDB](https://duckdb.org/)**.

Extracted and transformed from the official case law repository ([scj.ro](https://www.scj.ro/736/Cautare-jurisprudenta/)).

Rebuilt and published daily via GitHub Actions. Download from [Releases](https://github.com/LCBO/instante/releases).

---

## 📊 Tables & Data Structure

| Table | Description | Content |
| :--- | :--- | :--- |
| **`decisions.parquet`** | One row per judicial ruling | Metadata (case number, decision number, section, solution type, legal grounds, summary) + full judgment text |
| **`decision_paragraphs.parquet`** | One row per paragraph | Granular paragraph text categorized by section (*Considerente*, *Dispozitiv*) |
| **`relationships.parquet`** | Directed citation graph | Links from court decisions to laws, codes, articles, CCR decisions, and ECHR case law |

---

## 🔍 Pre-configured Subject Views (`create_views.sql`)

Load `create_views.sql` in DuckDB to enable pre-filtered domain views:

* `civil_section_1` — Secția I Civilă (Civil law, property, family, contracts)
* `civil_section_2` — Secția a II-a Civilă (Commercial law, corporate disputes, insolvency)
* `penal_section` — Secția Penală (Criminal law and criminal procedure)
* `administrative_fiscal_section` — Secția de Contencios Administrativ și Fiscal (Tax, customs, administrative litigation)
* `ril_decisions` — **Recursuri în Interesul Legii** (Binding case law unifying judicial practice)
* `hp_decisions` — **Hotărâri Prealabile** (Preliminary rulings on legal questions)
* `recent_decisions` — Rulings from the last 12 months

---

## 🚀 Usage

### 1. Download Release
```bash
mkdir -p data
gh release download -R LCBO/instante --pattern '*.parquet' --pattern 'create_views.sql' --dir data
```

### 2. Query with DuckDB (Python)
```python
import duckdb

conn = duckdb.connect()
conn.execute(open("data/create_views.sql").read())

# Query recent binding RIL rulings in criminal matters
results = conn.execute("""
    SELECT decision_number, decision_date, title, summary
    FROM ril_decisions
    WHERE matter_type ILIKE '%Penal%'
    ORDER BY decision_date DESC
    LIMIT 10
""").fetchall()

for row in results:
    print(row)
```

### 3. Full-Text Search (BM25)
Download and extract the pre-built DuckDB search index:
```bash
cat data/fts.duckdb.zst.part-* | zstd -d > data/fts.duckdb
```

```python
import duckdb

conn = duckdb.connect('data/fts.duckdb', read_only=True)

# Full-text BM25 search
res = conn.execute("""
    SELECT d.decision_number, d.department, f.score, d.content
    FROM (
        SELECT id, fts_main_decisions_fts.match_bm25(id, 'abuz in serviciu') AS score
        FROM decisions_fts
        WHERE fts_main_decisions_fts.match_bm25(id, 'abuz in serviciu') IS NOT NULL
        ORDER BY score DESC
        LIMIT 5
    ) f
    JOIN read_parquet('data/decisions.parquet') d ON f.id = d.id
    ORDER BY f.score DESC
""").fetchall()
```

---

## ⚙️ ETL Pipeline Architecture

```text
etl.extract        Sharded crawler with checkpoint cache (swept_ids.parquet)
etl.transform      HTML parser, citation extraction, Pandera schema validation
etl.fts            DuckDB FTS BM25 index builder (Romanian stemmer)
```

Run locally:
```bash
uv sync
uv run python -m etl.extract
uv run python -m etl.transform
uv run python -m etl.fts
uv run pytest
```

---

## 📜 License & Attribution

Public judicial case law from the Romanian High Court of Cassation and Justice (*Înalta Curte de Casație și Justiție*). Tooling and pipeline are open-source.
