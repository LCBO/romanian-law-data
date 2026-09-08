from datetime import date
from etl.transform import parse_date, extract_relationships

def test_parse_date():
    assert parse_date("15.03.2024") == date(2024, 3, 15)
    assert parse_date("2024-03-15") == date(2024, 3, 15)
    assert parse_date("invalid") is None
    assert parse_date(None) is None

def test_extract_relationships():
    sample_text = """
    În temeiul art. 188 din Codul penal și având în vedere dispozițiile din Legea nr. 286/2009,
    precum și Decizia Curții Constituționale nr. 358/2022, instanța reține aplicabilitatea jurisprudenței
    stabilite în Cauza Rotaru împotriva României.
    """
    rels = extract_relationships(1001, sample_text)
    assert len(rels) >= 3
    types = {r["target_type"] for r in rels}
    assert "LAW" in types
    assert "CCR_DECISION" in types
    assert "ECHR" in types
