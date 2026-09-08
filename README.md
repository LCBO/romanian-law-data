# ⚖️ Romanian Legal Corpus & Intelligence API (*Instanțe, CCR & Legislație*)

High-performance, Zstandard-compressed Parquet datasets, DuckDB analytical engine, and FastAPI REST service for Romanian High Court judicial rulings (*Înalta Curte de Casație și Justiție* - ÎCCJ), Constitutional Court decisions (*Curtea Constituțională a României* - CCR), bidirectional legislation citation graph, legal document templates (*Modele de Acte*), and legal dictionary definitions (*Dicționar Juridic*).

---

## 📊 Datasets & Schema Structure

All datasets are compressed with **Zstandard** (`zstd`) and structured for zero-copy streaming into **[DuckDB](https://duckdb.org/)**, **[Polars](https://pola.rs/)**, and **[PyArrow](https://arrow.apache.org/docs/python/)**.

| Dataset File | Source | Description |
| :--- | :--- | :--- |
| **`data/decisions.parquet`** | `scj.ro` | High Court judicial rulings with docket number, court section, decision date, legal matter, solution type, summary, and clean text. |
| **`data/decision_paragraphs.parquet`** | `scj.ro` | Granular paragraph text categorized by section (*Considerente*, *Dispozitiv*) for precise citation referencing. |
| **`data/relationships.parquet`** | NLP Engine | Directed citation graph linking decisions to acts (*OUG*, *Lege*, *Codul Civil*, *Codul Penal*), articles, paragraphs, and CCR/ECHR rulings. |
| **`data/modele_documente.parquet`** | `legeaz.net/modele` | ~900 categorized legal templates (Cereri, Contracte, Plângeri, Acțiuni, Notificări, etc.) with legal bases and fillable fields. |
| **`data/dictionar_juridic.parquet`** | `legeaz.net/dictionar` | ~5,200 legal definitions indexed alphabetically (A–Z) with automated Romanian legislation citation extraction. |
| **`data/ccr_decisions.parquet`** | `ccr.ro` | Constitutional Court rulings, unconstitutionality admissions, and opinions with full text decompressed from official PDFs. |


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
Checks system status and availability of datasets and the DuckDB FTS search index.

- **Response `200 OK`**:
```json
{
  "status": "healthy",
  "has_fts": true,
  "has_decisions": true,
  "has_modele": true,
  "has_dictionar": true
}
```

#### `GET /api/v1/stats`
Returns aggregated corpus counts across decisions, paragraphs, citation relationships, templates, and dictionary terms.

- **Response `200 OK`**:
```json
{
  "total_decisions": 125040,
  "total_paragraphs": 1420500,
  "total_citations": 489200,
  "total_modele": 895,
  "total_dictionar_terms": 5240
}
```

---

### 2. 🏛️ Court Decisions (*Jurisprudență ÎCCJ*)

#### `GET /api/v1/decisions`
Retrieve a paginated and filtered list of court rulings.

- **Query Parameters**:
  - `department` *(string, optional)*: Filter by court section (e.g. `Secția Penală`, `Secția I Civilă`).
  - `matter_type` *(string, optional)*: Legal domain (e.g. `Penal`, `Civil`, `Contencios administrativ și fiscal`).
  - `solution_type` *(string, optional)*: Ruling outcome (e.g. `Admis`, `Respins`, `Casat`).
  - `from_date` *(string, optional)*: Start date filter (`YYYY-MM-DD`).
  - `to_date` *(string, optional)*: End date filter (`YYYY-MM-DD`).
  - `page` *(int, default: 1)*: Page number.
  - `limit` *(int, default: 20, max: 100)*: Items per page.
- **Example Request**:
  ```http
  GET /api/v1/decisions?department=Secția+Penală&solution_type=Admis&page=1&limit=10
  ```

#### `GET /api/v1/decisions/{decision_id}`
Retrieve complete metadata, full judgment text, segmented paragraphs, and all legislation citations for a single ruling.

- **Path Parameters**:
  - `decision_id` *(int, required)*: Internal decision ID.
- **Example Request**:
  ```http
  GET /api/v1/decisions/1054
  ```

---

### 3. 🔗 Legislation Liaison (Bidirectional Citation Graph)

#### `GET /api/v1/decisions/{decision_id}/citations`
*(Decision $\to$ Legislation)*: List all laws, codes, emergency ordinances, articles, and paragraphs cited within a specific court decision.

- **Path Parameters**:
  - `decision_id` *(int, required)*: Decision ID.
- **Example Response**:
```json
{
  "decision_id": 1054,
  "count": 2,
  "citations": [
    {
      "canonical_citation": "OUG 62/2024 art. 2 alin. (1)",
      "target_type": "OUG",
      "act_type": "OUG",
      "act_number": "62",
      "act_year": 2024,
      "article_number": "2",
      "paragraph_number": "1",
      "relationship_type": "CITES"
    }
  ]
}
```

#### `GET /api/v1/legislation/search`
*(Legislation $\to$ Decisions)*: Search for all High Court rulings that cite, interpret, or apply a specific legislative act or legal provision.

- **Query Parameters**:
  - `act_type` *(string, optional)*: `OUG`, `LEGE`, `COD`, `ORDIN`, `DECIZIE CCR`, etc.
  - `act_number` *(string, optional)*: e.g. `62`, `153`, `Codul Civil`.
  - `act_year` *(int, optional)*: e.g. `2024`, `2017`.
  - `article_number` *(string, optional)*: Article identifier (e.g. `2`, `13`, `1200`).
  - `canonical_query` *(string, optional)*: Freeform citation text (e.g. `OUG 62/2024`, `Codul Penal`).
  - `limit` *(int, default: 20, max: 100)*: Result limit.
- **Example Request**:
  ```http
  GET /api/v1/legislation/search?act_type=OUG&act_number=62&act_year=2024&article_number=2
  ```

---

### 4. 🔍 Full-Text Search (BM25 & Columnar)

#### `GET /api/v1/search/fts`
High-speed full-text search across decisions using DuckDB BM25 index with Romanian language stemming and stopword filtering (with automatic fallback to columnar substring matching).

- **Query Parameters**:
  - `q` *(string, required)*: Search keywords or phrases (e.g. `abuz in serviciu`, `prescriptia raspunderii penale`).
  - `department` *(string, optional)*: Optional section filter.
  - `limit` *(int, default: 20, max: 100)*: Max results to return.
- **Example Request**:
  ```http
  GET /api/v1/search/fts?q=prescriptia+raspunderii+penale&limit=10
  ```

---

### 5. 📝 Document Templates (*Modele de Acte / Contracte*)

#### `GET /api/v1/modele/categories`
Retrieve all available template categories with document counts.

- **Response `200 OK`**:
```json
{
  "categories": [
    {"category": "Contracte", "count": 210},
    {"category": "Dreptul Familiei", "count": 145},
    {"category": "Succesiuni & Testamente", "count": 88},
    {"category": "Cereri & Acțiuni în Justiție", "count": 190}
  ]
}
```

#### `GET /api/v1/modele`
Browse or search legal document templates with category filtering.

- **Query Parameters**:
  - `category` *(string, optional)*: Filter by category (e.g. `Contracte`, `Dreptul Familiei`).
  - `q` *(string, optional)*: Search term in title or content.
  - `page` *(int, default: 1)*: Page number.
  - `limit` *(int, default: 20, max: 100)*: Items per page.
- **Example Request**:
  ```http
  GET /api/v1/modele?category=Contracte&q=inchiriere&page=1&limit=10
  ```

#### `GET /api/v1/modele/{template_id}`
Retrieve full template text, legal ground notes, fillable variables, and source attribution.

- **Path Parameters**:
  - `template_id` *(int, required)*: Template ID.

---

### 6. 📖 Legal Dictionary (*Dicționar Juridic DEX*)

#### `GET /api/v1/dictionar/letters`
List all alphabet letters indexed in the legal dictionary and the count of terms per letter.

- **Response `200 OK`**:
```json
{
  "letters": [
    {"letter": "A", "count": 412},
    {"letter": "B", "count": 198},
    {"letter": "C", "count": 680}
  ]
}
```

#### `GET /api/v1/dictionar`
Browse dictionary terms by initial letter or perform keyword search across legal terms and definitions.

- **Query Parameters**:
  - `letter` *(string, optional, max length: 2)*: Filter by initial letter (`A` through `Z`).
  - `q` *(string, optional)*: Keyword to search in term names and definition bodies.
  - `page` *(int, default: 1)*: Page number.
  - `limit` *(int, default: 50, max: 200)*: Items per page.
- **Example Request**:
  ```http
  GET /api/v1/dictionar?letter=A&q=apel&page=1&limit=25
  ```

#### `GET /api/v1/dictionar/{term_id_or_slug}`
Retrieve the full legal definition, synonyms, and cited legislation for a specific term using either its numeric ID or URL slug.

- **Path Parameters**:
  - `term_id_or_slug` *(string, required)*: Term numeric ID (e.g. `42`) or slug (e.g. `apel`).
- **Example Request**:
  ```http
  GET /api/v1/dictionar/apel
  ```

---

### 7. ⚖️ Constitutional Court Decisions (*Curtea Constituțională a României - CCR*)

#### `GET /api/v1/ccr/categories`
List all CCR decision categories (Decizii de admitere, Decizii relevante, Hotărâri de admitere, etc.) and ruling counts.

- **Response `200 OK`**:
```json
{
  "categories": [
    {"category": "Decizii de admitere", "count": 1210},
    {"category": "Decizii relevante", "count": 456},
    {"category": "Hotărâri de admitere", "count": 48},
    {"category": "Hotărâri relevante", "count": 32},
    {"category": "Avize consultative", "count": 12}
  ]
}
```

#### `GET /api/v1/ccr`
Browse and filter Constitutional Court rulings across multiple years and categories.

- **Query Parameters**:
  - `category` *(string, optional)*: Filter by section (e.g. `Decizii de admitere`, `Hotărâri`).
  - `act_type` *(string, optional)*: Filter by type (`DECIZIE`, `HOTĂRÂRE`, `AVIZ CONSULTATIV`).
  - `year` *(int, optional)*: Filter by year of ruling (e.g. `2026`, `2025`, `2024`... down to `1992`).
  - `q` *(string, optional)*: Keyword to search in ruling title, summary, or publication notice.
  - `page` *(int, default: 1)*: Page number.
  - `limit` *(int, default: 20, max: 100)*: Items per page.
- **Example Request**:
  ```http
  GET /api/v1/ccr?year=2026&category=admitere&page=1&limit=10
  ```

#### `GET /api/v1/ccr/{decision_id_or_slug}`
Retrieve complete ruling metadata, decompressed full judgment text extracted from official PDF, Monitorul Oficial publication notice, and cited Romanian legislation.

- **Path Parameters**:
  - `decision_id_or_slug` *(string, required)*: Numeric decision ID or slug (e.g. `decizie-885-2026`).
- **Example Response**:
```json
{
  "id": 885,
  "slug": "decizie-885-2026-decizia-nr885-din-17-august-2026",
  "title": "DECIZIA nr.885 din 17 august 2026",
  "act_type": "DECIZIE",
  "act_number": "885",
  "act_year": 2026,
  "decision_date": "2026-08-17",
  "category": "Decizii de admitere",
  "publication_notice": "Publicată în Monitorul Oficial nr.715 din 27.08.2026",
  "summary": "referitoare la obiecția de neconstituționalitate a Legii...",
  "pdf_url": "https://www.ccr.ro/wp-content/uploads/2026/08/Decizie_885_2026.pdf",
  "content": "...",
  "citations": [
    {
      "canonical_citation": "LEGE 47/1992 art. 2",
      "target_type": "LAW",
      "act_type": "LEGE",
      "act_number": "47",
      "act_year": 1992,
      "article_number": "2"
    }
  ]
}
```

---

## 🔎 Pre-Configured DuckDB SQL Views (`create_views.sql`)

Load `create_views.sql` into any DuckDB session for out-of-the-box analytical queries:

```sql
IMPORT DATABASE 'data'; -- Or execute create_views.sql
```

* `civil_section_1` — Secția I Civilă (Civil law, property, family, contracts)
* `civil_section_2` — Secția a II-a Civilă (Commercial law, corporate disputes, insolvency)
* `penal_section` — Secția Penală (Criminal law and criminal procedure)
* `administrative_fiscal_section` — Secția de Contencios Administrativ și Fiscal (Tax, customs, administrative litigation)
* `ril_decisions` — **Recursuri în Interesul Legii** (Binding case law unifying judicial practice)
* `hp_decisions` — **Hotărâri Prealabile** (Preliminary rulings on legal questions)
* `modele_documente` — Pre-filtered table view of legal templates
* `dictionar_juridic` — Pre-filtered table view of legal dictionary definitions
* `ccr_decisions` — Base view of all Constitutional Court decisions and rulings
* `decizii_admitere_ccr` — CCR rulings upholding unconstitutionality objections/exceptions
* `hotarari_ccr` — CCR official rulings (electoral validation, interim presidential status)

---

## 🤖 Automated ETL & Cron Schedules

| Workflow | Schedule (Romanian Time) | Target Source | Output Artifacts |
| :--- | :--- | :--- | :--- |
| **Decisions & Citations** | **Daily at 04:30 AM** (`30 1 * * *` UTC) | `scj.ro` | `decisions.parquet`, `decision_paragraphs.parquet`, `relationships.parquet`, `fts.duckdb` |
| **Document Templates** | **Weekly Sunday at 05:00 AM** (`00 2 * * 0` UTC) | `legeaz.net/modele` | `modele_documente.parquet` |
| **Legal Dictionary** | **Weekly Saturday at 05:30 AM** (`30 2 * * 6` UTC) | `legeaz.net/dictionar` | `dictionar_juridic.parquet` |
| **Constitutional Court (CCR)** | **Weekly Friday at 04:30 AM** (`30 1 * * 5` UTC) | `ccr.ro` | `ccr_decisions.parquet` |

---

## 🛠️ Local Development & CLI

```bash
# Install dependencies
uv sync --all-extras

# Run test suite
uv run pytest -v

# Run extractors manually
just extract-modele
just extract-dictionar
just extract-ccr

# Start API server
just serve
```

---

## 📜 License & Attribution

Public judicial case law from the Romanian High Court of Cassation and Justice (*Înalta Curte de Casație și Justiție*), the Constitutional Court of Romania (*Curtea Constituțională a României*), and legal models/definitions from *legeaz.net*. Tooling, ETL pipeline, and API are open-source.

