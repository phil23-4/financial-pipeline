from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from httpx import ReadTimeout

from fin_pipeline.cli import main
from fin_pipeline.sec_edgar import (
    SecEdgarFetchError,
    _classify_exhibit_by_content,
    _fetch_filing_html,
    _get_40f_exhibit_content,
    read_company_csv,
    stream_filings_from_csv,
)


def test_read_company_csv_normalizes_rows(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,forms,year,max_filings\nAAPL,10-K,2024,2\n")

    assert list(read_company_csv(str(csv_path))) == [
        {
            "ticker": "AAPL",
            "forms": "10-K",
            "year": "2024",
            "max_filings": "2",
        }
    ]


def test_read_company_csv_requires_ticker_or_cik(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,cik\n,\n")

    with pytest.raises(ValueError, match="requires ticker or cik"):
        list(read_company_csv(str(csv_path)))


def test_stream_filters_override_csv_filters(tmp_path, monkeypatch):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,forms,year\nAAPL,8-K,2020\n")
    filing = SimpleNamespace(
        accession_no="0000320193-25-000079",
        html=lambda: "<html>filing</html>",
        form="10-K",
        company="Apple Inc.",
        report_date="2025-09-27",
        filing_date="2025-11-01",
        cik=320193,
    )
    company = MagicMock()
    company.get_ticker.return_value = "AAPL"
    company.get_filings.return_value = [filing]
    fake_edgar = SimpleNamespace(
        Company=MagicMock(return_value=company),
        set_identity=MagicMock(),
    )
    monkeypatch.setenv("EDGAR_IDENTITY", "Test test@example.com")

    with patch.dict("sys.modules", {"edgar": fake_edgar}):
        results = list(
            stream_filings_from_csv(
                str(csv_path), year_range=(2018, 2025), forms=["10-K", "10-Q"]
            )
        )

    company.get_filings.assert_called_once_with(
        form=["10-K", "10-Q"],
        year=list(range(2018, 2026)),
        amendments=False,
        trigger_full_load=True,
    )
    assert results[0][1]["filingId"] == "sec_AAPL_0000320193-25-000079"
    assert results[0][1]["sourceType"] == "structured_filing"
    assert results[0][1]["reportingYear"] == 2025


def test_stream_retries_timeout_while_loading_filings(tmp_path, monkeypatch):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nAAPL\n")
    company = MagicMock()
    company.get_ticker.return_value = "AAPL"
    company.get_filings.side_effect = [
        ReadTimeout("SEC submissions timed out"),
        ReadTimeout("SEC submissions timed out"),
        [],
    ]
    fake_edgar = SimpleNamespace(
        Company=MagicMock(return_value=company),
        set_identity=MagicMock(),
    )
    monkeypatch.setenv("EDGAR_IDENTITY", "Test test@example.com")

    with (
        patch.dict("sys.modules", {"edgar": fake_edgar}),
        patch("fin_pipeline.sec_edgar.time.sleep") as sleep,
    ):
        results = list(stream_filings_from_csv(str(csv_path), forms=["20-F", "40-F"]))

    assert results == []
    assert company.get_filings.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


def test_stream_uses_40f_exhibits_and_includes_section_map(tmp_path, monkeypatch):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nTEST\n")
    filing = SimpleNamespace(
        accession_no="0001-24-000001",
        form="40-F",
        company="Test Company",
        report_date="2024-12-31",
        filing_date="2025-03-01",
        cik=1234,
        html=MagicMock(side_effect=AssertionError("cover page should not be used")),
        exhibits=[
            SimpleNamespace(
                document_type="EX-99.1",
                description="Annual Information Form",
                content=lambda: "<html><body>AIF " + "x" * 500 + "</body></html>",
            )
        ],
    )
    company = MagicMock()
    company.get_ticker.return_value = "TEST"
    company.get_filings.return_value = [filing]
    fake_edgar = SimpleNamespace(
        Company=MagicMock(return_value=company),
        set_identity=MagicMock(),
    )
    monkeypatch.setenv("EDGAR_IDENTITY", "Test test@example.com")

    with patch.dict("sys.modules", {"edgar": fake_edgar}):
        results = list(
            stream_filings_from_csv(str(csv_path), forms=["40-F"])
        )

    document, metadata = results[0]
    assert "<h1>Annual Information Form</h1>" in document
    assert metadata["exhibitSectionMap"] == '{"EX-99.1": "business"}'
    filing.html.assert_not_called()


def test_fetch_filing_html_retries_transient_timeout():
    filing = SimpleNamespace(
        accession_no="0000320193-25-000079",
        html=MagicMock(
            side_effect=[
                ReadTimeout("SEC read timed out"),
                ReadTimeout("SEC read timed out"),
                "<html>filing</html>",
            ]
        ),
    )

    with patch("fin_pipeline.sec_edgar.time.sleep") as sleep:
        html = _fetch_filing_html(filing)

    assert html == "<html>filing</html>"
    assert filing.html.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


def test_fetch_filing_html_reports_exhausted_retries():
    filing = SimpleNamespace(
        accession_no="0000320193-25-000079",
        html=MagicMock(side_effect=ReadTimeout("SEC read timed out")),
    )

    with (
        patch("fin_pipeline.sec_edgar.time.sleep"),
        pytest.raises(SecEdgarFetchError, match="after 3 attempts"),
    ):
        _fetch_filing_html(filing)

    assert filing.html.call_count == 3


def _raise_attribute_error():
    raise AttributeError("not available")


def test_40f_concatenates_exhibits_with_section_markers():
    exhibit_content = lambda text: (
        f"<html><body>{text}{'x' * 500}</body></html>"
    )
    exhibits = [
        SimpleNamespace(
            document_type="EX-99.1",
            description="EXHIBIT 99.1",
            html=_raise_attribute_error,
            content=lambda: exhibit_content("AIF content"),
            text=_raise_attribute_error,
        ),
        SimpleNamespace(
            document_type="EX-99.2",
            description="EXHIBIT 99.2",
            html=_raise_attribute_error,
            content=lambda: exhibit_content("MD&A content"),
            text=_raise_attribute_error,
        ),
        SimpleNamespace(
            document_type="EX-99.3",
            description="EXHIBIT 99.3",
            html=_raise_attribute_error,
            content=lambda: exhibit_content("Financials"),
            text=_raise_attribute_error,
        ),
    ]
    filing = SimpleNamespace(accession_no="0001-24-000001", exhibits=exhibits)

    document, section_map = _get_40f_exhibit_content(filing)

    assert document is not None
    assert "<h1>Annual Information Form</h1>" in document
    assert "<h1>Management's Discussion and Analysis</h1>" in document
    assert "<h1>Audited Financial Statements</h1>" in document
    assert section_map == {
        "EX-99.1": "business",
        "EX-99.2": "mdna",
        "EX-99.3": "financial_statements",
    }


def test_classify_exhibit_by_content_financial_statements():
    content = (
        "Manulife Financial Corporation\n"
        "Consolidated Financial Statements\n"
        "For the year ended December 31, 2023"
    )

    assert _classify_exhibit_by_content(content) == "financial_statements"


def test_classify_exhibit_by_content_aif():
    content = "ANNUAL INFORMATION FORM\nFOR THE YEAR ENDED December 31, 2023"

    assert _classify_exhibit_by_content(content) == "business"


def test_classify_exhibit_by_content_mdna():
    content = "MANAGEMENT'S DISCUSSION AND ANALYSIS\nFor the year ended 2023"

    assert _classify_exhibit_by_content(content) == "mdna"


def test_classify_exhibit_by_content_unknown():
    content = "Quarterly corporate update\n" + "No known section heading. " * 20

    assert _classify_exhibit_by_content(content) is None


def test_classify_exhibit_strips_html():
    content = (
        '<?xml version="1.0"?><html><body>'
        "<h1>Consolidated Financial Statements</h1><p>...</p>"
        "</body></html>"
    )

    assert _classify_exhibit_by_content(content) == "financial_statements"


def test_40f_uses_content_classification_before_sequence():
    exhibit = SimpleNamespace(
        document_type="EX-99.1",
        description="EXHIBIT 99.1",
        content=lambda: (
            "<html><body>Consolidated Financial Statements "
            + "x" * 500
            + "</body></html>"
        ),
    )
    filing = SimpleNamespace(accession_no="0001-24-000001", exhibits=[exhibit])

    document, section_map = _get_40f_exhibit_content(filing)

    assert document is not None
    assert section_map["EX-99.1"] == "financial_statements"


def test_40f_falls_back_to_text_when_content_missing():
    exhibit = SimpleNamespace(
        document_type="EX-99.1",
        description="Annual Information Form",
        text=lambda: "Plain text content " + "x" * 500,
    )
    filing = SimpleNamespace(accession_no="0001-24-000001", exhibits=[exhibit])

    document, section_map = _get_40f_exhibit_content(filing)

    assert document is not None
    assert "<pre>" in document
    assert "Plain text content" in document
    assert section_map == {"EX-99.1": "business"}


def test_40f_skips_non_ex99_exhibits():
    exhibits = [
        SimpleNamespace(
            document_type="40-F",
            description="Cover",
            content=lambda: "<html>" + "x" * 500,
        ),
        SimpleNamespace(
            document_type="EX-97",
            description="Exhibit 97",
            content=lambda: "<html>" + "x" * 500,
        ),
        SimpleNamespace(
            document_type="EX-99.1",
            description="AIF",
            content=lambda: "<html>" + "x" * 500,
        ),
    ]
    filing = SimpleNamespace(accession_no="0001-24-000001", exhibits=exhibits)

    document, section_map = _get_40f_exhibit_content(filing)

    assert document is not None
    assert section_map == {"EX-99.1": "business"}


def test_40f_returns_none_when_no_exhibits():
    filing = SimpleNamespace(accession_no="0001-24-000001", exhibits=[])

    assert _get_40f_exhibit_content(filing) == (None, {})


def test_40f_skips_pdf_exhibit():
    exhibit = SimpleNamespace(
        document_type="EX-99.1",
        description="Annual Information Form",
        content=lambda: "%PDF-1.7" + "x" * 500,
    )
    filing = SimpleNamespace(accession_no="0001-24-000001", exhibits=[exhibit])

    assert _get_40f_exhibit_content(filing) == (None, {})


def test_stream_cli_rejects_reversed_year_range(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nAAPL\n")

    result = CliRunner().invoke(
        main, ["sec-edgar-stream", str(csv_path), "--year-range", "2025-2018"]
    )

    assert result.exit_code != 0
    assert "START <= END" in result.output
