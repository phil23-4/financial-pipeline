from types import SimpleNamespace
from unittest.mock import patch

from fin_pipeline.utils.ocr_engine import extract_text_via_ocr
from fin_pipeline.utils.pdf_parser import _camelot_tables


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


def test_ocr_returns_empty_string_when_document_cannot_open():
    with patch(
        "fin_pipeline.utils.ocr_engine.fitz.open", side_effect=OSError("bad PDF")
    ):
        assert extract_text_via_ocr("report.pdf") == ""
