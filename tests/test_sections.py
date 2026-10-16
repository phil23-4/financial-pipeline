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
