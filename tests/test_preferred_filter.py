import csv
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from fin_pipeline.cli import main
from fin_pipeline.preferred_filter import (
    delete_ingested_preferred,
    filter_csv,
    find_preferred_rows,
    is_preferred_ticker,
)


@pytest.fixture
def companies_csv(tmp_path):
    path = tmp_path / "companies.csv"
    path.write_text(
        "ticker,cik,company\n"
        "GS,886982,Goldman Sachs Group\n"
        "GS-PD,886982,Goldman Sachs Group\n"
        "C,831001,Citigroup Inc.\n"
        "C-PR,831001,Citigroup Inc.\n"
        "BRK-A,1067983,Berkshire Hathaway\n"
        "BRK-B,1067983,Berkshire Hathaway\n"
        "AAPL,320193,Apple Inc.\n",
        encoding="utf-8",
    )
    return path


def test_is_preferred_ticker_standard_cases():
    for ticker in (
        "GS-PD",
        "C-PR",
        "MS-PA",
        "T-PC",
        "PSA-PS",
        "ALL-PB",
        "NEE-PR",
        "gs-pd",
    ):
        assert is_preferred_ticker(ticker) is True

    for ticker in (
        "AAPL",
        "GS",
        "BRK-A",
        "BRK-B",
        "BF-A",
        "BF-B",
        "META",
        "GOOGL",
        "AIZN",
        "",
        None,
    ):
        assert is_preferred_ticker(ticker) is False


def test_find_preferred_rows_drops_with_plain_sibling():
    rows = [
        {"ticker": "GS", "cik": "886982"},
        {"ticker": "GS-PD", "cik": "886982"},
        {"ticker": "AAPL", "cik": "320193"},
    ]

    result = find_preferred_rows(rows)

    assert result["drop"] == [rows[1]]
    assert result["keep"] == [rows[0], rows[2]]
    assert result["flagged"] == []


def test_find_preferred_rows_keeps_without_plain_sibling():
    rows = [
        {"ticker": "ALL-PB", "cik": "899051"},
        {"ticker": "GS", "cik": "886982"},
    ]

    result = find_preferred_rows(rows)

    assert result["drop"] == []
    assert result["keep"] == rows
    assert result["flagged"] == ["ALL-PB"]


def test_find_preferred_rows_preserves_dual_class_common():
    rows = [
        {"ticker": "BRK-A", "cik": "1067983"},
        {"ticker": "BRK-B", "cik": "1067983"},
        {"ticker": "BF-A", "cik": "14693"},
        {"ticker": "BF-B", "cik": "14693"},
    ]

    result = find_preferred_rows(rows)

    assert result["drop"] == []
    assert result["keep"] == rows
    assert result["flagged"] == []


def test_find_preferred_rows_handles_empty_cik():
    rows = [
        {"ticker": "GS-PD", "cik": ""},
        {"ticker": "GS", "cik": ""},
    ]

    result = find_preferred_rows(rows)

    assert result["drop"] == []
    assert result["keep"] == rows
    assert result["flagged"] == ["GS-PD"]


def test_filter_csv_writes_output(tmp_path):
    input_path = tmp_path / "companies.csv"
    input_path.write_text(
        "ticker,cik,company\n"
        "GS,886982,Goldman Sachs Group\n"
        "GS-PD,886982,Goldman Sachs Group\n"
        "AAPL,320193,Apple Inc.\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "filtered.csv"

    result = filter_csv(str(input_path), str(output_path))

    with output_path.open(newline="", encoding="utf-8") as source:
        output_rows = list(csv.DictReader(source))
    assert [row["ticker"] for row in output_rows] == ["GS", "AAPL"]
    assert result["output_rows"] == 2
    assert len(result["dropped"]) == 1
    assert result["dropped"][0]["ticker"] == "GS-PD"


def test_filter_csv_preserves_headers(tmp_path):
    input_path = tmp_path / "mixed_headers.csv"
    input_path.write_text(
        "Ticker,CIK,Company\nGS,886982,Goldman Sachs Group\n"
        "GS-PD,886982,Goldman Sachs Group\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.csv"

    filter_csv(str(input_path), str(output_path))

    with output_path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == ["Ticker", "CIK", "Company"]
        assert [row["Ticker"] for row in reader] == ["GS"]


def test_filter_csv_dry_run_no_write(tmp_path):
    input_path = tmp_path / "companies.csv"
    input_path.write_text(
        "ticker,cik\nGS,886982\nGS-PD,886982\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "not_written.csv"

    result = filter_csv(str(input_path), str(output_path), dry_run=True)

    assert not output_path.exists()
    assert result["dropped"][0]["ticker"] == "GS-PD"
    assert result["dry_run"] is True


def test_filter_csv_real_world_sample(companies_csv, tmp_path):
    output_path = tmp_path / "clean.csv"

    result = filter_csv(str(companies_csv), str(output_path))

    with output_path.open(newline="", encoding="utf-8") as source:
        output_tickers = [row["ticker"] for row in csv.DictReader(source)]
    assert output_tickers == ["GS", "C", "BRK-A", "BRK-B", "AAPL"]
    assert [row["ticker"] for row in result["dropped"]] == ["GS-PD", "C-PR"]


@pytest.mark.asyncio
async def test_delete_ingested_preferred_only_when_sibling_in_csv(
    tmp_path,
):
    filtered_csv = tmp_path / "filtered.csv"
    filtered_csv.write_text(
        "ticker,cik,company\nGS,886982,Goldman Sachs Group\n",
        encoding="utf-8",
    )
    db = AsyncMock()
    db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "sec_GS-PD_0000886982-24-000001",
                    "companyTicker": "GS-PD",
                    "cik": "0000886982",
                },
                {
                    "filingId": "sec_ALL-PB_0000899051-24-000001",
                    "companyTicker": "ALL-PB",
                    "cik": "0000899051",
                },
            ]
        }
    ]

    result = await delete_ingested_preferred(db, str(filtered_csv))

    assert result["scanned"] == 2
    assert result["deleted"] == 1
    assert result["kept"] == 1
    assert result["deleted_filing_ids"] == ["sec_GS-PD_0000886982-24-000001"]
    db.query_raw.assert_awaited_once_with(
        "SELECT filingId, companyTicker, cik FROM exchange_filing "
        "WHERE string::contains(companyTicker, '-')"
    )
    db.delete_record.assert_awaited_once_with(
        "exchange_filing:sec_GS-PD_0000886982-24-000001"
    )


@pytest.mark.asyncio
async def test_delete_ingested_preferred_dry_run(tmp_path):
    filtered_csv = tmp_path / "filtered.csv"
    filtered_csv.write_text(
        "ticker,cik\nGS,886982\n",
        encoding="utf-8",
    )
    db = AsyncMock()
    db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "sec_GS-PD_0000886982-24-000001",
                    "companyTicker": "GS-PD",
                    "cik": "0000886982",
                }
            ]
        }
    ]

    result = await delete_ingested_preferred(
        db, str(filtered_csv), dry_run=True
    )

    assert result["deleted"] == 1
    assert result["deleted_filing_ids"] == ["sec_GS-PD_0000886982-24-000001"]
    db.delete_record.assert_not_awaited()


def test_filter_preferred_cli_dry_run(companies_csv):
    output_path = companies_csv.with_name("clean.csv")

    result = CliRunner().invoke(
        main,
        [
            "filter-preferred",
            "--input",
            str(companies_csv),
            "--output",
            str(output_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dropped:       2" in result.output
    assert not output_path.exists()
