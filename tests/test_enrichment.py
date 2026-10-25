import csv
from unittest.mock import AsyncMock, patch

import pytest

from fin_pipeline.db import connection as connection_module
from fin_pipeline.db.connection import _HttpSurrealConnection
from fin_pipeline.enrichment import (
    backfill_cik_from_csv,
    backfill_reporting_year,
    enrich_industry_codes,
    load_industry_mapping,
)


@pytest.fixture
def sample_mapping_csv(tmp_path):
    csv_path = tmp_path / "industry_mapping.csv"
    csv_path.write_text(
        "companyKey,cik,ticker,company_name,country,"
        "gics_code,gics_name,gics_sector,gics_sector_name,"
        "confidence,source\n"
        ",0000320193,AAPL,Apple Inc.,United States,"
        "45202030,Technology Hardware,45,Information Technology,"
        "0.98,gics_official\n"
        ",0001652044,GOOG,Alphabet Inc.,United States,"
        "45102010,Interactive Media,45,Information Technology,"
        "0.98,gics_official\n"
        ",0000789019,MSFT,Microsoft Corp.,United States,"
        "45103010,Application Software,45,Information Technology,"
        "0.95,gics_official\n",
        encoding="utf-8",
    )
    return str(csv_path)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.merge = AsyncMock(return_value=None)
    db.query = AsyncMock(return_value=[{"result": []}])
    db.query_raw = AsyncMock(return_value=[{"result": []}])
    return db


def _write_mapping(path, rows):
    fields = [
        "companyKey",
        "cik",
        "ticker",
        "company_name",
        "country",
        "gics_code",
        "gics_name",
        "gics_sector",
        "gics_sector_name",
        "confidence",
        "source",
    ]
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def _mapping_row(
    *,
    company_key="",
    cik="",
    ticker="",
    gics_code="45202030",
    gics_sector="45",
    company_name="Example Corp",
):
    return {
        "companyKey": company_key,
        "cik": cik,
        "ticker": ticker,
        "company_name": company_name,
        "country": "United States",
        "gics_code": gics_code,
        "gics_name": "Example industry",
        "gics_sector": gics_sector,
        "gics_sector_name": "Information Technology",
        "confidence": "0.98",
        "source": "gics_official",
    }


def test_load_industry_mapping_valid(sample_mapping_csv):
    mapping = load_industry_mapping(sample_mapping_csv)

    assert mapping["row_count"] == 3
    assert mapping["by_cik"]["0000320193"]["gics_code"] == "45202030"
    assert mapping["by_ticker"]["AAPL"]["gics_sector"] == "45"
    assert mapping["by_companyKey"] == {}
    assert mapping["warnings"] == []


def test_load_industry_mapping_skips_invalid_rows(tmp_path):
    valid_rows = [
        _mapping_row(cik="0000320193", ticker="AAPL"),
        _mapping_row(cik="0001652044", ticker="GOOG"),
        _mapping_row(cik="0000789019", ticker="MSFT"),
    ]
    invalid_rows = [
        _mapping_row(cik="0000999991", ticker="BAD", gics_code="ABC"),
        _mapping_row(),
    ]
    invalid_rows[-1].update({"companyKey": "", "cik": "", "ticker": ""})
    mapping_path = _write_mapping(
        tmp_path / "invalid_rows.csv", valid_rows + invalid_rows
    )

    mapping = load_industry_mapping(mapping_path)

    assert mapping["row_count"] == 3
    assert len(mapping["warnings"]) == 2
    assert "line 5" in mapping["warnings"][0]
    assert "line 6" in mapping["warnings"][1]


def test_load_industry_mapping_missing_file():
    with pytest.raises(FileNotFoundError, match="mapping|not found"):
        load_industry_mapping("/nonexistent/path.csv")


def test_load_industry_mapping_derives_sector_from_code(tmp_path):
    mapping_path = _write_mapping(
        tmp_path / "missing_sector.csv",
        [_mapping_row(cik="0000320193", ticker="AAPL", gics_sector="")],
    )

    mapping = load_industry_mapping(mapping_path)

    assert mapping["by_cik"]["0000320193"]["gics_sector"] == "45202030"[:2]


@pytest.mark.asyncio
async def test_enrich_lookup_priority_cik_first(
    mock_db, tmp_path
):
    mapping_path = _write_mapping(
        tmp_path / "priority_cik.csv",
        [
            _mapping_row(cik="0000320193", ticker="", gics_code="45202030"),
            _mapping_row(company_key="co_abc", gics_code="88888888"),
            _mapping_row(ticker="AAPL", gics_code="99999999"),
        ],
    )
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "filing-cik",
                    "cik": "0000320193",
                    "companyKey": "co_abc",
                    "companyTicker": "AAPL",
                    "gicsCode": None,
                    "industryCodeRaw": None,
                }
            ]
        }
    ]

    result = await enrich_industry_codes(mock_db, mapping_path=mapping_path)

    payload = mock_db.merge.call_args.args[1]
    assert payload["gicsCode"] == "45202030"
    assert result["by_source"]["by_cik"] == 1


@pytest.mark.asyncio
async def test_enrich_lookup_priority_companykey_fallback(mock_db, tmp_path):
    mapping_path = _write_mapping(
        tmp_path / "priority_company_key.csv",
        [
            _mapping_row(company_key="co_abc", gics_code="88888888"),
            _mapping_row(ticker="AAPL", gics_code="99999999"),
        ],
    )
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "filing-company-key",
                    "cik": "0000999999",
                    "companyKey": "co_abc",
                    "companyTicker": "AAPL",
                    "gicsCode": None,
                    "industryCodeRaw": None,
                }
            ]
        }
    ]

    result = await enrich_industry_codes(mock_db, mapping_path=mapping_path)

    payload = mock_db.merge.call_args.args[1]
    assert payload["gicsCode"] == "88888888"
    assert result["by_source"]["by_companyKey"] == 1


@pytest.mark.asyncio
async def test_enrich_lookup_priority_ticker_last(mock_db, tmp_path):
    mapping_path = _write_mapping(
        tmp_path / "priority_ticker.csv",
        [_mapping_row(ticker="BABA", gics_code="45102010")],
    )
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "filing-ticker",
                    "cik": None,
                    "companyKey": "co_unknown",
                    "companyTicker": "BABA",
                    "gicsCode": None,
                    "industryCodeRaw": None,
                }
            ]
        }
    ]

    result = await enrich_industry_codes(mock_db, mapping_path=mapping_path)

    payload = mock_db.merge.call_args.args[1]
    assert payload["gicsCode"] == "45102010"
    assert result["by_source"]["by_ticker"] == 1


@pytest.mark.asyncio
async def test_enrich_unmatched_record_untouched(
    mock_db, sample_mapping_csv
):
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "unmatched",
                    "cik": "0000999999",
                    "companyKey": "co_unknown",
                    "companyTicker": "UNKNOWN",
                    "gicsCode": None,
                    "industryCodeRaw": None,
                }
            ]
        }
    ]

    result = await enrich_industry_codes(mock_db, mapping_path=sample_mapping_csv)

    mock_db.merge.assert_not_awaited()
    assert result["unmatched"] == 1


@pytest.mark.asyncio
async def test_enrich_dry_run_no_writes(mock_db, sample_mapping_csv):
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "dry-run",
                    "cik": "0000320193",
                    "companyKey": None,
                    "companyTicker": "AAPL",
                    "gicsCode": None,
                    "industryCodeRaw": None,
                }
            ]
        }
    ]

    result = await enrich_industry_codes(
        mock_db, mapping_path=sample_mapping_csv, dry_run=True
    )

    assert result["matched"] > 0
    assert result["updated"] == 0
    mock_db.merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_preserves_existing_industryCodeRaw(
    mock_db, sample_mapping_csv
):
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "preserve-industry-raw",
                    "cik": "0000320193",
                    "companyKey": None,
                    "companyTicker": "AAPL",
                    "gicsCode": None,
                    "industryCodeRaw": "IT Software & Services",
                }
            ]
        }
    ]

    await enrich_industry_codes(mock_db, mapping_path=sample_mapping_csv)

    payload = mock_db.merge.call_args.args[1]
    assert "industryCodeRaw" not in payload


@pytest.mark.asyncio
async def test_backfill_reporting_year(mock_db):
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "dated",
                    "periodEndDate": "2023-12-31",
                    "reportingYear": None,
                },
                {
                    "filingId": "missing-date",
                    "periodEndDate": None,
                    "reportingYear": None,
                },
            ]
        }
    ]

    result = await backfill_reporting_year(mock_db)

    assert result["derivable"] == 1
    assert result["updated"] == 1
    assert result["skipped"] == 1
    mock_db.merge.assert_awaited_once_with(
        "exchange_filing:dated", {"reportingYear": 2023}
    )


@pytest.mark.asyncio
async def test_backfill_dry_run_no_writes(mock_db):
    mock_db.query_raw.return_value = [
        {
            "result": [
                {
                    "filingId": "dry-run-year",
                    "periodEndDate": "2023-12-31",
                    "reportingYear": None,
                }
            ]
        }
    ]

    result = await backfill_reporting_year(mock_db, dry_run=True)

    assert result["derivable"] > 0
    assert result["updated"] == 0
    mock_db.merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_partial_update():
    with patch.object(
        connection_module, "surreal_query", return_value=[{"status": "OK"}]
    ) as query:
        db = _HttpSurrealConnection()
        await db.merge("exchange_filing:test", {"gicsCode": "45102010"})

    sql = query.call_args.args[0]
    assert 'UPDATE exchange_filing:⟨test⟩ MERGE {gicsCode: "45102010"' in sql
    assert "CONTENT" not in sql


@pytest.mark.asyncio
async def test_backfill_cik_from_csv(tmp_path, mock_db):
    csv_path = tmp_path / "sec_companies.csv"
    csv_path.write_text(
        "Ticker,CIK\nAAPL,320193\nGOOG,1652044\nMSFT,789019\n",
        encoding="utf-8",
    )
    mock_db.query_raw.return_value = [
        {
            "result": [
                {"filingId": "aapl", "companyTicker": "AAPL", "cik": None},
                {"filingId": "goog", "companyTicker": "GOOG", "cik": None},
                {"filingId": "msft", "companyTicker": "MSFT", "cik": None},
                {
                    "filingId": "already-filled",
                    "companyTicker": "AAPL",
                    "cik": "0000320193",
                },
            ]
        }
    ]

    result = await backfill_cik_from_csv(mock_db, str(csv_path))

    assert result == {"scanned": 4, "matched": 3, "updated": 3}
    assert mock_db.merge.await_args_list[0].args == (
        "exchange_filing:aapl",
        {"cik": "0000320193"},
    )
    assert mock_db.merge.await_args_list[1].args == (
        "exchange_filing:goog",
        {"cik": "0001652044"},
    )
    assert mock_db.merge.await_args_list[2].args == (
        "exchange_filing:msft",
        {"cik": "0000789019"},
    )
