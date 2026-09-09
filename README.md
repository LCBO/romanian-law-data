# ⚖️ Romanian Legal Corpus & Intelligence API (*Instanțe, CCR & Legislație*)

High-performance, Zstandard-compressed Parquet datasets, DuckDB analytical engine, and FastAPI REST service for Romanian High Court judicial rulings (*Înalta Curte de Casație și Justiție* - ÎCCJ), Constitutional Court decisions (*Curtea Constituțională a României* - CCR), primary Romanian legislation (251k+ acts), bidirectional legislation citation graph, legal document templates (*Modele de Acte*), and legal dictionary definitions (*Dicționar Juridic*).

---

## 📊 Datasets & Schema Structure

All datasets are compressed with **Zstandard** (`zstd`) and structured for zero-copy streaming into **[DuckDB](https://duckdb.org/)**, **[Polars](https://pola.rs/)**, and **[PyArrow](https://arrow.apache.org/docs/python/)**.

- **`data/documents.parquet`** (1.06 GB): Full primary Romanian legislation corpus (over 251,000 laws, emergency ordinances, government decisions, decrees, and published CCR decisions).
- **`data/articles.parquet`** (874 MB): 1,579,000+ structured legal articles linked by `document_id`.
- **`data/paragraphs.parquet`** (891 MB): 3,198,000+ granular paragraph rows for precision vector retrieval and citations.
- **`data/decisions.parquet`**: High Court (*ÎCCJ*) judicial rulings with docket number, court section, decision date, legal matter, solution type, summary, and clean text.
- **`data/decision_paragraphs.parquet`**: Granular paragraph text categorized by section (*Considerente*, *Dispozitiv*) for precise citation referencing.
- **`data/relationships.parquet`**: Directed citation graph linking decisions to acts (*OUG*, *Lege*, *Codul Civil*, *Codul Penal*), articles, paragraphs, and CCR/ECHR rulings.
- **`data/modele_documente.parquet`**: 848 categorized legal templates (Cereri, Contracte, Plângeri, Acțiuni, Notificări, etc.) with legal bases and fillable fields.
- **`data/dictionar_juridic.parquet`**: 5,181 legal definitions indexed alphabetically (A–Z) from *legeaz.net/dictionar* with automated Romanian legislation citation extraction.
- **`data/ccr_decisions.parquet`**: Constitutional Court rulings, unconstitutionality admissions, and opinions with full text decompressed from official PDFs.
- **`data/fts.duckdb`**: Pre-indexed DuckDB BM25 Full-Text Search database.

---

## ☁️ Cloudflare R2 Object Storage (`s3://lawchat-documents/legislatie/`)

All datasets and database snapshots are automatically uploaded and synchronized to **Cloudflare R2** under the **`legislatie/`** folder:

- **R2 Bucket**: `lawchat-documents`
- **Prefix / Subfolder**: `legislatie`
- **S3 Endpoint**: `https://<account-id>.r2.cloudflarestorage.com`

### Environment Configuration (`.env`)
```bash
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<your_r2_access_key_id>
R2_SECRET_ACCESS_KEY=<your_r2_secret_access_key>
R2_BUCKET=lawchat-documents
R2_PREFIX=legislatie
```

### Direct Streaming from DuckDB via S3/R2
```sql
INSTALL httpfs;
LOAD httpfs;

SET s3_endpoint = '<account-id>.r2.cloudflarestorage.com';
SET s3_access_key_id = '<r2_access_key_id>';
SET s3_secret_access_key = '<r2_secret_access_key>';
SET s3_url_style = 'path';

-- Query remote Parquet without downloading the whole file:
SELECT title, document_citation, adopted_at 
FROM read_parquet('s3://lawchat-documents/legislatie/documents.parquet')
WHERE issuer = 'CURTEA CONSTITUȚIONALĂ'
LIMIT 10;
```

---

## 🌐 Full REST API Reference

The FastAPI service runs in-process with DuckDB, querying Parquet files directly with sub-millisecond response times.

Interactive Swagger UI documentation is available at **`/docs`** (or ReDoc at **`/redoc`**).

```bash
# Start API locally
uv run uvicorn api:app --reload --port 8000
```

---

### 1. 🩺 Health & Metadata

#### `GET /health`
Checks system status and availability of all datasets and the DuckDB FTS search index.

#### `GET /api/v1/stats`
Returns aggregated corpus counts across decisions, paragraphs, primary legislation documents, citation relationships, templates, and dictionary terms.

---

### 2. 📜 Primary Legislation (*Corpus Legislativ: Legi, OUG, HG, Decrete*)

#### `GET /api/v1/documents`
List and filter primary Romanian legislation from `documents.parquet` (over 251,000 acts).
- Query Parameters: `type` (e.g. `LEGE`, `ORDONANȚĂ DE URGENȚĂ`), `issuer`, `document_number`, `year`, `q`, `page`, `limit`.

#### `GET /api/v1/documents/{document_id}`
Retrieve full text and metadata for a specific primary legislative act.

#### `GET /api/v1/documents/{document_id}/articles`
Retrieve all structured articles for a specific legislative act from `articles.parquet`.

---

### 3. 🏛️ Court Decisions (*Jurisprudență ÎCCJ*)

#### `GET /api/v1/decisions`
Retrieve a paginated and filtered list of court rulings.
- Query Parameters: `department`, `matter_type`, `solution_type`, `from_date`, `to_date`, `page`, `limit`.

#### `GET /api/v1/decisions/{decision_id}`
Retrieve complete metadata, full judgment text, segmented paragraphs, and all legislation citations for a single ruling.

---

### 4. 🔗 Legislation Liaison (Bidirectional Citation Graph)

#### `GET /api/v1/decisions/{decision_id}/citations`
*(Decision $\to$ Legislation)*: List all laws, codes, emergency ordinances, articles, and paragraphs cited within a specific court decision.

#### `GET /api/v1/legislation/search`
*(Legislation $\to$ Decisions)*: Search for all High Court rulings that cite, interpret, or apply a specific legislative act or legal provision.
- Query Parameters: `act_type`, `act_number`, `act_year`, `article_number`, `canonical_query`, `limit`.

---

### 5. 🔍 Full-Text Search (BM25 & Columnar)

#### `GET /api/v1/search/fts`
High-speed full-text search across decisions using DuckDB BM25 index with Romanian language stemming and stopword filtering.
- Query Parameters: `q` (e.g. `abuz in serviciu`, `prescriptia raspunderii penale`), `department`, `limit`.

---

### 6. 📝 Document Templates (*Modele de Acte / Contracte*)

#### `GET /api/v1/modele/categories`
Retrieve all available template categories with document counts.

#### `GET /api/v1/modele`
Browse or search 848 legal document templates with category filtering.

#### `GET /api/v1/modele/{template_id}`
Retrieve full template text, legal ground notes, fillable variables, and source attribution.

---

### 7. 📖 Legal Dictionary (*Dicționar Juridic DEX*)

#### `GET /api/v1/dictionar/letters`
List all alphabet letters indexed in the legal dictionary and the count of terms per letter.

#### `GET /api/v1/dictionar`
Browse 5,181 dictionary terms by initial letter or perform keyword search across legal terms and definitions.

#### `GET /api/v1/dictionar/{term_id_or_slug}`
Retrieve the full legal definition, synonyms, and cited legislation for a specific term.

---

### 8. ⚖️ Constitutional Court Decisions (*Curtea Constituțională a României - CCR*)

#### `GET /api/v1/ccr/categories`
List all CCR decision categories and ruling counts.

#### `GET /api/v1/ccr`
Browse and filter Constitutional Court rulings across multiple years and categories.

#### `GET /api/v1/ccr/{decision_id_or_slug}`
Retrieve complete ruling metadata, decompressed full judgment text, publication notice, and cited Romanian legislation.

---

## 🔎 Pre-Configured DuckDB SQL Views (`create_views.sql`)

Load `create_views.sql` into any DuckDB session for out-of-the-box analytical queries:

```sql
IMPORT DATABASE 'data'; -- Or execute create_views.sql
```

- `civil_section_1` — Secția I Civilă (Civil law, property, family, contracts)
- `civil_section_2` — Secția a II-a Civilă (Commercial law, corporate disputes, insolvency)
- `penal_section` — Secția Penală (Criminal law and criminal procedure)
- `administrative_fiscal_section` — Secția de Contencios Administrativ și Fiscal (Tax, customs, administrative litigation)
- `ril_decisions` — **Recursuri în Interesul Legii** (Binding case law unifying judicial practice)
- `hp_decisions` — **Hotărâri Prealabile** (Preliminary rulings on legal questions)
- `modele_documente` — Pre-filtered table view of legal templates
- `dictionar_juridic` — Pre-filtered table view of legal dictionary definitions
- `ccr_decisions` — Base view of all Constitutional Court decisions and rulings
- `decizii_admitere_ccr` — CCR rulings upholding unconstitutionality objections/exceptions
- `hotarari_ccr` — CCR official rulings (electoral validation, interim presidential status)

---

## 🤖 Automated ETL & Cron Schedules

- **Decisions & Citations**: Daily at 04:30 AM Romanian Time (`30 1 * * *` UTC) $\to$ `scj.ro` $\to$ `decisions.parquet`, `decision_paragraphs.parquet`, `relationships.parquet`, `fts.duckdb`, Cloudflare R2
- **Document Templates**: Weekly Sunday at 05:00 AM (`00 2 * * 0` UTC) $\to$ `legeaz.net/modele` $\to$ `modele_documente.parquet`, Cloudflare R2
- **Legal Dictionary**: Weekly Saturday at 05:30 AM (`30 2 * * 6` UTC) $\to$ `legeaz.net/dictionar` $\to$ `dictionar_juridic.parquet`, Cloudflare R2
- **Constitutional Court (CCR)**: Weekly Friday at 04:30 AM (`30 1 * * 5` UTC) $\to$ `ccr.ro` $\to$ `ccr_decisions.parquet`, Cloudflare R2

---

## 🛠️ Local Development & CLI

```bash
# Install dependencies
uv sync --all-extras

# Run test suite
uv run pytest -v

# Sync all datasets to Cloudflare R2
just sync-r2

# Start API server
just serve
```

---

## 📜 License & Attribution

Public judicial case law from the Romanian High Court of Cassation and Justice (*Înalta Curte de Casație și Justiție*), the Constitutional Court of Romania (*Curtea Constituțională a României*), official legislation from *Monitorul Oficial*, and legal models/definitions from *legeaz.net*. Tooling, ETL pipeline, and API are open-source.

