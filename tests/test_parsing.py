from datetime import date
from etl.citations import extract_citations
from etl.transform import parse_date

def test_parse_date():
    assert parse_date("15.03.2024") == date(2024, 3, 15)
    assert parse_date("2024-03-15") == date(2024, 3, 15)
    assert parse_date("invalid") is None
    assert parse_date(None) is None

def test_extract_complex_user_example():
    text = """
    Secţia I civilă a dispus, prin încheierea din 25 martie 2026, în Dosarul nr. 1.490/107/2025, 
    sesizarea Înaltei Curţi de Casaţie şi Justiţie, în temeiul dispoziţiilor art. 2 alin. (1) din Ordonanţa de urgenţă a Guvernului nr. 62/2024 
    privind unele măsuri pentru soluţionarea proceselor privind salarizarea personalului plătit din fonduri publice, 
    precum şi a proceselor privind prestaţii de asigurări sociale (Ordonanţa de urgenţă a Guvernului nr. 62/2024), 
    în vederea pronunţării unei hotărâri prealabile dacă sporul prevăzut de art. 13 din capitolul VIII, secţiunea a 2-a din anexa nr. V la Legea-cadru nr. 153/2017 
    privind salarizarea personalului plătit din fonduri publice, cu modificările şi completările ulterioare, 
    şi de art. 1 alin. (1) şi (2) din Ordinul ministrului justiţiei nr. 2.830/C/2017, 
    are natura unui spor permanent ce trebuie luat în considerare la calculul indemnizaţiei concediului de odihnă, 
    cuvenită pentru perioada aferentă duratei delegării, în condiţiile art. 6 din Regulamentul privind concediile judecătorilor.
    """
    
    citations = extract_citations(999, text)
    canonical_list = [c["canonical_citation"] for c in citations]
    
    # 1. Check OUG 62/2024 Art. 2 Alin. 1
    assert any("OUG 62/2024 art. 2 alin. (1)" in c for c in canonical_list)
    oug_cit = next(c for c in citations if "62" in c["act_number"] and c["act_type"] == "OUG")
    assert oug_cit["act_year"] == 2024
    assert oug_cit["article_number"] == "2"
    assert oug_cit["paragraph_number"] == "1"
    
    # 2. Check Legea-cadru 153/2017 Anexa V Cap. VIII Art. 13
    assert any("LEGE 153/2017" in c and "Anexa V" in c and "Cap. VIII" in c and "art. 13" in c for c in canonical_list)
    
    # 3. Check Ordinul ministrului justitiei 2.830/C/2017
    assert any("ORDIN 2.830/C/2017" in c and "art. 1" in c for c in canonical_list)
    
    # 4. Check Regulamentul privind concediile judecatorilor
    assert any("Regulamentul privind concediile judecătorilor" in c and "art. 6" in c for c in canonical_list)

def test_extract_codes_and_case_law():
    text = """
    În aplicarea art. 188 din Codul penal și a art. 1200 din Codul civil, precum și a Deciziei CCR nr. 358/2022.
    """
    citations = extract_citations(1000, text)
    canonical_list = [c["canonical_citation"] for c in citations]
    
    assert "Codul Penal art. 188" in canonical_list
    assert "Codul Civil art. 1200" in canonical_list
    assert "Decizia CCR nr. 358/2022" in canonical_list
