import os
from pathlib import Path
import polars as pl
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, date

from api import app
from etl.transform import run_transform

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_test_data():
    raw_dir = Path("data/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    sample_text = """
    Secţia I civilă a dispus sesizarea în temeiul art. 2 alin. (1) din Ordonanţa de urgenţă a Guvernului nr. 62/2024 
    privind salarizarea personalului plătit din fonduri publice şi art. 13 din anexa nr. V la Legea-cadru nr. 153/2017.
    """
    
    df = pl.DataFrame([{
        "scj_id": "test-api-01",
        "title": "Decizia nr. 101/2026",
        "link": "https://www.scj.ro/detalii?id=test-api-01",
        "metadata_json": str({
            "Număr decizie": "Decizia nr. 101/2026",
            "Data deciziei": "10.05.2026",
            "Număr dosar": "100/1/2026",
            "Secția": "Secția I Civilă",
            "Tip document": "Decizie",
            "Materie": "Civil",
            "Tip soluție": "Admite recursul",
            "Cuvinte cheie": "salarizare, recurs",
            "Sumar speță": "Test summary privind salarizarea.",
        }),
        "content": sample_text,
        "raw_html": "<html></html>",
        "extracted_at": datetime.now(),
    }])
    df.write_parquet(raw_dir / "test_api_raw.parquet")
    run_transform()
    
    yield
    
    # Cleanup test raw file
    if (raw_dir / "test_api_raw.parquet").exists():
        (raw_dir / "test_api_raw.parquet").unlink()

def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["has_parquet"] is True

def test_stats_endpoint():
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_decisions"] >= 1
    assert data["total_paragraphs"] >= 1

def test_list_decisions():
    resp = client.get("/api/v1/decisions?department=Civil")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["data"][0]["decision_number"] == "Decizia nr. 101/2026"

def test_decision_detail_and_citations():
    # List to get ID
    resp = client.get("/api/v1/decisions")
    dec_id = resp.json()["data"][0]["id"]
    
    detail_resp = client.get(f"/api/v1/decisions/{dec_id}")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert len(detail_data["citations"]) >= 1
    
    # Decision -> Legislation
    cit_resp = client.get(f"/api/v1/decisions/{dec_id}/citations")
    assert cit_resp.status_code == 200
    citations = cit_resp.json()["citations"]
    assert any(c["act_type"] == "OUG" and "62" in c["act_number"] for c in citations)

def test_legislation_to_decisions_search():
    # Legislation -> Decision search
    resp = client.get("/api/v1/legislation/search?act_type=OUG&act_number=62&act_year=2024")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["data"][0]["decision_number"] == "Decizia nr. 101/2026"

def test_fts_search():
    resp = client.get("/api/v1/search/fts?q=salarizarea")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
