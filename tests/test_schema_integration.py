# tests/test_schema_integration.py
import json
import os

import pytest

from fin_pipeline.db.connection import SurrealConnection

pytestmark = pytest.mark.skipif(
    not os.getenv("SURREAL_INTEGRATION"),
    reason="set SURREAL_INTEGRATION=1 to run against a live SurrealDB",
)


@pytest.mark.asyncio
async def test_full_record_round_trip_preserves_new_fields():
    payload = {
        "filingId": "test_roundtrip_001",
        "companyTicker": "TEST",
        "stockCode": "TEST",
        "exchange": "NYSE",
        "filingType": "10-K",
        "source": "SEC",
        "sourceType": "structured_filing",
        "reportingYear": 2024,
        "documentChunks": [
            {
                "chunkIndex": 0,
                "pageNumber": 1,
                "sectionLabel": "business",
                "text": "AI operations.",
                "charStart": 0,
                "charEnd": 15,
            }
        ],
        "xbrlFacts": {"revenue": 1.0, "capex": 0.5, "assets": 9.9},
    }
    async with SurrealConnection() as db:
        await db.delete_record(f"exchange_filing:{payload['filingId']}")
        await db.upsert(f"exchange_filing:{payload['filingId']}", payload)
        rows = await db.query(
            f"SELECT sourceType, reportingYear, documentChunks, xbrlFacts "
            f"FROM exchange_filing:{payload['filingId']};"
        )
    record = rows[0]["result"][0]
    assert record["sourceType"] == "structured_filing"
    assert record["reportingYear"] == 2024
    assert json.loads(record["documentChunks"])[0]["sectionLabel"] == "business"
    assert json.loads(record["xbrlFacts"])["revenue"] == 1.0
