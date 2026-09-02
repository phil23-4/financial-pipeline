import pytest

from fin_pipeline.sec_edgar import read_company_csv


def test_read_company_csv_normalizes_rows(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,forms,year,max_filings\nAAPL,10-K,2024,2\n")

    assert list(read_company_csv(str(csv_path))) == [{
        "ticker": "AAPL",
        "forms": "10-K",
        "year": "2024",
        "max_filings": "2",
    }]


def test_read_company_csv_requires_ticker_or_cik(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,cik\n,\n")

    with pytest.raises(ValueError, match="requires ticker or cik"):
        list(read_company_csv(str(csv_path)))