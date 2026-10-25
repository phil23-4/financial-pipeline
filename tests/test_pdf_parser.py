import json
from types import SimpleNamespace
from unittest.mock import patch

from fin_pipeline.models.schemas import ExchangeFilingModel
from fin_pipeline.utils.metadata import extract_metadata_from_text
from fin_pipeline.utils.pdf_parser import (
    _camelot_tables,
    _detect_language,
    extract_filing_metadata,
    parse_pdf_layout,
)


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
    assert result[0]["rows"] == [["Cash", "10"]]
    assert result[0]["rowCount"] == 1

    record = ExchangeFilingModel(
        filingId="test",
        companyTicker="TEST",
        stockCode="TEST",
        exchange="TEST",
        filingType="REPORT",
        source="LOCAL",
        documentTables=json.dumps(result),
    ).model_dump()
    assert json.loads(record["documentTables"])[0]["accuracy"] == 97.5


def test_camelot_tables_falls_back_to_stream_and_normalizes_cells():
    lattice = patch(
        "fin_pipeline.utils.pdf_parser.camelot.read_pdf",
        side_effect=[
            RuntimeError("lattice unavailable"),
            [
                SimpleNamespace(
                    df=SimpleNamespace(
                        values=SimpleNamespace(
                            tolist=lambda: [[None, "Value | Total"], [" Cash ", "10\n"]]
                        )
                    ),
                    page="3",
                    parsing_report={},
                )
            ],
        ],
    )

    with lattice as read_pdf:
        result = _camelot_tables("report.pdf")

    assert read_pdf.call_count == 2
    assert result[0]["headers"] == ["", "Value \\| Total"]
    assert result[0]["rowCount"] == 1
    assert result[0]["accuracy"] is None
    assert "| Cash | 10 |" in result[0]["markdown"]


def test_camelot_tables_skips_empty_tables():
    table = SimpleNamespace(
        df=SimpleNamespace(
            values=SimpleNamespace(tolist=lambda: [[None, ""], ["", None]])
        ),
        page="1",
        parsing_report={"accuracy": 10.0},
    )

    with patch("fin_pipeline.utils.pdf_parser.camelot.read_pdf", return_value=[table]):
        assert _camelot_tables("report.pdf") == []


def test_camelot_tables_preserves_data_when_first_row_has_empty_cell():
    long_data_value = "123456789012345678901234567890"
    table = SimpleNamespace(
        df=SimpleNamespace(
            values=SimpleNamespace(
                tolist=lambda: [
                    ["2021", "", long_data_value],
                    ["2022", "120"],
                    ["2023", "130"],
                ]
            )
        ),
        page="1",
        parsing_report={},
    )

    with patch(
        "fin_pipeline.utils.pdf_parser.camelot.read_pdf",
        return_value=[table],
    ):
        result = _camelot_tables("report.pdf")

    assert result[0]["headers"] == ["Column 1", "Column 2", "Column 3"]
    assert result[0]["rows"][0] == ["2021", "", long_data_value]
    assert result[0]["rowCount"] == 3


def test_camelot_tables_skips_malformed_table_and_keeps_valid_tables():
    malformed = SimpleNamespace(df=SimpleNamespace(values=None))
    valid = SimpleNamespace(
        df=SimpleNamespace(
            values=SimpleNamespace(tolist=lambda: [["Asset", "Value"], ["Cash", "10"]])
        ),
        page="2",
        parsing_report={},
    )
    warnings = []

    with patch(
        "fin_pipeline.utils.pdf_parser.camelot.read_pdf",
        return_value=[malformed, valid],
    ):
        result = _camelot_tables("report.pdf", warnings)

    assert len(result) == 1
    assert result[0]["headers"] == ["Asset", "Value"]
    assert "table 0 skipped" in warnings[0]


def test_camelot_tables_skips_text_restricted_pdf():
    from playa.exceptions import PDFTextExtractionNotAllowed

    with patch(
        "fin_pipeline.utils.pdf_parser.camelot.read_pdf",
        side_effect=PDFTextExtractionNotAllowed(
            "Text extraction is not allowed"
        ),
    ):
        assert _camelot_tables("restricted.pdf") == []


def test_pdf_properties_keep_higher_confidence_than_text_metadata():
    document = SimpleNamespace(metadata={"title": "Apple Inc.", "subject": "10-K"})

    result = __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["extract_filing_metadata"]
    ).extract_filing_metadata(
        "Apple Corporation 10-K",
        document,
    )

    assert result["stockName"] == "Apple Inc."
    assert result["metadataSources"]["stockName"] == "pdf_properties"


def test_generic_pdf_properties_do_not_override_text_metadata():
    document = SimpleNamespace(
        metadata={
            "title": "Adobe InDesign - Untitled",
            "subject": "Adobe InDesign",
            "keywords": "Adobe InDesign, report",
        }
    )

    result = __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["extract_filing_metadata"]
    ).extract_filing_metadata(
        "Acme Holdings Annual Report 2024\nFor the year ended 31 December 2024",
        document,
    )

    assert result["stockName"] == "Acme Holdings"
    assert result["filingType"] == "Annual Report"
    assert result["filingDate"] == "2024-12-31"
    assert result["metadataSources"]["stockName"] == "pdf_text_regex"


def test_pdf_property_keywords_only_provide_a_normalized_exchange():
    document = SimpleNamespace(metadata={"keywords": "NYSE, annual report"})

    result = __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["extract_filing_metadata"]
    ).extract_filing_metadata("Annual Report", document)

    assert result["exchange"] == "NYSE"
    assert result["metadataSources"]["exchange"] == "pdf_properties"


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


def test_metadata_extracts_company_name_from_cover_page_title():
    result = extract_metadata_from_text(
        "Acme Holdings Annual Report 2024",
        "pdf_text_regex",
    )

    assert result["stockName"] == "Acme Holdings"
    assert result["filingType"] == "Annual Report"


def test_pdf_parser_extracts_company_name_from_document_title_metadata():
    document = SimpleNamespace(metadata={"title": "Northwind Group Annual Report 2024"})
    result = __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["extract_filing_metadata"]
    ).extract_filing_metadata("Northwind Group Annual Report 2024", document)

    assert result["stockName"] == "Northwind Group"


def test_pdf_parser_returns_metadata_without_tables():
    document = SimpleNamespace(metadata={}, page_count=3)
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
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
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf")

    assert result["tables"] == []
    assert result["table_cnt"] == 0
    assert result["filingType"] == "Annual Report"
    assert result["page_count"] == 3
    assert result["warnings"] == []
    assert result["chunks"][0]["pageNumber"] > 0
    assert result["chunks"][0]["chunkIndex"] == 0
    open_pdf.assert_called_once_with("report.pdf")


def test_pdf_parser_detects_english_and_returns_page_count():
    document = SimpleNamespace(metadata={}, page_count=3)
    english_text = (
        "The company reports strong financial results across its global markets. "
        "Revenue increased during the year, while operating costs remained stable. "
    ) * 8
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": english_text}],
        ),
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
        patch(
            "fin_pipeline.utils.pdf_parser.extract_filing_metadata",
            return_value={},
        ),
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf", use_ocr=False)

    assert result["page_count"] == 3
    assert result["language"] == "en"


def test_pdf_parser_period_end_and_fiscal_month_from_filing_date():
    document = SimpleNamespace(metadata={}, page_count=1)
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": "Annual report text"}],
        ),
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
        patch(
            "fin_pipeline.utils.pdf_parser.extract_filing_metadata",
            return_value={"filingDate": "2023-12-31"},
        ),
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf", use_ocr=False)

    assert result["periodEndDate"] == "2023-12-31"
    assert result["fiscalYearEndMonth"] == 12


def test_pdf_parser_detects_amendment_from_filing_type():
    with patch(
        "fin_pipeline.utils.pdf_parser.extract_metadata_from_text",
        return_value={"filingType": "10-K/A"},
    ):
        amendment = extract_filing_metadata("Annual report")
    with patch(
        "fin_pipeline.utils.pdf_parser.extract_metadata_from_text",
        return_value={"filingType": "ANNUAL_REPORT"},
    ):
        annual_report = extract_filing_metadata("Annual report")

    assert amendment["isAmendment"] is True
    assert annual_report["isAmendment"] is False


def test_pdf_language_detection_returns_none_for_short_text():
    assert _detect_language("Too short to classify") is None


def test_pdf_parser_passes_force_ocr_and_native_callback():
    document = SimpleNamespace(metadata={})
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": "Scanned report"}],
        ) as to_markdown,
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
        patch(
            "fin_pipeline.utils.pdf_parser.extract_metadata_from_text",
            return_value={},
        ),
    ):
        open_pdf.return_value.__enter__.return_value = document
        parse_pdf_layout("report.pdf", force_ocr=True)

    assert to_markdown.call_args.kwargs["force_ocr"] is True
    assert to_markdown.call_args.kwargs["ocr_function"]
    assert to_markdown.call_args.args[0] is document


def test_pdf_parser_uses_ocr_selectively_by_default():
    document = SimpleNamespace(metadata={})
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": "Digital report"}],
        ) as to_markdown,
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
    ):
        open_pdf.return_value.__enter__.return_value = document
        parse_pdf_layout("report.pdf")

    assert to_markdown.call_args.kwargs["force_ocr"] is False
    assert to_markdown.call_args.kwargs["use_ocr"] == __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["OCRMode"]
    ).OCRMode.SELECT_REMOVING_OLD
    assert to_markdown.call_args.kwargs["ocr_function"]


def test_pdf_parser_disables_automatic_and_forced_ocr():
    document = SimpleNamespace(metadata={})
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": "Native text"}],
        ) as to_markdown,
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf", force_ocr=False, use_ocr=False)

    assert to_markdown.call_args.kwargs["use_ocr"] == __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["OCRMode"]
    ).OCRMode.NEVER
    assert to_markdown.call_args.kwargs["force_ocr"] is False
    assert "ocr_function" not in to_markdown.call_args.kwargs
    assert result["reason"] == "PyMuPDF4LLM (OCR disabled)"


def test_pdf_parser_keeps_ocr_disabled_during_page_fallback():
    document = SimpleNamespace(metadata={}, page_count=1)
    parse_error = __import__("pymupdf").mupdf.FzErrorArgument(
        "incomplete or truncated stream"
    )
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            side_effect=[parse_error, parse_error, parse_error],
        ) as to_markdown,
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
    ):
        open_pdf.return_value.__enter__.return_value = document
        parse_pdf_layout("report.pdf", force_ocr=False, use_ocr=False)

    assert all(
        call.kwargs["use_ocr"]
        == __import__("fin_pipeline.utils.pdf_parser", fromlist=["OCRMode"]).OCRMode.NEVER
        and call.kwargs["force_ocr"] is False
        and "ocr_function" not in call.kwargs
        for call in to_markdown.call_args_list
    )


def test_pdf_parser_can_skip_expensive_table_extraction():
    document = SimpleNamespace(metadata={})
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            return_value=[{"text": "Annual Report"}],
        ),
        patch("fin_pipeline.utils.pdf_parser._camelot_tables") as camelot_tables,
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf", extract_tables=False)

    camelot_tables.assert_not_called()
    assert result["tables"] == []
    assert "Camelot table extraction disabled" in result["warnings"]


def test_pdf_parser_falls_back_to_native_text_when_ocr_fails():
    document = SimpleNamespace(metadata={})
    ocr_error = __import__("pymupdf").mupdf.FzErrorArgument(
        "Invalid bandwriter header dimensions/setup"
    )
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            side_effect=[
                ocr_error,
                [{"text": "Acme Holdings Annual Report 2024"}],
            ],
        ) as to_markdown,
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf")

    assert result["text"] == "Acme Holdings Annual Report 2024"
    assert result["reason"] == "PyMuPDF4LLM (OCR fallback)"
    assert "OCR extraction failed" in result["warnings"][0]
    assert to_markdown.call_count == 2
    assert to_markdown.call_args_list[1].kwargs["use_ocr"] == __import__(
        "fin_pipeline.utils.pdf_parser", fromlist=["OCRMode"]
    ).OCRMode.NEVER


def test_pdf_parser_marks_empty_native_fallback_as_partial():
    document = SimpleNamespace(metadata={})
    ocr_error = __import__("pymupdf").mupdf.FzErrorArgument(
        "Invalid bandwriter header dimensions/setup"
    )
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            side_effect=[ocr_error, []],
        ),
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf")

    assert result["text"] == ""
    assert result["reason"] == "PyMuPDF4LLM (OCR fallback; no native text)"
    assert any("No usable text" in warning for warning in result["warnings"])


def test_pdf_parser_extracts_healthy_pages_when_document_parse_fails():
    document = SimpleNamespace(metadata={}, page_count=2)
    parse_error = __import__("pymupdf").mupdf.FzErrorArgument(
        "incomplete or truncated stream"
    )
    with (
        patch("fin_pipeline.utils.pdf_parser.pymupdf.open") as open_pdf,
        patch(
            "fin_pipeline.utils.pdf_parser.pymupdf4llm.to_markdown",
            side_effect=[
                parse_error,
                parse_error,
                parse_error,
                [{"text": "Healthy page"}],
            ],
        ) as to_markdown,
        patch("fin_pipeline.utils.pdf_parser._camelot_tables", return_value=[]),
    ):
        open_pdf.return_value.__enter__.return_value = document
        result = parse_pdf_layout("report.pdf")

    assert result["text"] == "Healthy page"
    assert result["reason"] == "PyMuPDF4LLM (page-isolated fallback)"
    assert any("PDF page 1 skipped" in warning for warning in result["warnings"])
    assert to_markdown.call_count == 4
