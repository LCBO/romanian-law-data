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
    target_type: str = pa.Field()  # 'LAW', 'ARTICLE', 'CCR_DECISION', 'ICCJ_DECISION', 'ECHR'
    target_citation: str = pa.Field()
    relationship_type: str = pa.Field()  # 'applies', 'interprets', 'cites', 'overrules'

    class Config:
        strict = True
        coerce = True
