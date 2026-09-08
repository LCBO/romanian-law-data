import re
from typing import Any

ACT_TYPES = [
    (r"ordonan[tţ][aă]\s+de\s+urgen[tţ][aă]\s*(?:a\s+guvernului)?|o\.?u\.?g\.?", "OUG"),
    (r"ordonan[tţ][aă]\s*(?:a\s+guvernului)?|o\.?g\.?", "OG"),
    (r"hot[aă]r[aâî]rea\s+guvernului|h\.?g\.?", "HG"),
    (r"ordinul\s+ministrului\s+[a-z\u0103\u00e2\u00ee\u0219\u021b\u015f\u0163]+|ordinul\s+pre[sş]edintelui\s+[a-z\u0103\u00e2\u00ee\u0219\u021b\u015f\u0163]+|ordinul|ordin", "ORDIN"),
    (r"legea-cadru|lege-cadru|legea|lege", "LEGE"),
    (r"decretul|decret", "DECRET"),
    (r"regulamentul|regulament", "REGULAMENT"),
]

CODE_MAP = {
    "codul civil": "Codul Civil",
    "vechiul cod civil": "Codul Civil (1864)",
    "noul cod civil": "Codul Civil",
    "codul penal": "Codul Penal",
    "vechiul cod penal": "Codul Penal (1969)",
    "noul cod penal": "Codul Penal",
    "codul de procedură civilă": "Codul de Procedură Civilă",
    "codul de procedura civila": "Codul de Procedură Civilă",
    "noul cod de procedură civilă": "Codul de Procedură Civilă",
    "vechiul cod de procedură civilă": "Codul de Procedură Civilă (1865)",
    "codul de procedură penală": "Codul de Procedură Penală",
    "codul de procedura penala": "Codul de Procedură Penală",
    "noul cod de procedură penală": "Codul de Procedură Penală",
    "vechiul cod de procedură penală": "Codul de Procedură Penală (1968)",
    "codul fiscal": "Codul Fiscal",
    "codul de procedură fiscală": "Codul de Procedură Fiscală",
    "codul muncii": "Codul Muncii",
    "codul administrativ": "Codul Administrativ",
    "codul aerian": "Codul Aerian",
    "codul silvic": "Codul Silvic",
    "codul vamal": "Codul Vamal",
    "constituția româniei": "Constituția României",
    "constituţia româniei": "Constituția României",
}


def normalize_act_type(raw_type: str) -> str:
    cleaned = raw_type.strip().lower()
    for pattern, act_type in ACT_TYPES:
        if re.search(r"^" + pattern, cleaned, re.IGNORECASE) or re.search(pattern, cleaned, re.IGNORECASE):
            return act_type
    return "LEGE"


def extract_citations(decision_id: int, text: str) -> list[dict[str, Any]]:
    citations = []
    seen = set()

    # 1. Complex numbered acts with optional structural hierarchy (art, alin, cap, sect, anexa)
    # Examples:
    # - art. 2 alin. (1) din Ordonanţa de urgenţă a Guvernului nr. 62/2024
    # - art. 13 din capitolul VIII, secţiunea a 2-a din anexa nr. V la Legea-cadru nr. 153/2017
    # - art. 1 alin. (1) şi (2) din Ordinul ministrului justiţiei nr. 2.830/C/2017
    numbered_regex = re.compile(
        r"(?:(?:prevăzut\s+de|dispoziţiile|dispozițiile|prevederile|conform|potrivit)\s+)?"
        r"(?:art\.|articolul|articolului)\s*(?P<art>\d+(?:\^\d+)?(?:\s*lit\.\s*[a-z\)]+)?)"
        r"(?:\s*(?:alin\.|alineatul|alineatele|alineatului)\s*(?P<alin>\(?\d+(?:\)?(?:\s*(?:și|şi|,)\s*\(?\d+\)?)*)?))?"
        r"(?:\s*(?:din|al)\s*(?:capitolul|cap\.)\s*(?P<cap>[IVXLCDM\d]+))?"
        r"(?:,?\s*(?:secțiunea|secţiunea)\s*(?P<sec>[a-z\d\-\s]+?))?"
        r"(?:\s*(?:din|al)\s*(?:anexa|anexei)\s*(?:nr\.)?\s*(?P<anexa>[IVXLCDM\d]+))?"
        r"\s*(?:din|la|al)\s*"
        r"(?P<act_name>Legea-cadru|Legea|Ordonan[tţ]a\s+de\s+urgen[tţ][aă]\s*(?:a\s+Guvernului)?|Ordonan[tţ]a\s*(?:Guvernului)?|Hot[aă]r[aâî]rea\s+Guvernului|Ordinul\s+ministrului(?:\s+[a-z\u0103\u00e2\u00ee\u0219\u021b\u015f\u0163]+)?|Ordinul\s+pre[sş]edintelui(?:\s+[a-z\u0103\u00e2\u00ee\u0219\u021b\u015f\u0163]+)?|Decretul|OUG|OG|HG|Ordinul)\s*"
        r"(?:nr\.)?\s*(?P<num>\d+(?:\.\d+)?(?:/[A-Z\d\-]+)?)"
        r"(?:/(?P<year>\d{4}))?",
        re.IGNORECASE,
    )

    for match in numbered_regex.finditer(text):
        gd = match.groupdict()
        act_type = normalize_act_type(gd["act_name"])
        raw_num = gd["num"].strip()
        
        # Check if year is appended in num (e.g. 62/2024 or 2.830/C/2017)
        year = None
        if gd.get("year"):
            year = int(gd["year"])
            num = raw_num
        elif "/" in raw_num:
            parts = raw_num.split("/")
            if parts[-1].isdigit() and len(parts[-1]) == 4:
                year = int(parts[-1])
                num = "/".join(parts[:-1])
            else:
                num = raw_num
        else:
            num = raw_num

        art = gd.get("art")
        alin = gd.get("alin")
        if alin:
            alin = re.sub(r"[\(\)]", "", alin).strip()
        anexa = gd.get("anexa")
        cap = gd.get("cap")

        parts = [act_type, f"{num}/{year}" if year else num]
        if anexa:
            parts.append(f"Anexa {anexa}")
        if cap:
            parts.append(f"Cap. {cap}")
        if art:
            parts.append(f"art. {art}")
        if alin:
            parts.append(f"alin. ({alin})")

        canonical = " ".join(parts)
        key = (decision_id, canonical)
        if key not in seen:
            seen.add(key)
            citations.append({
                "source_decision_id": decision_id,
                "target_type": "LAW",
                "act_type": act_type,
                "act_number": str(num),
                "act_year": year,
                "article_number": str(art) if art else None,
                "paragraph_number": str(alin) if alin else None,
                "annex": str(anexa) if anexa else None,
                "chapter": str(cap) if cap else None,
                "canonical_citation": canonical,
                "relationship_type": "applies",
            })

    # 2. Standalone Code citations (Codul civil, penal, etc.)
    code_regex = re.compile(
        r"(?:(?:prevăzut\s+de|dispoziţiile|dispozițiile|prevederile|conform|potrivit)\s+)?"
        r"(?:art\.|articolul|articolului)\s*(?P<art>\d+(?:\^\d+)?(?:\s*lit\.\s*[a-z\)]+)?)"
        r"(?:\s*(?:alin\.|alineatul|alineatele|alineatului)\s*(?P<alin>\(?\d+(?:\)?(?:\s*(?:și|şi|,)\s*\(?\d+\)?)*)?))?"
        r"\s*(?:din|al)\s*"
        r"(?P<code>(?:noul|vechiul)?\s*Cod(?:ul)?\s+(?:de\s+procedur[aă]\s+civil[aă]|de\s+procedur[aă]\s+penal[aă]|civil|penal|fiscal|de\s+procedur[aă]\s+fiscal[aă]|muncii|administrativ|aerian|silvic|vamal)|Constitu[tţ]ia\s+Rom[aâ]niei)",
        re.IGNORECASE,
    )

    for match in code_regex.finditer(text):
        gd = match.groupdict()
        code_name = gd["code"].strip().lower()
        code_canonical = CODE_MAP.get(code_name, gd["code"].strip())
        art = gd.get("art")
        alin = gd.get("alin")
        if alin:
            alin = re.sub(r"[\(\)]", "", alin).strip()

        parts = [code_canonical]
        if art:
            parts.append(f"art. {art}")
        if alin:
            parts.append(f"alin. ({alin})")

        canonical = " ".join(parts)
        key = (decision_id, canonical)
        if key not in seen:
            seen.add(key)
            citations.append({
                "source_decision_id": decision_id,
                "target_type": "CODE",
                "act_type": "COD",
                "act_number": code_canonical,
                "act_year": None,
                "article_number": str(art) if art else None,
                "paragraph_number": str(alin) if alin else None,
                "annex": None,
                "chapter": None,
                "canonical_citation": canonical,
                "relationship_type": "applies",
            })

    # 3. Named Regulations (e.g. Regulamentul privind concediile judecătorilor)
    reg_regex = re.compile(
        r"(?:(?:prevăzut\s+de|dispoziţiile|dispozițiile|prevederile|conform|potrivit)\s+)?"
        r"(?:art\.|articolul|articolului)\s*(?P<art>\d+(?:\^\d+)?)"
        r"(?:\s*(?:alin\.|alineatul|alineatului)\s*(?P<alin>\(?\d+\)?))?"
        r"\s*(?:din|al|în\s+condițiile|în\s+condiţiile)\s*"
        r"(?P<reg>Regulament(?:ul)?\s+privind\s+[a-z\u0103\u00e2\u00ee\u0219\u021b\u015f\u0163\s]+?)(?:,|\.|\s+cu|\s+aprobat|\s+din|$)",
        re.IGNORECASE,
    )

    for match in reg_regex.finditer(text):
        gd = match.groupdict()
        reg_title = gd["reg"].strip()
        art = gd.get("art")
        alin = gd.get("alin")
        if alin:
            alin = re.sub(r"[\(\)]", "", alin).strip()

        canonical = f"{reg_title} art. {art}" + (f" alin. ({alin})" if alin else "")
        key = (decision_id, canonical)
        if key not in seen:
            seen.add(key)
            citations.append({
                "source_decision_id": decision_id,
                "target_type": "REGULATION",
                "act_type": "REGULAMENT",
                "act_number": reg_title,
                "act_year": None,
                "article_number": str(art) if art else None,
                "paragraph_number": str(alin) if alin else None,
                "annex": None,
                "chapter": None,
                "canonical_citation": canonical,
                "relationship_type": "applies",
            })

    # 4. Constitutional & High Court Case Law (Decizia CCR, Decizia ÎCCJ)
    court_regex = re.compile(
        r"(?P<court>Decizi(?:a|ei)\s+(?:Cur[tţ]ii\s+Constitu[tţ]ionale|C\.C\.R\.|CCR|Î\.C\.C\.J\.|ÎCCJ|Înaltei\s+Cur[tţ]i(?:\s+de\s+Casa[tţ]ie\s+[sş]i\s+Justi[tţ]ie)?))\s*"
        r"(?:nr\.)?\s*(?P<num>\d+(?:/\d{4})?)",
        re.IGNORECASE,
    )

    for match in court_regex.finditer(text):
        gd = match.groupdict()
        court_raw = gd["court"].strip()
        court_type = "DECIZIE_CCR" if any(c in court_raw for c in ["Constituțională", "C.C.R.", "CCR"]) else "DECIZIE_ICCJ"
        num = gd["num"].strip()
        
        canonical = f"{'Decizia CCR' if court_type == 'DECIZIE_CCR' else 'Decizia ÎCCJ'} nr. {num}"
        key = (decision_id, canonical)
        if key not in seen:
            seen.add(key)
            citations.append({
                "source_decision_id": decision_id,
                "target_type": "CCR_DECISION" if court_type == "DECIZIE_CCR" else "ICCJ_DECISION",
                "act_type": court_type,
                "act_number": num,
                "act_year": int(num.split("/")[1]) if "/" in num and num.split("/")[1].isdigit() else None,
                "article_number": None,
                "paragraph_number": None,
                "annex": None,
                "chapter": None,
                "canonical_citation": canonical,
                "relationship_type": "applies",
            })

    return citations
