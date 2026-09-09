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
    data_dir = Path("data")
    raw_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    
    sample_text = """
    Secţia I civilă a dispus sesizarea în temeiul art. 2 alin. (1) din Ordonanţa de urgenţă a Guvernului nr. 62/2024 
    privind salarizarea personalului plătit din fonduri publice şi art. 13 din anexa nr. V la Legea-cadru nr. 153/2017.
    """
    
    # Decisions fixture
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
    
    # Templates fixture
    modele_df = pl.DataFrame([{
        "id": 888123,
        "slug": "model-contract-vanzare-imobil-optiune-rascumparare",
        "title": "Model de contract de vânzare a unui imobil cu opţiune de răscumpărare",
        "category": "Contracte",
        "legal_basis": "art. 1758 Cod civil",
        "content": "Subsemnatul vânzător vând cumpărătorului imobilul în condițiile art. 1758 Cod civil...",
        "source_attribution": "Uniunea Naţionala a Notarilor Publici",
        "link": "https://legeaz.net/modele/model-contract-vanzare-imobil-optiune-rascumparare",
        "synced_at": datetime.now(),
    }])
    modele_df.write_parquet(data_dir / "modele_documente.parquet")

    # Dictionary fixture
    dict_df = pl.DataFrame([{
        "id": 73947,
        "slug": "abuzul-in-serviciu",
        "term": "Abuzul în serviciu",
        "letter": "A",
        "definition": "ABUZUL ÎN SERVICIU, fapta funcţionarului public care în exerciţiul atribuţiilor...",
        "link": "https://legeaz.net/dictionar-juridic/abuzul-in-serviciu",
        "synced_at": datetime.now(),
    }])
    dict_df.write_parquet(data_dir / "dictionar_juridic.parquet")

    # CCR fixture
    ccr_df = pl.DataFrame([{
        "id": 991,
        "slug": "decizie-885-2026-decizia-nr885-din-17-august-2026",
        "title": "DECIZIA nr.885 din 17 august 2026",
        "act_type": "DECIZIE",
        "act_number": "885",
        "act_year": 2026,
        "decision_date": date(2026, 8, 17),
        "category": "Decizii de admitere",
        "publication_notice": "Publicată în Monitorul Oficial nr.715 din 27.08.2026",
        "summary": "referitoare la obiecția de neconstituționalitate...",
        "content": "Curtea Constituțională a constatat neconstituționalitatea conform art. 2 din Legea nr. 47/1992 privind organizarea și funcționarea Curții Constituționale.",
        "pdf_url": "https://www.ccr.ro/wp-content/uploads/2026/08/Decizie_885_2026.pdf",
        "synced_at": datetime.now(),
    }])
    ccr_df.write_parquet(data_dir / "ccr_decisions.parquet")

    yield
    
    # Cleanup test raw file
    if (raw_dir / "test_api_raw.parquet").exists():
        (raw_dir / "test_api_raw.parquet").unlink()

def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["has_decisions"] is True
    assert data["has_modele"] is True
    assert data["has_dictionar"] is True
    assert data["has_ccr"] is True

def test_stats_endpoint():
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_decisions"] >= 1
    assert data["total_paragraphs"] >= 1
    assert data["total_modele"] >= 1
    assert data["total_dictionar_terms"] >= 1
    assert data["total_ccr_decisions"] >= 1

def test_list_decisions():
    resp = client.get("/api/v1/decisions?department=Civil")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["data"][0]["decision_number"] == "Decizia nr. 101/2026"

def test_decision_detail_and_citations():
    resp = client.get("/api/v1/decisions")
    dec_id = resp.json()["data"][0]["id"]
    
    detail_resp = client.get(f"/api/v1/decisions/{dec_id}")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert len(detail_data["citations"]) >= 1
    
    cit_resp = client.get(f"/api/v1/decisions/{dec_id}/citations")
    assert cit_resp.status_code == 200
    citations = cit_resp.json()["citations"]
    assert any(c["act_type"] == "OUG" and "62" in c["act_number"] for c in citations)

def test_legislation_to_decisions_search():
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

def test_modele_endpoints():
    cat_resp = client.get("/api/v1/modele/categories")
    assert cat_resp.status_code == 200
    cats = cat_resp.json()["categories"]
    assert any(c["category"] == "Contracte" for c in cats)

    list_resp = client.get("/api/v1/modele?category=Contracte")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["count"] >= 1

    tpl_id = list_data["data"][0]["id"]
    det_resp = client.get(f"/api/v1/modele/{tpl_id}")
    assert det_resp.status_code == 200
    det_data = det_resp.json()
    assert det_data["category"] == "Contracte"

def test_dictionar_endpoints():
    # 1. Letters
    let_resp = client.get("/api/v1/dictionar/letters")
    assert let_resp.status_code == 200
    letters = let_resp.json()["letters"]
    assert any(l["letter"] == "A" for l in letters)

    # 2. List & Search
    list_resp = client.get("/api/v1/dictionar?letter=A&q=abuz")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["count"] >= 1
    assert "abuzul-in-serviciu" in list_data["data"][0]["slug"]

    # 3. Detail by Slug
    det_resp = client.get("/api/v1/dictionar/abuzul-in-serviciu")
    assert det_resp.status_code == 200
    det_data = det_resp.json()
    assert "funcţionarului public" in det_data["definition"]

def test_ccr_endpoints():
    # 1. Categories
    cat_resp = client.get("/api/v1/ccr/categories")
    assert cat_resp.status_code == 200
    cats = cat_resp.json()["categories"]
    assert any(c["category"] == "Decizii de admitere" for c in cats)

    # 2. List & Search
    list_resp = client.get("/api/v1/ccr?year=2026&category=admitere")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["count"] >= 1
    assert list_data["data"][0]["act_number"] == "885"

    # 3. Detail by ID or Slug
    slug = list_data["data"][0]["slug"]
    det_resp = client.get(f"/api/v1/ccr/{slug}")
    assert det_resp.status_code == 200
    det_data = det_resp.json()
    assert det_data["act_number"] == "885"
    assert len(det_data["citations"]) >= 1
    assert any(c["act_number"] == "47" and c["act_year"] == 1992 for c in det_data["citations"])


def test_documents_endpoints():
    resp = client.get("/api/v1/documents?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "data" in data
    if data["count"] > 0:
        doc_id = data["data"][0]["id"]
        detail_resp = client.get(f"/api/v1/documents/{doc_id}")
        assert detail_resp.status_code == 200
        assert "title" in detail_resp.json()

        art_resp = client.get(f"/api/v1/documents/{doc_id}/articles")
        assert art_resp.status_code == 200
        assert "articles" in art_resp.json()


