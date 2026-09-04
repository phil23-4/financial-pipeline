from types import SimpleNamespace
from unittest.mock import patch

from fin_pipeline.models.schemas import ExchangeFilingModel
from fin_pipeline.utils.metadata import extract_metadata_from_text
from fin_pipeline.utils.ocr_engine import extract_text_via_ocr
from fin_pipeline.utils.pdf_parser import _camelot_tables, parse_pdf_layout


def test_camelot_tables_uses_parsing_report_accuracy():
    table = SimpleNamespace(
        df=SimpleNamespace(
            values=SimpleNamespace(tolist=lambda: [["Asset", "Value"], ["Cash", "10"]])
        ),
        page="2",
        parsing_report={"accuracy": 97.5},
    )

    with patch("fin_pipeline.utils.pdf_parser.camelot.read_pdf", return_value=[table]):
        result = _camelot_tables("report.pdf")

    assert result[0]["accuracy"] == 97.5
    assert result[0]["headers"] == ["Asset", "Value"]
    assert result[0]["rowCount"] == 1

    record = ExchangeFilingModel(
        filingId="test",
        companyTicker="TEST",
        stockCode="TEST",
        exchange="TEST",
        filingType="REPORT",
        source="LOCAL",
        documentTables=result,
    ).model_dump()
    assert record["documentTables"][0]["accuracy"] == 97.5


def test_metadata_prefers_labeled_company_name_from_early_pages():
    result = extract_metadata_from_text(
        "Accessories includes Apple-branded products. Apple",
        "pdf_text_regex",
        company_text="**Registrant Name**: Apple Inc.\nAnnual Report",
    )

    assert result["stockName"] == "Apple Inc."
    assert result["metadataConfidence"]["stockName"] == 0.92


def test_metadata_rejects_narrative_company_match():
    result = extract_metadata_from_text(
        "Banca Transilvania S.A. was founded in 1993.",
        "pdf_text_regex",
    )

    assert result["stockName"] is None


def test_pdf_parser_returns_metadata_without_tables():
    reader = SimpleNamespace(metadata={})
    with (
        patch("fin_pipeline.utils.pdf_parser.PdfReader", return_value=reader),
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": "Annual Report\n2023"}],
        ),
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
        patch(
            "fin_pipeline.utils.pdf_parser.extract_metadata_from_text",
            return_value={"filingType": "Annual Report"},
        ),
    ):
        result = parse_pdf_layout("report.pdf")

    assert result["tables"] == []
    assert result["table_cnt"] == 0
    assert result["filingType"] == "Annual Report"


def test_ocr_returns_empty_string_when_document_cannot_open():
    with patch(
        "fin_pipeline.utils.ocr_engine.pymupdf.open", side_effect=OSError("bad PDF")
    ):
        assert extract_text_via_ocr("report.pdf") == ""
