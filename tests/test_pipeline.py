import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ConnectTimeout

from fin_pipeline.config.logger import serialize_json_log
from fin_pipeline.db import connection as connection_module
from fin_pipeline.db import relations
from fin_pipeline.db.connection import SurrealConnection
from fin_pipeline.models.schemas import ExchangeFilingModel
from fin_pipeline.pipeline import (
    process_mixed_directory,
    process_sec_edgar_csv_stream,
    run_html_content_pipeline,
    run_ingestion_pipeline,
)
from fin_pipeline.sec_edgar import SecEdgarFetchError
from fin_pipeline.utils.classify import classify_source_type
from fin_pipeline.utils.html_parser import (
    enrich_filing_metadata_with_edgartools,
    extract_filing_metadata,
    extract_text_from_html,
    parse_html_content,
)
from fin_pipeline.utils.xbrl_facts import extract_xbrl_facts


def test_classify_source_type():
    assert classify_source_type("Annual") == "glossy_report"
    assert classify_source_type("ANNUAL_REPORT") == "glossy_report"
    assert classify_source_type("10-K") == "structured_filing"
    assert classify_source_type("20-F") == "structured_filing"
    assert classify_source_type("40-F") == "structured_filing"
    assert classify_source_type("10-K/A") == "structured_filing"
    assert classify_source_type("weird") == "unknown"
    assert classify_source_type(None) == "unknown"
    assert classify_source_type("") == "unknown"


def test_edgar_metadata_enrichment_retries_timeout_and_preserves_fallback(
    monkeypatch,
):
    company = SimpleNamespace(
        get_filings=MagicMock(
            side_effect=ConnectTimeout("SEC handshake timed out")
        )
    )
    fake_edgar = SimpleNamespace(Company=lambda _cik: company)
    metadata = {
        "cik": "1234",
        "filingType": "10-K",
        "stockName": "Example Corp",
    }
    monkeypatch.setenv("EDGAR_IDENTITY", "Test test@example.com")

    with (
        patch.dict("sys.modules", {"edgar": fake_edgar}),
        patch("fin_pipeline.sec_edgar.time.sleep"),
    ):
        enriched = enrich_filing_metadata_with_edgartools(
            metadata, "0000000001-24-000001"
        )

    assert enriched is metadata
    assert company.get_filings.call_count == 3


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
async def test_pipeline_derives_source_type_when_missing(
    mock_graph_rel, mock_surreal_conn, mock_parse_document, sample_pdf_path
):
    mock_parse_document.return_value = {
        "text": "Annual report",
        "chunks": [],
        "tables": [],
        "table_cnt": 0,
        "reason": "Digital Native",
    }
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db

    completed = await run_ingestion_pipeline(
        {
            "filingId": "annual_report",
            "companyTicker": "XYZ",
            "stockCode": "9999",
            "exchange": "LSE",
            "filingType": "ANNUAL_REPORT",
            "sourceType": None,
        },
        sample_pdf_path,
        source="LOCAL",
    )

    assert completed is True
    assert mock_db.upsert.call_args.args[1]["sourceType"] == "glossy_report"
    mock_graph_rel.assert_awaited_once()


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
    assert mock_db.upsert.call_args.args[1]["sourceType"] == "structured_filing"
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


@pytest.mark.asyncio
async def test_html_pipeline_passes_filing_type_to_sections():
    chunks = [{"chunkIndex": 0, "pageNumber": 1, "text": "Item 3.D Risk Factors"}]
    parsed_html = {
        "text": "Item 3.D Risk Factors",
        "chunks": chunks,
        "tables": [],
        "table_cnt": 0,
        "reason": "html_beautifulsoup",
    }
    metadata = {
        "filingId": "sec_foreign_filing",
        "companyTicker": "XYZ",
        "stockCode": "UNKNOWN",
        "exchange": "UNKNOWN",
        "filingType": "20-F",
        "sourceType": "structured_filing",
    }

    with (
        patch("fin_pipeline.pipeline.parse_html_content", return_value=parsed_html),
        patch(
            "fin_pipeline.pipeline.enrich_filing_metadata_with_edgartools",
            side_effect=lambda parsed, _accession: parsed,
        ),
        patch(
            "fin_pipeline.pipeline.label_sections_structured",
            return_value=chunks,
        ) as label_sections,
    ):
        completed = await run_html_content_pipeline(
            metadata, "<p>Item 3.D Risk Factors</p>", dry_run=True
        )

    assert completed is True
    label_sections.assert_called_once_with(chunks, form_type="20-F")


@pytest.mark.asyncio
async def test_sec_stream_reports_download_failures_without_traceback():
    with patch(
        "fin_pipeline.sec_edgar.stream_filings_from_csv",
        side_effect=SecEdgarFetchError("SEC read timed out after 3 attempts"),
    ):
        completed = await process_sec_edgar_csv_stream("companies.csv")

    assert completed is False


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
    assert result["periodEndDate"] == "2021-12-31"
    assert result["filingType"] == "10-K"
    assert result["fiscalYearEndMonth"] == 12
    assert result["isAmendment"] is False
    assert result["exchange"] is None
    assert result["cik"] is None
    assert result["metadataSources"]["stockName"] == "html_ixbrl"
    assert result["metadataConfidence"]["filingDate"] >= 0.9


def test_extract_filing_metadata_parses_fiscal_year_end_and_amendment():
    html = """
    <ix:nonNumeric name="dei:DocumentPeriodEndDate">2024-03-31</ix:nonNumeric>
    <ix:nonNumeric name="dei:CurrentFiscalYearEndDate">--03-31</ix:nonNumeric>
    <ix:nonNumeric name="dei:DocumentType">10-K/A</ix:nonNumeric>
    """

    result = extract_filing_metadata(html)

    assert result["periodEndDate"] == "2024-03-31"
    assert result["filingDate"] == "2024-03-31"
    assert result["fiscalYearEndMonth"] == 3
    assert result["isAmendment"] is True


def test_html_text_excludes_xbrl_header():
    html = """
    <html><body>
      <ix:header>
        https://taxonomy.example.com/us-gaap/2024
        context-id-should-not-appear
        fact-reference-should-not-appear
        <ix:hidden>hidden inline XBRL metadata</ix:hidden>
      </ix:header>
      <p>Visible annual report body text.</p>
      <ix:nonFraction name="us-gaap:Assets" contextRef="current">42</ix:nonFraction>
    </body></html>
    """

    text = extract_text_from_html(html)
    facts = extract_xbrl_facts(html)

    assert "taxonomy.example.com" not in text
    assert "context-id-should-not-appear" not in text
    assert "fact-reference-should-not-appear" not in text
    assert "hidden inline XBRL metadata" not in text
    assert "Visible annual report body text." in text
    assert facts["assets"] == 42


def test_html_text_preserves_line_breaks():
    html = """
    <html><body>
      <h2>ITEM 1.</h2>
      <h2>BUSINESS</h2>
      <p>Our company is a technology firm.</p>
    </body></html>
    """

    text = extract_text_from_html(html)
    assert "ITEM 1." in text
    assert "BUSINESS" in text
    assert "ITEM 1.BUSINESS" not in text
    assert "ITEM 1.\nBUSINESS" in text or "ITEM 1.\n\nBUSINESS" in text


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
    mock_graph_rel, mock_surreal_conn, monkeypatch
):
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db
    monkeypatch.setattr(
        "fin_pipeline.pipeline._load_xbrl_bulk_cache",
        lambda: {
            ("0000320193-25-000079", "assets"): {
                "value": 42,
                "tag": "Assets",
                "unit": "USD",
                "period": "2025-09-27",
                "kind": "instant",
            }
        },
    )

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


async def _run_schema_placeholder_ingestion(
    metadata,
    tmp_path,
    monkeypatch,
    route,
    include_xbrl=False,
    parsed_result=None,
):
    db = AsyncMock()
    connection_context = AsyncMock()
    connection_context.__aenter__.return_value = db
    connection_context.__aexit__.return_value = False
    monkeypatch.setattr(
        "fin_pipeline.pipeline.SurrealConnection",
        lambda: connection_context,
    )
    monkeypatch.setattr(
        "fin_pipeline.pipeline.establish_graph_relations",
        AsyncMock(),
    )
    parsed = parsed_result or {
        "text": "Test filing",
        "chunks": [],
        "tables": [],
        "table_cnt": 0,
        "reason": "test",
    }
    monkeypatch.setattr(
        "fin_pipeline.pipeline.parse_document",
        lambda *_args, **_kwargs: parsed,
    )
    monkeypatch.setattr(
        "fin_pipeline.pipeline.parse_html_content",
        lambda *_args, **_kwargs: parsed,
    )
    monkeypatch.setattr(
        "fin_pipeline.pipeline.enrich_filing_metadata_with_edgartools",
        lambda result, _accession: result,
    )
    accession = metadata["filingId"].removeprefix("sec_")
    ticker = metadata.get("companyTicker", "")
    if ticker:
        accession = accession.removeprefix(f"{ticker}_")
    cache = (
        {
            (accession, "assets"): {
                "value": 42,
                "tag": "Assets",
                "unit": "USD",
                "period": "2023-12-31",
                "kind": "instant",
            }
        }
        if include_xbrl
        else {}
    )
    monkeypatch.setattr(
        "fin_pipeline.pipeline._load_xbrl_bulk_cache",
        lambda: cache,
    )
    if route in {"file", "pdf"}:
        extension = "pdf" if route == "pdf" else "html"
        document_path = tmp_path / f"placeholder.{extension}"
        document_path.write_text("<html><body>Test filing</body></html>")
        completed = await run_ingestion_pipeline(
            metadata, str(document_path), source="SEC", ocr_enabled=False
        )
    else:
        completed = await run_html_content_pipeline(
            metadata, "<html><body>Test filing</body></html>"
        )

    assert completed is True
    db.upsert.assert_awaited_once()
    return db.upsert.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["file", "html"])
async def test_new_fields_default_to_none(tmp_path, monkeypatch, route):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": f"schema_defaults_{route}",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
        },
        tmp_path,
        monkeypatch,
        route,
    )

    for field in (
        "periodEndDate",
        "fiscalYearEndMonth",
        "pageCount",
        "industryCodeRaw",
        "gicsCode",
        "gicsSector",
        "industryConfidence",
        "xbrlCurrency",
        "xbrlProvenance",
    ):
        assert record[field] is None
    assert record["language"] == "en"
    assert record["xbrlSource"] == "none"
    assert record["isAmendment"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["file", "html"])
async def test_new_fields_persist_from_metadata(tmp_path, monkeypatch, route):
    metadata_fields = {
        "periodEndDate": "2023-12-31",
        "fiscalYearEndMonth": 12,
        "isAmendment": True,
        "language": "en",
        "pageCount": 42,
        "industryCodeRaw": "IT Software & Services",
        "gicsCode": "45102010",
        "gicsSector": "45",
        "industryConfidence": 0.95,
    }
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": f"schema_metadata_{route}",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
            **metadata_fields,
        },
        tmp_path,
        monkeypatch,
        route,
    )

    assert record["periodEndDate"] == "2023-12-31T00:00:00Z"
    for field, value in metadata_fields.items():
        if field != "periodEndDate":
            assert record[field] == value


@pytest.mark.asyncio
async def test_is_amendment_default_false(tmp_path, monkeypatch):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "schema_amendment_default",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
        },
        tmp_path,
        monkeypatch,
        "html",
    )

    assert record["isAmendment"] is False


@pytest.mark.asyncio
async def test_period_end_date_validator_appends_time(tmp_path, monkeypatch):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "schema_period_end",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
            "periodEndDate": "2023-12-31",
        },
        tmp_path,
        monkeypatch,
        "html",
    )

    assert record["periodEndDate"] == "2023-12-31T00:00:00Z"


@pytest.mark.asyncio
async def test_html_pipeline_uses_parsed_period_end_and_language_default(
    tmp_path, monkeypatch
):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "html_period_end",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
        },
        tmp_path,
        monkeypatch,
        "html",
        parsed_result={
            "text": "Test filing",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": "test",
            "periodEndDate": "2023-12-31",
        },
    )

    assert record["periodEndDate"] == "2023-12-31T00:00:00Z"
    assert record["fiscalYearEndMonth"] == 12
    assert record["language"] == "en"


@pytest.mark.asyncio
async def test_pdf_pipeline_uses_page_count_and_detected_language(
    tmp_path, monkeypatch
):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "pdf_metadata",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "ANNUAL_REPORT",
            "filingDate": "2024-03-31",
        },
        tmp_path,
        monkeypatch,
        "pdf",
        parsed_result={
            "text": "Test filing",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": "test",
            "page_count": 215,
            "language": "ja",
        },
    )

    assert record["pageCount"] == 215
    assert record["language"] == "ja"
    assert record["periodEndDate"] == "2024-03-31T00:00:00Z"
    assert record["fiscalYearEndMonth"] == 3


@pytest.mark.asyncio
async def test_pdf_filename_date_is_used_for_period_end(tmp_path, monkeypatch):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "pdf_filename_period",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "ANNUAL_REPORT",
            "filingDate": "2024-12-31",
            "metadataSources": {"filingDate": "filename_year_inferred"},
        },
        tmp_path,
        monkeypatch,
        "pdf",
        parsed_result={
            "text": "Test filing",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": "test",
            "periodEndDate": "2024-03-31",
            "fiscalYearEndMonth": 3,
        },
    )

    assert record["periodEndDate"] == "2024-12-31T00:00:00Z"
    assert record["fiscalYearEndMonth"] == 12


@pytest.mark.asyncio
async def test_industry_and_amendment_metadata_persist(tmp_path, monkeypatch):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "html_metadata_input",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K/A",
            "industryCodeRaw": "IT Software & Services",
        },
        tmp_path,
        monkeypatch,
        "html",
    )

    assert record["industryCodeRaw"] == "IT Software & Services"
    assert record["isAmendment"] is True


@pytest.mark.asyncio
async def test_missing_pdf_language_remains_none(tmp_path, monkeypatch):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "pdf_missing_language",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "ANNUAL_REPORT",
        },
        tmp_path,
        monkeypatch,
        "pdf",
    )

    assert record["language"] is None
    assert record["isAmendment"] is False


@pytest.mark.asyncio
async def test_xbrl_fields_populate_from_bulk_cache(tmp_path, monkeypatch):
    record = await _run_schema_placeholder_ingestion(
        {
            "filingId": "schema_xbrl_placeholders",
            "companyTicker": "TEST",
            "stockCode": "UNKNOWN",
            "exchange": "UNKNOWN",
            "filingType": "10-K",
            "xbrlCurrency": "USD",
            "xbrlProvenance": '{"assets": {"tag": "Assets"}}',
            "xbrlSource": "bulk",
        },
        tmp_path,
        monkeypatch,
        "html",
        include_xbrl=True,
    )

    assert json.loads(record["xbrlFacts"]) == {"assets": 42}
    assert record["xbrlCurrency"] == "USD"
    assert json.loads(record["xbrlProvenance"]) == {
        "assets": {
            "tag": "Assets",
            "unit": "USD",
            "period": "2023-12-31",
        }
    }
    assert record["xbrlSource"] == "bulk"


def test_existing_records_remain_valid_without_new_fields():
    record = ExchangeFilingModel(
        filingId="legacy_record",
        companyTicker="TEST",
        stockCode="UNKNOWN",
        exchange="UNKNOWN",
        filingType="10-K",
        source="SEC",
    ).model_dump()

    for field in (
        "periodEndDate",
        "fiscalYearEndMonth",
        "language",
        "pageCount",
        "industryCodeRaw",
        "gicsCode",
        "gicsSector",
        "industryConfidence",
        "xbrlCurrency",
        "xbrlProvenance",
        "xbrlSource",
    ):
        assert record[field] is None
    assert record["isAmendment"] is False


@pytest.mark.asyncio
@patch("fin_pipeline.pipeline.SurrealConnection")
@patch("fin_pipeline.pipeline.establish_graph_relations", new_callable=AsyncMock)
async def test_40f_exhibit_section_map_persists(mock_graph_rel, mock_surreal_conn):
    mock_db = AsyncMock()
    mock_surreal_conn.return_value.__aenter__.return_value = mock_db
    exhibit_section_map = '{"EX-99.1": "business"}'
    parsed_html = {
        "text": "Annual Information Form",
        "chunks": [{"chunkIndex": 0, "pageNumber": 1, "text": "Annual Information Form"}],
        "tables": [],
        "table_cnt": 0,
        "reason": "html_beautifulsoup",
    }

    with (
        patch("fin_pipeline.pipeline.parse_html_content", return_value=parsed_html),
        patch(
            "fin_pipeline.pipeline.enrich_filing_metadata_with_edgartools",
            side_effect=lambda parsed, _accession: parsed,
        ),
    ):
        completed = await run_html_content_pipeline(
            {
                "filingId": "sec_test_0001-24-000001",
                "companyTicker": "TEST",
                "stockCode": "UNKNOWN",
                "exchange": "UNKNOWN",
                "filingType": "40-F",
                "sourceType": "structured_filing",
                "exhibitSectionMap": exhibit_section_map,
            },
            "<html><body>Annual Information Form</body></html>",
        )

    assert completed is True
    payload = mock_db.upsert.call_args.args[1]
    assert payload["exhibitSectionMap"] == exhibit_section_map
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
async def test_surreal_connection_initializes_schema_once(mock_http_conn, monkeypatch):
    """The app should use the repository's helper-backed connection adapter."""
    mock_db = AsyncMock()
    mock_http_conn.return_value = mock_db
    monkeypatch.setattr(connection_module, "_schema_initialized", False)

    with patch("fin_pipeline.db.connection.initialize_schema") as mock_schema:
        async with SurrealConnection() as db:
            assert db is mock_db
        async with SurrealConnection() as db:
            assert db is mock_db

    assert mock_http_conn.call_count == 2
    mock_schema.assert_called_once()
    assert mock_db.connect.await_count == 2
    assert mock_db.signin.await_count == 2
    mock_db.signin.assert_awaited_with(
        {
            "user": "root",
            "pass": "secret",
            "namespace": "finance",
            "database": "analytics",
        }
    )
    assert mock_db.use.await_count == 2
    mock_db.use.assert_awaited_with(
        namespace="finance", database="analytics"
    )
    assert mock_db.close.await_count == 2


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
            "filingType": "OTHER",
            "sourceType": "unknown",
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
