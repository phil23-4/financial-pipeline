import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fin_pipeline.sec_edgar import read_company_csv, stream_filings_from_csv
from click.testing import CliRunner
from fin_pipeline.cli import main


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


def test_stream_cli_rejects_reversed_year_range(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nAAPL\n")

    result = CliRunner().invoke(
        main, ["sec-edgar-stream", str(csv_path), "--year-range", "2025-2018"]
    )

    assert result.exit_code != 0
    assert "START <= END" in result.output
