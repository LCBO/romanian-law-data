"""
High-Precision DOM & AST Parser for Romanian Legislation.
Ported from DOM hierarchy (legislatie.just.ro S_* classes) and AST stack parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional
from selectolax.parser import HTMLParser, Node

ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int:
    total = 0
    prev = 0
    for c in reversed(s.upper()):
        val = ROMAN_VAL.get(c, 0)
        total += -val if val < prev else val
        prev = val
    return total


def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<!---->", "", raw)
    text = re.sub(r"[\xa0\t ]+", " ", text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_article_number_and_variant(raw_ttl: str) -> tuple[Optional[int], Optional[str], str]:
    """
    Extracts article number, variant (e.g. '^1', 'bis'), and canonical citation.
    Examples:
      'Articolul 1357' -> (1357, None, 'Art. 1357')
      'Art. 2^1' -> (2, '^1', 'Art. 2^1')
      'Art. 188 bis' -> (188, 'bis', 'Art. 188 bis')
      'Articolul I' -> (1, None, 'Art. I')
      'Articolul unic' -> (None, 'unic', 'Art. unic')
    """
    ttl = clean_text(raw_ttl)
    m = re.search(
        r"^(?:Art(?:icolul)?\.?\s*)((?:\d+\.)*\d+|[IVXLCM]+|unic)(?:[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\^(\d+)|\s+(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies))?",
        ttl,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"(?:Art(?:icolul)?\.?\s*)((?:\d+\.)*\d+|[IVXLCM]+|unic)(?:[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\^(\d+)|\s+(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies))?",
            ttl,
            re.IGNORECASE,
        )

    if not m:
        return None, None, ttl or "(unparsed)"

    raw_num = m.group(1).replace(".", "")
    sup = m.group(2)
    latin_var = m.group(3)

    variant = None
    if sup:
        variant = f"^{sup}"
    elif latin_var:
        variant = latin_var.lower()
    elif raw_num.lower() == "unic":
        variant = "unic"

    art_num = None
    if raw_num.isdigit():
        art_num = int(raw_num)
        cit_num = str(art_num)
    elif raw_num.upper() in ROMAN_VAL or re.match(r"^[IVXLCM]+$", raw_num, re.IGNORECASE):
        art_num = roman_to_int(raw_num)
        cit_num = raw_num.upper()
    elif raw_num.lower() == "unic":
        art_num = None
        cit_num = "unic"
    else:
        cit_num = raw_num

    if variant and variant != "unic":
        citation = f"Art. {cit_num}{variant if variant.startswith('^') else ' ' + variant}"
    elif variant == "unic":
        citation = "Art. unic"
    else:
        citation = f"Art. {cit_num}"

    return art_num, variant, citation


@dataclass
class ParsedParagraph:
    paragraph_number: Optional[int]
    paragraph_citation: str
    content: str
    point_letter: Optional[str] = None
    sub_point: Optional[str] = None
    marginal_name: Optional[str] = None


@dataclass
class ParsedArticle:
    article_number: Optional[int]
    article_variant: Optional[str]
    article_citation: str
    marginal_name: Optional[str]
    content: str
    paragraphs: List[ParsedParagraph] = field(default_factory=list)
    container_path: Optional[str] = None


@dataclass
class ParsedDocument:
    title: str
    document_type: str
    document_number: Optional[str]
    issuer: Optional[str]
    publication: Optional[str]
    full_text: str
    articles: List[ParsedArticle] = field(default_factory=list)


def has_class(node: Node, class_name: str) -> bool:
    classes = node.attributes.get("class", "").split()
    return class_name in classes


def find_first_by_class(node: Node, class_name: str) -> Optional[Node]:
    for child in node.css(f".{class_name}"):
        if has_class(child, class_name):
            return child
    return None


def find_all_by_class(node: Node, class_name: str) -> List[Node]:
    return [el for el in node.css(f".{class_name}") if has_class(el, class_name)]


class RomanianLawDomParser:
    """DOM-based parser exploiting native S_* classes from legislatie.just.ro."""

    def parse_html(self, html_content: str, url: str = "") -> Optional[ParsedDocument]:
        parser = HTMLParser(html_content)
        info_root = parser.css_first("#infoactinfoact, #infoact, .infoact, #continut, #content")
        if not info_root:
            info_root = parser.body or parser.root

        if not info_root:
            return None

        # Check if structural S_ART spans exist
        art_nodes = find_all_by_class(info_root, "S_ART")
        if not art_nodes:
            return self._parse_fallback(info_root.text(strip=True), html_content)

        # 1. Document metadata
        hdr = (
            find_first_by_class(info_root, "S_HDR")
            or find_first_by_class(info_root, "S_LGI")
            or info_root.css_first(".titlu, h1, h2")
        )
        title_text = hdr.text(strip=True) if hdr else ""
        if not title_text:
            title_node = parser.css_first("title, .page-header")
            title_text = title_node.text(strip=True) if title_node else "Act Normativ"

        # Emitent & Publicatie
        emt_node = find_first_by_class(info_root, "S_EMT_BDY") or find_first_by_class(info_root, "S_EMT")
        issuer = emt_node.text(strip=True) if emt_node else None

        pub_node = find_first_by_class(info_root, "S_PUB_BDY") or find_first_by_class(info_root, "S_PUB")
        publication = pub_node.text(strip=True) if pub_node else None

        doc_type, doc_num = self._extract_doc_type_number(title_text)

        # 2. Extract Articles & Granular Paragraphs
        articles: List[ParsedArticle] = []

        for art_node in art_nodes:
            ttl_node = find_first_by_class(art_node, "S_ART_TTL")
            den_node = find_first_by_class(art_node, "S_ART_DEN")
            bdy_node = find_first_by_class(art_node, "S_ART_BDY") or art_node

            raw_ttl = ttl_node.text(strip=True) if ttl_node else ""
            if not raw_ttl:
                raw_ttl = art_node.text(strip=True)[:30]

            art_num, art_var, art_cit = parse_article_number_and_variant(raw_ttl)
            marginal_name = clean_text(den_node.text(strip=True)) if den_node else None

            # Look for alineate (S_ALN)
            aln_nodes = find_all_by_class(bdy_node, "S_ALN")
            paragraphs: List[ParsedParagraph] = []

            if aln_nodes:
                for aln_idx, aln in enumerate(aln_nodes, start=1):
                    aln_ttl_node = find_first_by_class(aln, "S_ALN_TTL")
                    aln_bdy_node = find_first_by_class(aln, "S_ALN_BDY") or aln

                    aln_ttl_text = aln_ttl_node.text(strip=True) if aln_ttl_node else ""
                    m_num = re.search(r"\((\d+)\)", aln_ttl_text)
                    para_num = int(m_num.group(1)) if m_num else aln_idx
                    base_cit = f"{art_cit} alin. ({para_num})"

                    # Check for litere / puncte inside alineat
                    lit_nodes = find_all_by_class(aln_bdy_node, "S_LIT")
                    pct_nodes = find_all_by_class(aln_bdy_node, "S_PCT")

                    if lit_nodes:
                        for lit in lit_nodes:
                            lit_ttl = find_first_by_class(lit, "S_LIT_TTL")
                            lit_bdy = find_first_by_class(lit, "S_LIT_BDY") or lit
                            l_letter = lit_ttl.text(strip=True).rstrip(")").strip() if lit_ttl else ""
                            l_text = clean_text(lit_bdy.text(strip=True))
                            if l_text:
                                paragraphs.append(
                                    ParsedParagraph(
                                        paragraph_number=para_num,
                                        paragraph_citation=f"{base_cit} lit. {l_letter})",
                                        content=l_text,
                                        point_letter=l_letter,
                                        marginal_name=marginal_name,
                                    )
                                )
                    elif pct_nodes:
                        for pct in pct_nodes:
                            pct_ttl = find_first_by_class(pct, "S_PCT_TTL")
                            pct_bdy = find_first_by_class(pct, "S_PCT_BDY") or pct
                            p_num = pct_ttl.text(strip=True).rstrip(".").strip() if pct_ttl else ""
                            p_text = clean_text(pct_bdy.text(strip=True))
                            if p_text:
                                paragraphs.append(
                                    ParsedParagraph(
                                        paragraph_number=para_num,
                                        paragraph_citation=f"{base_cit} pct. {p_num}.",
                                        content=p_text,
                                        sub_point=p_num,
                                        marginal_name=marginal_name,
                                    )
                                )
                    else:
                        aln_text = clean_text(aln_bdy_node.text(strip=True))
                        if aln_text:
                            paragraphs.append(
                                ParsedParagraph(
                                    paragraph_number=para_num,
                                    paragraph_citation=base_cit,
                                    content=aln_text,
                                    marginal_name=marginal_name,
                                )
                            )
            else:
                raw_bdy = clean_text(bdy_node.text(strip=True))
                inline_alns = list(re.finditer(r"\((\d+)\)\s*(.*?)(?=\(\d+\)|$)", raw_bdy, re.DOTALL))
                if len(inline_alns) >= 2:
                    for m in inline_alns:
                        p_num = int(m.group(1))
                        p_txt = clean_text(m.group(2))
                        if p_txt:
                            paragraphs.append(
                                ParsedParagraph(
                                    paragraph_number=p_num,
                                    paragraph_citation=f"{art_cit} alin. ({p_num})",
                                    content=p_txt,
                                    marginal_name=marginal_name,
                                )
                            )
                else:
                    paragraphs.append(
                        ParsedParagraph(
                            paragraph_number=None,
                            paragraph_citation=art_cit,
                            content=raw_bdy,
                            marginal_name=marginal_name,
                        )
                    )

            art_full_text = clean_text(bdy_node.text(strip=True))
            articles.append(
                ParsedArticle(
                    article_number=art_num,
                    article_variant=art_var,
                    article_citation=art_cit,
                    marginal_name=marginal_name,
                    content=art_full_text,
                    paragraphs=paragraphs,
                )
            )

        full_doc_text = clean_text(info_root.text(strip=True))
        return ParsedDocument(
            title=title_text,
            document_type=doc_type,
            document_number=doc_num,
            issuer=issuer,
            publication=publication,
            full_text=full_doc_text,
            articles=articles,
        )

    def _extract_doc_type_number(self, title: str) -> tuple[str, Optional[str]]:
        t_clean = clean_text(title).upper()
        types = [
            "ORDONANȚĂ DE URGENȚĂ",
            "ORDONANTA DE URGENTA",
            "HOTĂRÂRE DE GUVERN",
            "HOTARARE DE GUVERN",
            "HOTĂRÂRE",
            "HOTARARE",
            "DECRET-LEGE",
            "DECRET",
            "DECIZIE",
            "ORDIN",
            "LEGE",
            "REGULAMENT",
            "CONSTITUȚIE",
            "CODUL CIVIL",
            "CODUL PENAL",
            "CODUL FISCAL",
            "CODUL MUNCII",
        ]
        doc_type = "ACT"
        for tp in types:
            if tp in t_clean:
                doc_type = tp
                break

        num_m = re.search(r"nr\.\s*([\d.]+)", title, re.IGNORECASE)
        doc_num = num_m.group(1).replace(".", "") if num_m else None
        return doc_type, doc_num

    def _parse_fallback(self, text: str, html: str = "") -> ParsedDocument:
        lines = [clean_text(line) for line in text.split("\n") if clean_text(line)]
        title = lines[0] if lines else "Act Normativ"
        doc_type, doc_num = self._extract_doc_type_number(title)

        art_regex = re.compile(
            r"^(?:Art(?:icolul)?\.?\s*)((?:\d+\.)*\d+|[IVXLCM]+|unic)(?:[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\^(\d+)|\s+(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies))?\.?\s*[-—–.]?[ \t]*(.*)",
            re.IGNORECASE,
        )

        articles: List[ParsedArticle] = []
        current_art: Optional[ParsedArticle] = None
        current_lines: List[str] = []

        for line in lines:
            m = art_regex.match(line)
            if m:
                if current_art:
                    current_art.content = "\n".join(current_lines).strip()
                    self._populate_fallback_paragraphs(current_art)
                    articles.append(current_art)
                    current_lines = []

                art_num, art_var, art_cit = parse_article_number_and_variant(line[:40])
                rest = m.group(4).strip() if m.group(4) else ""
                current_art = ParsedArticle(
                    article_number=art_num,
                    article_variant=art_var,
                    article_citation=art_cit,
                    marginal_name=None,
                    content="",
                )
                if rest:
                    current_lines.append(rest)
            else:
                if current_art:
                    current_lines.append(line)

        if current_art:
            current_art.content = "\n".join(current_lines).strip()
            self._populate_fallback_paragraphs(current_art)
            articles.append(current_art)

        if not articles:
            single_para = ParsedParagraph(
                paragraph_number=None,
                paragraph_citation="(unparsed)",
                content=text,
            )
            articles.append(
                ParsedArticle(
                    article_number=None,
                    article_variant=None,
                    article_citation="(unparsed)",
                    marginal_name=None,
                    content=text,
                    paragraphs=[single_para],
                )
            )

        return ParsedDocument(
            title=title,
            document_type=doc_type,
            document_number=doc_num,
            issuer=None,
            publication=None,
            full_text=text,
            articles=articles,
        )

    def _populate_fallback_paragraphs(self, art: ParsedArticle):
        text = art.content
        inline_alns = list(re.finditer(r"\((\d+)\)\s*(.*?)(?=\(\d+\)|$)", text, re.DOTALL))
        if len(inline_alns) >= 2:
            for m in inline_alns:
                p_num = int(m.group(1))
                p_txt = clean_text(m.group(2))
                if p_txt:
                    art.paragraphs.append(
                        ParsedParagraph(
                            paragraph_number=p_num,
                            paragraph_citation=f"{art.article_citation} alin. ({p_num})",
                            content=p_txt,
                        )
                    )
        else:
            art.paragraphs.append(
                ParsedParagraph(
                    paragraph_number=None,
                    paragraph_citation=art.article_citation,
                    content=text,
                )
            )
