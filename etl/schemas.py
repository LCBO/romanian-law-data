import pandera.polars as pa
import polars as pl

class DecisionSchema(pa.DataFrameModel):
    id: int = pa.Field(unique=True)
    scj_id: str = pa.Field(nullable=True)
    decision_number: str = pa.Field(nullable=True)
    decision_date: pl.Date = pa.Field(nullable=True)
    docket_number: str = pa.Field(nullable=True)
    department: str = pa.Field(nullable=True)
    document_type: str = pa.Field(nullable=True)
    matter_type: str = pa.Field(nullable=True)
    solution_type: str = pa.Field(nullable=True)
    keywords: str = pa.Field(nullable=True)
    legal_grounds: str = pa.Field(nullable=True)
    summary: str = pa.Field(nullable=True)
    content: str = pa.Field(nullable=False)
    link: str = pa.Field(nullable=True)
    synced_at: pl.Datetime = pa.Field(nullable=False)

    class Config:
        strict = True
        coerce = True


class ParagraphSchema(pa.DataFrameModel):
    id: int = pa.Field(unique=True)
    decision_id: int = pa.Field()
    section_type: str = pa.Field(nullable=True)
    paragraph_number: int = pa.Field()
    content: str = pa.Field(nullable=False)

    class Config:
        strict = True
        coerce = True


class RelationshipSchema(pa.DataFrameModel):
    id: int = pa.Field(unique=True)
    source_decision_id: int = pa.Field()
    target_type: str = pa.Field()  # 'LAW', 'CODE', 'CCR_DECISION', 'ICCJ_DECISION', 'REGULATION', 'ECHR'
    act_type: str = pa.Field(nullable=True)  # 'LEGE', 'OUG', 'OG', 'HG', 'ORDIN', 'COD', etc.
    act_number: str = pa.Field(nullable=True)
    act_year: int = pa.Field(nullable=True)
    article_number: str = pa.Field(nullable=True)
    paragraph_number: str = pa.Field(nullable=True)
    annex: str = pa.Field(nullable=True)
    chapter: str = pa.Field(nullable=True)
    canonical_citation: str = pa.Field(nullable=False)
    relationship_type: str = pa.Field(nullable=False)  # 'applies', 'interprets', 'cites', 'overrules'

    class Config:
        strict = True
        coerce = True


class DocumentTemplateSchema(pa.DataFrameModel):
    id: int = pa.Field(unique=True)
    slug: str = pa.Field(nullable=False)
    title: str = pa.Field(nullable=False)
    category: str = pa.Field(nullable=False)
    legal_basis: str = pa.Field(nullable=True)
    content: str = pa.Field(nullable=False)
    source_attribution: str = pa.Field(nullable=True)
    link: str = pa.Field(nullable=False)
    synced_at: pl.Datetime = pa.Field(nullable=False)

    class Config:
        strict = True
        coerce = True


class DictionaryTermSchema(pa.DataFrameModel):
    id: int = pa.Field(unique=True)
    slug: str = pa.Field(nullable=False)
    term: str = pa.Field(nullable=False)
    letter: str = pa.Field(nullable=False)
    definition: str = pa.Field(nullable=False)
    link: str = pa.Field(nullable=False)
    synced_at: pl.Datetime = pa.Field(nullable=False)

    class Config:
        strict = True
        coerce = True


class CCRDecisionSchema(pa.DataFrameModel):
    id: int = pa.Field(unique=True)
    slug: str = pa.Field(nullable=False)
    title: str = pa.Field(nullable=False)
    act_type: str = pa.Field(nullable=False)
    act_number: str = pa.Field(nullable=False)
    act_year: int = pa.Field(nullable=True)
    decision_date: pl.Date = pa.Field(nullable=True)
    category: str = pa.Field(nullable=False)
    publication_notice: str = pa.Field(nullable=True)
    summary: str = pa.Field(nullable=True)
    content: str = pa.Field(nullable=False)
    pdf_url: str = pa.Field(nullable=False)
    synced_at: pl.Datetime = pa.Field(nullable=False)

    class Config:
        strict = True
        coerce = True

