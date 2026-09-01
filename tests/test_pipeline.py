import pytest
from unittest.mock import AsyncMock, patch
from fin_pipeline.pipeline import run_ingestion_pipeline

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
