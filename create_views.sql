-- =============================================================================
-- Views and helper macros for Romanian High Court of Cassation & Justice (ÎCCJ)
-- Compatible with DuckDB and Parquet storage
-- =============================================================================

-- Base table views pointing to local or remote Parquet files
CREATE OR REPLACE VIEW decisions AS 
SELECT * FROM read_parquet('data/decisions.parquet');

CREATE OR REPLACE VIEW decision_paragraphs AS 
SELECT * FROM read_parquet('data/decision_paragraphs.parquet');

CREATE OR REPLACE VIEW relationships AS 
SELECT * FROM read_parquet('data/relationships.parquet');

-- =============================================================================
-- Legislation Liaison (Bidirectional Citation & Relationship Graph)
-- =============================================================================

-- 1. Given a decision, list all referenced normative acts, codes, and articles
CREATE OR REPLACE VIEW decision_to_legislation_view AS
SELECT 
    d.id AS decision_id,
    d.decision_number,
    d.decision_date,
    d.docket_number,
    d.department,
    r.target_type,
    r.act_type,
    r.act_number,
    r.act_year,
    r.article_number,
    r.paragraph_number,
    r.annex,
    r.chapter,
    r.canonical_citation,
    r.relationship_type
FROM decisions d
JOIN relationships r ON d.id = r.source_decision_id;

-- 2. Given a law, code, or article, list all court decisions citing or applying it
CREATE OR REPLACE VIEW legislation_to_decisions_view AS
SELECT 
    r.canonical_citation,
    r.target_type,
    r.act_type,
    r.act_number,
    r.act_year,
    r.article_number,
    r.paragraph_number,
    r.annex,
    r.chapter,
    d.id AS decision_id,
    d.decision_number,
    d.decision_date,
    d.docket_number,
    d.department,
    d.solution_type,
    d.summary,
    d.link
FROM relationships r
JOIN decisions d ON r.source_decision_id = d.id;

-- =============================================================================
-- Domain Views (Specialized Court Sections / Secții ÎCCJ)
-- =============================================================================

-- Secția I Civilă (Civil Law Section I)
CREATE OR REPLACE VIEW civil_section_1 AS
SELECT * FROM decisions 
WHERE department ILIKE '%Civilă I%' OR department ILIKE '%I Civilă%';

-- Secția a II-a Civilă (Commercial & Company Law / Secția a II-a Civilă)
CREATE OR REPLACE VIEW civil_section_2 AS
SELECT * FROM decisions 
WHERE department ILIKE '%Civilă II%' OR department ILIKE '%II-a Civilă%';

-- Secția Penală (Criminal Law Section)
CREATE OR REPLACE VIEW penal_section AS
SELECT * FROM decisions 
WHERE department ILIKE '%Penal%';

-- Secția de Contencios Administrativ și Fiscal (Administrative & Tax Section)
CREATE OR REPLACE VIEW administrative_fiscal_section AS
SELECT * FROM decisions 
WHERE department ILIKE '%Contencios%' OR department ILIKE '%Fiscal%';

-- =============================================================================
-- High-Value Binding Jurisprudence (RIL & Hotărâri Prealabile)
-- =============================================================================

-- Recursuri în Interesul Legii (Appeals in the Interest of Law - Binding Case Law)
CREATE OR REPLACE VIEW ril_decisions AS
SELECT * FROM decisions 
WHERE document_type ILIKE '%recurs în interesul legii%' 
   OR summary ILIKE '%recurs în interesul legii%'
   OR keywords ILIKE '%RIL%';

-- Hotărâri Prealabile pentru dezlegarea unor chestiuni de drept (Preliminary Rulings)
CREATE OR REPLACE VIEW hp_decisions AS
SELECT * FROM decisions 
WHERE document_type ILIKE '%hotărâre prealabilă%' 
   OR summary ILIKE '%dezlegarea unor chestiuni de drept%'
   OR keywords ILIKE '%hotarare prealabila%';

-- Recent Rulings (Last 12 Months)
CREATE OR REPLACE VIEW recent_decisions AS
SELECT * FROM decisions 
WHERE decision_date >= (CURRENT_DATE - INTERVAL 1 YEAR)
ORDER BY decision_date DESC;
