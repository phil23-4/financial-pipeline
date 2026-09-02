from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, patch
from fin_pipeline.config.logger import serialize_json_log
from fin_pipeline.db.connection import SurrealConnection
from fin_pipeline.pipeline import run_ingestion_pipeline
from fin_pipeline.utils.html_parser import extract_filing_metadata

@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.parse_pdf_layout")
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_successful_pipeline_ingestion(
    mock_graph_rel,
    mock_surreal_conn,
    mock_pdf_parser,
    sample_pdf_path
):
    """Tests an end-to-end ingestion pass with fully mocked data layers."""
    
    # 1. Setup mock returns for the PDF parser engine
    mock_pdf_parser.return_value = {
        "text": "Sample financial document text layer",
        "tables": [{"tableIndex": 0, "pageNumber": 1, "headers": ["Asset", "Value"], "rowCount": 1, "markdown": "| A | B |"}],
        "table_cnt": 1,
        "reason": "Digital Native"
    }
    
    # 2. Mock the async database context manager behavior
    mock_db_instance = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db_instance

    # 3. Define raw pipeline arguments
    metadata = {
        "filingId": "test_filing_123",
        "companyTicker": "XYZ",
        "stockCode": "9999",
        "exchange": "LSE",
        "filingType": "10-K",
        "referencedTickers": ["AAPL"]
    }

    # 4. Trigger structural function engine node logic
    await run_ingestion_pipeline(metadata, sample_pdf_path, source="SEC")

    # 5. Assertions: Verify database update statement was made correctly
    mock_db_instance.upsert.assert_called_once()
    called_id, called_payload = mock_db_instance.upsert.call_args[0]
    
    assert called_id == "exchange_filing:test_filing_123"
    assert called_payload["companyTicker"] == "XYZ"
    assert called_payload["documentStatus"] == "PROCESSED"
    assert called_payload["documentTableCnt"] == 1
    
    # Verify graph edge constructor call was dispatched safely
    mock_graph_rel.assert_called_once()


def test_serialize_json_log_uses_loguru_time_field():
    """Ensure JSON log serialization is compatible with Loguru's current record layout."""
    record = {
        "time": datetime(2026, 9, 1, 15, 16, 50, tzinfo=timezone.utc),
        "level": SimpleNamespace(name="INFO"),
        "message": "hello world",
        "module": "test_module",
        "function": "demo_function",
        "line": 42,
        "exception": None,
    }

    payload = serialize_json_log(record)

    assert payload["timestamp"] == "2026-09-01T15:16:50Z"
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello world"


def test_extract_filing_metadata_from_inline_xbrl():
    html = """
    <ix:nonNumeric name="dei:EntityRegistrantName">Bank of America Corporation</ix:nonNumeric>
    <ix:nonNumeric name="dei:DocumentPeriodEndDate">December 31 , 2021</ix:nonNumeric>
    <ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>
    """

    assert extract_filing_metadata(html) == {
        "stockName": "Bank of America Corporation",
        "filingDate": "2021-12-31",
        "filingType": "10-K",
        "exchange": None,
        "cik": None,
    }


def test_extract_filing_metadata_finds_exchange_in_document_text():
    html = "<p>Name of each exchange on which registered: New York Stock Exchange</p>"

    assert extract_filing_metadata(html)["exchange"] == "NYSE"


@pytest.mark.parametrize("date_text", ["December 31, 2019", "December 31 , 2019", "December\u00a031,\u00a02019"])
def test_extract_filing_metadata_accepts_sec_date_spacing(date_text):
    html = f'<ix:nonNumeric name="dei:DocumentPeriodEndDate">{date_text}</ix:nonNumeric>'

    assert extract_filing_metadata(html)["filingDate"] == "2019-12-31"


@pytest.mark.asyncio
@patch("fin_pipeline.db.connection._HttpSurrealConnection")
async def test_surreal_connection_uses_helper_adapter(mock_http_conn):
    """The app should use the repository's helper-backed connection adapter."""
    mock_db = AsyncMock()
    mock_http_conn.return_value = mock_db

    async with SurrealConnection() as db:
        assert db is mock_db

    mock_http_conn.assert_called_once()
    mock_db.connect.assert_awaited_once()
    mock_db.signin.assert_awaited_once_with({
        "user": "root",
        "pass": "secret",
        "namespace": "finance",
        "database": "analytics",
    })
    mock_db.use.assert_awaited_once_with(namespace="finance", database="analytics")
    mock_db.close.assert_awaited_once()


def test_http_upsert_uses_upsert_statement():
    """Ensure the adapter uses raw SurrealQL UPSERT writes instead of a no-op UPDATE."""
    with patch("fin_pipeline.db.connection.surreal_query") as mock_surreal_query:
        mock_surreal_query.return_value = {"result": [{"status": "OK"}]}

        async def run():
            adapter = __import__("fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"])._HttpSurrealConnection()
            await adapter.upsert(
                "exchange_filing:test",
                {"filingId": "test", "updatedAt": "2026-09-01T00:00:00Z", "sheetName": None, "documentTables": [{"sheetName": None, "headers": ["A"]}]}
            )

        import asyncio
        asyncio.run(run())

    mock_surreal_query.assert_called_once()
    sql = mock_surreal_query.call_args[0][0]
    assert sql.startswith("UPSERT exchange_filing:⟨test⟩ CONTENT")
    assert "updatedAt: d'2026-09-01T00:00:00Z'" in sql
    assert "sheetName" not in sql


def test_http_upsert_uses_rpc_for_large_documents():
    """Large HTML filings should bypass the smaller /sql request limit."""
    with patch("fin_pipeline.db.connection.surreal_query") as mock_surreal_query, \
         patch("fin_pipeline.db.connection.surreal_rpc") as mock_surreal_rpc:
        mock_surreal_rpc.return_value = {"result": [{"status": "OK"}]}

        async def run():
            adapter = __import__("fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"])._HttpSurrealConnection()
            await adapter.upsert(
                "exchange_filing:large",
                {"filingId": "large", "updatedAt": "2026-09-01T00:00:00Z", "documentText": "x" * 900_000},
            )

        import asyncio
        asyncio.run(run())

    mock_surreal_query.assert_not_called()
    mock_surreal_rpc.assert_called_once()
    method, params = mock_surreal_rpc.call_args.args[:2]
    assert method == "query"
    assert params[0] == "UPSERT type::record($table, $key) CONTENT $payload;"
    assert params[1]["table"] == "exchange_filing"
    assert params[1]["key"] == "large"
    assert params[1]["payload"]["documentText"] == "x" * 900_000


def test_http_delete_record_removes_stale_partial_data():
    """Ensure stale partial records are cleaned up before a fresh ingest."""
    with patch("fin_pipeline.db.connection.surreal_query") as mock_surreal_query:
        mock_surreal_query.return_value = {"result": [{"status": "OK"}]}

        async def run():
            adapter = __import__("fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"])._HttpSurrealConnection()
            await adapter.delete_record("exchange_filing:test")

        import asyncio
        asyncio.run(run())

    mock_surreal_query.assert_called_once_with("DELETE exchange_filing:⟨test⟩;", timeout=60)
