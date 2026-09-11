import pytest
from etl.parser_dom import RomanianLawDomParser, parse_article_number_and_variant


def test_article_variant_parsing():
    assert parse_article_number_and_variant("Articolul 1357") == (1357, None, "Art. 1357")
    assert parse_article_number_and_variant("Art. 2^1") == (2, "^1", "Art. 2^1")
    assert parse_article_number_and_variant("Articolul 188 bis") == (188, "bis", "Art. 188 bis")
    assert parse_article_number_and_variant("Articolul unic") == (None, "unic", "Art. unic")
    assert parse_article_number_and_variant("Art. I") == (1, None, "Art. I")


def test_dom_parser_structure():
    sample_html = """
    <div id="infoactinfoact">
      <div class="S_LGI">LEGE nr. 999 din 15 iulie 2026</div>
      <div class="S_EMT_BDY">PARLAMENTUL ROMÂNIEI</div>
      <span class="S_ART">
        <span class="S_ART_TTL">Articolul 1</span>
        <span class="S_ART_DEN">Obiectul reglementării</span>
        <span class="S_ART_BDY">
          <span class="S_ALN">
            <span class="S_ALN_TTL">(1)</span>
            <span class="S_ALN_BDY">
              <span class="S_LIT">
                <span class="S_LIT_TTL">a)</span>
                <span class="S_LIT_BDY">prima condiție de aplicare;</span>
              </span>
              <span class="S_LIT">
                <span class="S_LIT_TTL">b)</span>
                <span class="S_LIT_BDY">a doua condiție de aplicare.</span>
              </span>
            </span>
          </span>
          <span class="S_ALN">
            <span class="S_ALN_TTL">(2)</span>
            <span class="S_ALN_BDY">Prezenta normă se aplică unitar.</span>
          </span>
        </span>
      </span>
    </div>
    """
    parser = RomanianLawDomParser()
    doc = parser.parse_html(sample_html)

    assert doc is not None
    assert doc.document_type == "LEGE"
    assert doc.document_number == "999"
    assert doc.issuer == "PARLAMENTUL ROMÂNIEI"
    assert len(doc.articles) == 1

    art = doc.articles[0]
    assert art.article_number == 1
    assert art.article_citation == "Art. 1"
    assert art.marginal_name == "Obiectul reglementării"

    # Verify granular paragraphs: 2 litere + 1 simple alineat = 3 paragraphs
    assert len(art.paragraphs) == 3
    assert art.paragraphs[0].paragraph_citation == "Art. 1 alin. (1) lit. a)"
    assert art.paragraphs[0].content == "prima condiție de aplicare;"
    assert art.paragraphs[1].paragraph_citation == "Art. 1 alin. (1) lit. b)"
    assert art.paragraphs[2].paragraph_citation == "Art. 1 alin. (2)"
    assert art.paragraphs[2].content == "Prezenta normă se aplică unitar."


def test_fallback_parser():
    sample_raw = """LEGE nr. 100 din 2026
Art. 1. - (1) Primul alineat. (2) Al doilea alineat.
Art. 2. - Text simplu de articol."""

    parser = RomanianLawDomParser()
    doc = parser.parse_html(sample_raw)

    assert doc is not None
    assert len(doc.articles) == 2
    assert doc.articles[0].article_number == 1
    assert len(doc.articles[0].paragraphs) == 2
    assert doc.articles[0].paragraphs[0].paragraph_citation == "Art. 1 alin. (1)"
    assert doc.articles[1].article_number == 2
