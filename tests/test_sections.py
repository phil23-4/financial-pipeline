from fin_pipeline.utils.sections import (
    label_sections_glossy,
    label_sections_structured,
)


def test_structured_chunks_follow_10k_item_headers():
    chunks = [
        {"text": "# PART I"},
        {"text": "## ITEM 1. Business"},
        {"text": "ITEM 1A. Risk Factors"},
        {"text": "ITEM 7. Management's Discussion and Analysis"},
        {"text": "ITEM 8. Financial Statements"},
        {"text": "Content without another header"},
    ]

    labeled = label_sections_structured(chunks)

    assert [chunk["sectionLabel"] for chunk in labeled] == [
        "business",
        "business",
        "risk_factors",
        "mdna",
        "financial_statements",
        "financial_statements",
    ]


def _20f_fixture_chunks():
    return [
        {
            "text": (
                "FORM 20-F\n"
                "ITEM 3. KEY INFORMATION\n"
                "3.D. Risk Factors\n"
                "We face significant risks."
            )
        },
        {
            "text": (
                "ITEM 4. INFORMATION ON THE COMPANY\n"
                "Our business operates globally."
            )
        },
        {
            "text": (
                "ITEM 5. OPERATING AND FINANCIAL REVIEW AND PROSPECTS\n"
                "Revenue increased."
            )
        },
    ]


def test_20f_labels_risk_factors_from_item_3d():
    labeled = label_sections_structured(_20f_fixture_chunks(), form_type="20-F")

    assert labeled[0]["sectionLabel"] == "risk_factors"
    assert labeled[1]["sectionLabel"] == "business"
    assert labeled[2]["sectionLabel"] == "mdna"


def test_20f_labels_without_explicit_form_type():
    labeled = label_sections_structured(_20f_fixture_chunks())

    assert labeled[0]["sectionLabel"] == "risk_factors"
    assert labeled[1]["sectionLabel"] == "business"
    assert labeled[2]["sectionLabel"] == "mdna"


def test_40f_labels_from_canadian_sections():
    chunks = [
        {"text": "ANNUAL INFORMATION FORM\nRisk Factors\nWe face risks."},
        {"text": "MANAGEMENT'S DISCUSSION AND ANALYSIS\nRevenue grew."},
        {"text": "AUDITED FINANCIAL STATEMENTS\nStatements follow."},
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert labeled[0]["sectionLabel"] == "risk_factors"
    assert labeled[1]["sectionLabel"] == "mdna"
    assert labeled[2]["sectionLabel"] == "financial_statements"


def test_40f_first_match_wins_over_toc():
    chunks = [
        {
            "text": (
                "ANNUAL INFORMATION FORM\n"
                "FOR THE YEAR ENDED DECEMBER 31, 2024\n"
                "TABLE OF CONTENTS\n"
                "Business Overview ........ 5\n"
                "Risk Factors ............ 12\n"
                "Financial Statements .... 47\n"
                "Item 5 ...\n"
            )
        }
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert labeled[0]["sectionLabel"] == "business"


def test_40f_chunk_starting_with_marker_wins():
    chunks = [
        {
            "text": (
                "Annual Information Form\n"
                "Exhibit 99.1\n"
                "TABLE OF CONTENTS\n"
                "Financial Statements .... 47\n"
            )
        }
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert labeled[0]["sectionLabel"] == "business"


def test_40f_suppresses_toc_headings_without_marker():
    chunks = [
        {"text": "Annual Information Form"},
        {
            "text": (
                "TABLE OF CONTENTS\n"
                "Financial Statements\n"
                "Risk Factors\n"
            )
        },
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert [chunk["sectionLabel"] for chunk in labeled] == ["business", "business"]


def test_40f_carries_forward_without_headings():
    chunks = [
        {"text": "Annual Information Form"},
        {"text": "This chunk contains body text and no section heading."},
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert [chunk["sectionLabel"] for chunk in labeled] == ["business", "business"]


def test_40f_marker_state_machine_carries_forward():
    chunks = [
        {"text": "Annual Information Form\nCompany overview..."},
        {"text": "Our business is..."},
        {"text": "Management's Discussion and Analysis\nRevenue..."},
        {"text": "Financial Statements\n..."},
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert [chunk["sectionLabel"] for chunk in labeled] == [
        "business",
        "business",
        "mdna",
        "financial_statements",
    ]


def test_40f_marker_ignores_toc_entries():
    chunks = [
        {
            "text": (
                "Annual Information Form\n"
                "TABLE OF CONTENTS\n"
                "Risk Factors ... 12\n"
                "MD&A ... 20\n"
            )
        },
        {"text": "We are a leading mining company..."},
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert [chunk["sectionLabel"] for chunk in labeled] == ["business", "business"]


def test_40f_marker_boundary_in_middle_of_chunk():
    chunks = [
        {
            "text": (
                "Annual Information Form\n"
                "AIF content...\n"
                "Management's Discussion and Analysis\n"
                "MD&A content..."
            )
        }
    ]

    labeled = label_sections_structured(chunks, form_type="40-F")

    assert labeled[0]["sectionLabel"] == "mdna"


def test_glossy_chunks_use_llm_section_ranges():
    chunks = [{"text": "CEO message"}, {"text": "Strategy"}, {"text": "Outlook"}]

    def llm_callable(prompt: str) -> str:
        assert "Chunk 0: CEO message" in prompt
        return (
            '{"sections":[{"startChunk":0,"endChunk":0,"label":"ceo_letter"},'
            '{"startChunk":1,"endChunk":2,"label":"strategy"}]}'
        )

    labeled = label_sections_glossy(chunks, llm_callable)

    assert [chunk["sectionLabel"] for chunk in labeled] == [
        "ceo_letter",
        "strategy",
        "strategy",
    ]


def test_invalid_glossy_llm_json_leaves_chunks_unlabeled():
    chunks = [{"text": "CEO message"}, {"text": "Strategy"}]

    labeled = label_sections_glossy(chunks, lambda _prompt: "not json")

    assert [chunk["sectionLabel"] for chunk in labeled] == [None, None]
