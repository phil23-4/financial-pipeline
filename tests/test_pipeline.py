import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from fin_pipeline.config.logger import serialize_json_log
from fin_pipeline.db import relations
from fin_pipeline.db.connection import SurrealConnection
from fin_pipeline.pipeline import (
    process_mixed_directory,
    run_html_content_pipeline,
    run_ingestion_pipeline,
)
from fin_pipeline.utils.html_parser import extract_filing_metadata, parse_html_content


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.parse_pdf_layout")
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_successful_pipeline_ingestion(
    mock_graph_rel, mock_surreal_conn, mock_pdf_parser, sample_pdf_path
):
    """Tests an end-to-end ingestion pass with fully mocked data layers."""

    # 1. Setup mock returns for the PDF parser engine
    mock_pdf_parser.return_value = {
        "text": "Sample financial document text layer",
        "chunks": [
            {
                "chunkIndex": 0,
                "pageNumber": 1,
                "sectionLabel": None,
                "text": "ITEM 1. Business\nSample financial document text layer",
                "charStart": None,
                "charEnd": None,
            }
        ],
        "tables": [
            {
                "tableIndex": 0,
                "pageNumber": 1,
                "headers": ["Asset", "Value"],
                "rowCount": 1,
                "accuracy": 96.5,
                "markdown": "| A | B |",
            }
        ],
        "table_cnt": 1,
        "reason": "Digital Native",
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
        "sourceType": "structured_filing",
        "referencedTickers": ["AAPL"],
    }

    # 4. Trigger structural function engine node logic
    await run_ingestion_pipeline(
        metadata, sample_pdf_path, source="SEC", ocr_enabled=False
    )

    # 5. Assertions: Verify database update statement was made correctly
    mock_pdf_parser.assert_called_once_with(
        sample_pdf_path, force_ocr=False, use_ocr=False
    )
    mock_db_instance.upsert.assert_called_once()
    called_id, called_payload = mock_db_instance.upsert.call_args[0]

    assert called_id == "exchange_filing:test_filing_123"
    assert called_payload["companyTicker"] == "XYZ"
    assert called_payload["documentStatus"] == "PROCESSED"
    document_chunks = json.loads(called_payload["documentChunks"])
    assert document_chunks[0]["chunkIndex"] == 0
    assert document_chunks[0]["sectionLabel"] == "business"
    assert called_payload["documentTableCnt"] == 1
    assert json.loads(called_payload["documentTables"])[0]["accuracy"] == 96.5

    # Verify graph edge constructor call was dispatched safely
    mock_graph_rel.assert_called_once()


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.parse_document")
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_ingestion_pipeline_labels_glossy_chunks_with_optional_llm(
    mock_graph_rel, mock_surreal_conn, mock_parse_document, sample_pdf_path
):
    mock_parse_document.return_value = {
        "text": "Governance report",
        "chunks": [{"chunkIndex": 0, "pageNumber": 1, "text": "Governance report"}],
        "tables": [],
        "table_cnt": 0,
        "reason": "PyMuPDF4LLM",
    }
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db
    llm_callable = lambda _prompt: (
        '{"sections":[{"startChunk":0,"endChunk":0,"label":"governance"}]}'
    )

    completed = await run_ingestion_pipeline(
        {
            "filingId": "glossy_report",
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "ANNUAL_REPORT",
            "sourceType": "glossy_report",
        },
        sample_pdf_path,
        source="LOCAL",
        llm_callable=llm_callable,
    )

    assert completed is True
    document_chunks = json.loads(mock_db.upsert.call_args.args[1]["documentChunks"])
    assert document_chunks[0]["sectionLabel"] == "governance"
    mock_graph_rel.assert_awaited_once()


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.scan_documents", return_value=[("report.pdf", {})])
@patch("fin_pipeline.pipeline.run_ingestion_pipeline", new_callable=AsyncMock)
async def test_mixed_directory_passes_optional_llm_callable(
    mock_run_ingestion, _mock_scan_documents, tmp_path
):
    llm_callable = lambda _prompt: '{"sections":[]}'

    await process_mixed_directory(str(tmp_path), llm_callable=llm_callable)

    mock_run_ingestion.assert_awaited_once()
    assert mock_run_ingestion.call_args.kwargs["llm_callable"] is llm_callable


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.parse_pdf_layout")
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_dry_run_validates_without_database_writes(
    mock_graph_rel, mock_surreal_conn, mock_pdf_parser, sample_pdf_path
):
    mock_pdf_parser.return_value = {
        "text": "Sample financial document text layer",
        "tables": [],
        "table_cnt": 0,
        "reason": "Digital Native",
    }

    completed = await run_ingestion_pipeline(
        {
            "filingId": "dry_run_filing",
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "10-K",
        },
        sample_pdf_path,
        source="SEC",
        dry_run=True,
    )

    assert completed is True
    mock_surreal_conn.assert_not_called()
    mock_graph_rel.assert_not_awaited()


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.parse_pdf_layout")
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_file_pipeline_generates_filing_id_when_missing(
    mock_graph_rel, mock_surreal_conn, mock_pdf_parser, sample_pdf_path
):
    mock_pdf_parser.return_value = {
        "text": "Document",
        "tables": [],
        "table_cnt": 0,
        "reason": "Digital Native",
    }
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db

    completed = await run_ingestion_pipeline(
        {
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "10-K",
        },
        sample_pdf_path,
        source="SEC",
    )

    assert completed is True
    filing_id = mock_db.upsert.call_args.args[1]["filingId"]
    assert filing_id.startswith("local_")
    assert filing_id != "UNKNOWN_ID"
    mock_graph_rel.assert_awaited_once()


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_html_pipeline_generates_filing_id_when_missing(
    mock_graph_rel, mock_surreal_conn
):
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db

    completed = await run_html_content_pipeline(
        {
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "10-K",
        },
        "<html><body>Annual Report</body></html>",
    )

    assert completed is True
    filing_id = mock_db.upsert.call_args.args[1]["filingId"]
    assert filing_id.startswith("html_")
    assert filing_id != "UNKNOWN_ID"
    mock_graph_rel.assert_awaited_once()


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.SurrealConnection")
async def test_html_pipeline_dry_run_does_not_open_database(mock_surreal_conn):
    completed = await run_html_content_pipeline(
        {
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "10-K",
        },
        "<html><body>Annual Report</body></html>",
        dry_run=True,
    )

    assert completed is True
    mock_surreal_conn.assert_not_called()


def test_serialize_json_log_uses_loguru_time_field():
    """Ensure JSON log serialization is compatible with Loguru's current record layout."""
    record = {
        "time": datetime(2026, 9, 1, 15, 16, 50, tzinfo=UTC),
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


@pytest.mark.asyncio
async def test_company_graph_relations_skip_when_company_table_is_unset(monkeypatch):
    monkeypatch.setattr(relations, "COMPANY_TABLE", "")

    assert await relations.ensure_company_exists(
        AsyncMock(), "TLV", "Banca Transilvania", "LOCAL_FS"
    ) is None


def test_extract_filing_metadata_from_inline_xbrl():
    html = """
    <ix:nonNumeric name="dei:EntityRegistrantName">Bank of America Corporation</ix:nonNumeric>
    <ix:nonNumeric name="dei:DocumentPeriodEndDate">December 31 , 2021</ix:nonNumeric>
    <ix:nonNumeric name="dei:DocumentType">10-K</ix:nonNumeric>
    """

    result = extract_filing_metadata(html)
    assert result["stockName"] == "Bank of America Corporation"
    assert result["filingDate"] == "2021-12-31"
    assert result["filingType"] == "10-K"
    assert result["exchange"] is None
    assert result["cik"] is None
    assert result["metadataSources"]["stockName"] == "html_ixbrl"
    assert result["metadataConfidence"]["filingDate"] >= 0.9


def test_extract_filing_metadata_finds_exchange_in_document_text():
    html = "<p>Name of each exchange on which registered: New York Stock Exchange</p>"

    assert extract_filing_metadata(html)["exchange"] == "NYSE"


def test_html_chunks_are_sequential_nonempty_pseudo_pages():
    text = "A" * 1800 + "\n\n" + "B" * 1800 + "\n\n" + "C"
    html = f"<html><body><p>{text}</p></body></html>"

    chunks = parse_html_content(html)["chunks"]

    assert [chunk["chunkIndex"] for chunk in chunks] == list(range(len(chunks)))
    assert [chunk["pageNumber"] for chunk in chunks] == list(
        range(1, len(chunks) + 1)
    )
    assert all(chunk["text"].strip() for chunk in chunks)
    assert len(chunks) == 2


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_html_content_pipeline_ingests_without_file(
    mock_graph_rel, mock_surreal_conn
):
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db

    completed = await run_html_content_pipeline(
        {
            "filingId": "sec_AAPL_0000320193-25-000079",
            "companyTicker": "AAPL",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
            "sourceType": "structured_filing",
        },
        '<p>ITEM 1. Business</p>'
        '<ix:nonNumeric name="dei:EntityRegistrantName">Apple Inc.</ix:nonNumeric>'
        '<ix:nonFraction name="us-gaap:Assets" contextRef="current">42</ix:nonFraction>',
    )

    assert completed is True
    mock_db.upsert.assert_called_once()
    payload = mock_db.upsert.call_args.args[1]
    assert payload["documentType"] == "HTML"
    assert payload["stockName"] == "Apple Inc."
    document_chunks = json.loads(payload["documentChunks"])
    assert document_chunks[0]["pageNumber"] == 1
    assert document_chunks[0]["sectionLabel"] == "business"
    assert json.loads(payload["xbrlFacts"])["assets"] == 42
    mock_graph_rel.assert_awaited_once()


@pytest.mark.parametrize(
    "date_text",
    ["December 31, 2019", "December 31 , 2019", "December\u00a031,\u00a02019"],
)
def test_extract_filing_metadata_accepts_sec_date_spacing(date_text):
    html = (
        f'<ix:nonNumeric name="dei:DocumentPeriodEndDate">{date_text}</ix:nonNumeric>'
    )

    assert extract_filing_metadata(html)["filingDate"] == "2019-12-31"


@pytest.mark.asyncio
@patch("fin_pipeline.db.connection._HttpSurrealConnection")
async def test_surreal_connection_uses_helper_adapter(mock_http_conn):
    """The app should use the repository's helper-backed connection adapter."""
    mock_db = AsyncMock()
    mock_http_conn.return_value = mock_db

    with patch("fin_pipeline.db.connection.initialize_schema") as mock_schema:
        async with SurrealConnection() as db:
            assert db is mock_db

    mock_http_conn.assert_called_once()
    mock_schema.assert_called_once()
    mock_db.connect.assert_awaited_once()
    mock_db.signin.assert_awaited_once_with(
        {
            "user": "root",
            "pass": "secret",
            "namespace": "finance",
            "database": "analytics",
        }
    )
    mock_db.use.assert_awaited_once_with(namespace="finance", database="analytics")
    mock_db.close.assert_awaited_once()


def test_http_upsert_uses_upsert_statement():
    """Ensure the adapter uses raw SurrealQL UPSERT writes instead of a no-op UPDATE."""
    with patch("fin_pipeline.db.connection.surreal_query") as mock_surreal_query:
        mock_surreal_query.return_value = {"result": [{"status": "OK"}]}

        async def run():
            adapter = __import__(
                "fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"]
            )._HttpSurrealConnection()
            await adapter.upsert(
                "exchange_filing:test",
                {
                    "filingId": "test",
                    "updatedAt": "2026-09-01T00:00:00Z",
                    "sheetName": None,
                    "documentTables": [{"sheetName": None, "headers": ["A"]}],
                },
            )

        import asyncio

        asyncio.run(run())

    mock_surreal_query.assert_called_once()
    sql = mock_surreal_query.call_args[0][0]
    assert sql.startswith("UPSERT exchange_filing:⟨test⟩ CONTENT")
    assert "updatedAt: d'2026-09-01T00:00:00Z'" in sql
    assert "sheetName" not in sql


def test_http_upsert_serializes_nested_fields():
    chunks = [{"chunkIndex": 0, "text": "Note:Unless arbitrary content"}]
    serialized_chunks = json.dumps(chunks, ensure_ascii=False)
    with (
        patch("fin_pipeline.db.connection.surreal_query") as mock_surreal_query,
        patch("fin_pipeline.db.connection.surreal_rpc") as mock_surreal_rpc,
    ):
        mock_surreal_query.return_value = {"result": [{"status": "OK"}]}

        async def run():
            adapter = __import__(
                "fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"]
            )._HttpSurrealConnection()
            await adapter.upsert(
                "exchange_filing:chunks",
                {
                    "filingId": "chunks",
                    "updatedAt": "2026-09-01T00:00:00Z",
                    "documentChunks": chunks,
                },
            )

        import asyncio

        asyncio.run(run())

    mock_surreal_rpc.assert_not_called()
    mock_surreal_query.assert_called_once()
    sql = mock_surreal_query.call_args.args[0]
    assert json.dumps(serialized_chunks, ensure_ascii=False) in sql


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.parse_document")
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_pipeline_serializes_chunks_as_json_string(
    mock_graph_rel, mock_surreal_conn, mock_parse_document, sample_pdf_path
):
    chunks = [
        {
            "chunkIndex": 0,
            "pageNumber": 1,
            "sectionLabel": "business",
            "text": "Revenue:Growth",
        }
    ]
    mock_parse_document.return_value = {
        "text": "Revenue:Growth",
        "chunks": chunks,
        "tables": [],
        "table_cnt": 0,
        "reason": "Digital Native",
    }
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db

    completed = await run_ingestion_pipeline(
        {
            "filingId": "serialized_chunks",
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "10-K",
        },
        sample_pdf_path,
        source="SEC",
    )

    payload = mock_db.upsert.call_args.args[1]
    assert completed is True
    assert isinstance(payload["documentChunks"], str)
    assert json.loads(payload["documentChunks"]) == chunks
    mock_graph_rel.assert_awaited_once()


def test_http_upsert_uses_rpc_for_large_documents():
    """Large HTML filings should bypass the smaller /sql request limit."""
    with (
        patch("fin_pipeline.db.connection.surreal_query") as mock_surreal_query,
        patch("fin_pipeline.db.connection.surreal_rpc") as mock_surreal_rpc,
    ):
        mock_surreal_rpc.return_value = {"result": [{"status": "OK"}]}

        async def run():
            adapter = __import__(
                "fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"]
            )._HttpSurrealConnection()
            await adapter.upsert(
                "exchange_filing:large",
                {
                    "filingId": "large",
                    "updatedAt": "2026-09-01T00:00:00Z",
                    "documentText": "x" * 900_000,
                },
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
            adapter = __import__(
                "fin_pipeline.db.connection", fromlist=["_HttpSurrealConnection"]
            )._HttpSurrealConnection()
            await adapter.delete_record("exchange_filing:test")

        import asyncio

        asyncio.run(run())

    mock_surreal_query.assert_called_once_with(
        "DELETE exchange_filing:⟨test⟩;", timeout=60
    )
