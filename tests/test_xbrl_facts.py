from fin_pipeline.utils.xbrl_facts import extract_xbrl_facts


def test_extract_xbrl_facts_from_inline_nonfraction_tags():
    html = """
    <xbrli:context id="current">
      <xbrli:entity><xbrli:segment>
        <xbrldi:explicitMember dimension="us-gaap:ProductOrServiceAxis">
          us-gaap:ProductsMember
        </xbrldi:explicitMember>
      </xbrli:segment></xbrli:entity>
    </xbrli:context>
    <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
      contextRef="current" scale="6">125</ix:nonFraction>
    <ix:nonFraction name="us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
      contextRef="current" scale="3">4.5</ix:nonFraction>
    <ix:nonFraction name="us-gaap:ResearchAndDevelopmentExpense"
      contextRef="current">2.25</ix:nonFraction>
    <ix:nonFraction name="us-gaap:Assets" contextRef="current">900</ix:nonFraction>
    """

    facts = extract_xbrl_facts(html)

    assert facts["revenues"] == 125_000_000
    assert facts["capitalExpenditures"] == 4_500
    assert facts["researchAndDevelopmentExpense"] == 2.25
    assert facts["assets"] == 900
    assert facts["segmentRevenue"] == 125_000_000
    assert facts["intangibleAssets"] is None


def test_extract_xbrl_facts_without_xbrl_returns_none_values():
    facts = extract_xbrl_facts("<html><body>Plain filing text</body></html>")

    assert facts == {
        "revenues": None,
        "capitalExpenditures": None,
        "researchAndDevelopmentExpense": None,
        "intangibleAssets": None,
        "assets": None,
        "segmentRevenue": None,
    }
