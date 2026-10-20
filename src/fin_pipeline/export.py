"""Export SurrealDB filings and document chunks to Parquet."""

import json
import os
from collections.abc import Mapping
from importlib import import_module
from typing import Any

from fin_pipeline.db.connection import _surrealql_literal
from fin_pipeline.db.db import surreal_query


def _build_export_query(filters: dict | None) -> str:
    filters = filters or {}
    supported_filters = {"sourceType", "companyTicker", "filingDate"}
    unsupported = set(filters) - supported_filters
    if unsupported:
        raise ValueError(f"Unsupported export filters: {', '.join(sorted(unsupported))}")

    conditions = []
    for key in ("sourceType", "companyTicker"):
        value = filters.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{key} filter must be a string")
            conditions.append(f"{key} = {_surrealql_literal(value)}")

    date_range = filters.get("filingDate")
    if date_range is not None:
        if isinstance(date_range, Mapping):
            start = date_range.get("start")
            end = date_range.get("end")
        elif isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start, end = date_range
        else:
            raise ValueError(
                "filingDate filter must be a {start, end} mapping or a two-item range"
            )
        if start is not None:
            conditions.append(f"filingDate >= {_surrealql_literal(start)}")
        if end is not None:
            conditions.append(f"filingDate <= {_surrealql_literal(end)}")

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return f"SELECT * FROM exchange_filing{where};"


def _filing_rows(response: Any) -> list[dict]:
    if isinstance(response, dict):
        if response.get("error"):
            raise RuntimeError(f"SurrealDB export query failed: {response['error']}")
        result = response.get("result")
        if isinstance(result, list):
            return [row for row in result if isinstance(row, dict)]
        return []

    if isinstance(response, list):
        rows: list[dict] = []
        for statement in response:
            if not isinstance(statement, dict):
                continue
            if statement.get("status") == "ERR":
                raise RuntimeError(
                    f"SurrealDB export query failed: {statement.get('result')}"
                )
            result = statement.get("result")
            if isinstance(result, list):
                rows.extend(row for row in result if isinstance(row, dict))
        return rows
    raise RuntimeError("SurrealDB export query returned an unexpected response")


def _parquet_scalar(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if value is not None and not isinstance(value, (str, int, float, bool)):
        return str(value)
    return value


def export_filings_to_parquet(
    output_dir: str, filters: dict | None = None
) -> str:
    """Write filtered filing records and their chunks to Parquet files."""
    try:
        import pandas as pd
        import_module("pyarrow")
    except ImportError as exc:
        raise RuntimeError(
            "Parquet export requires the optional dependencies; install with "
            "`pip install fin_pipeline[export]`."
        ) from exc

    response = surreal_query(_build_export_query(filters), timeout=240)
    filings = _filing_rows(response)
    os.makedirs(output_dir, exist_ok=True)
    filings_path = os.path.join(output_dir, "filings.parquet")
    chunks_path = os.path.join(output_dir, "chunks.parquet")

    filing_rows = [
        {key: _parquet_scalar(value) for key, value in filing.items()}
        for filing in filings
    ]
    chunk_rows = []
    for filing in filings:
        filing_id = filing.get("filingId")
        document_chunks = filing.get("documentChunks") or []
        if isinstance(document_chunks, str):
            document_chunks = json.loads(document_chunks)
        if not isinstance(document_chunks, list):
            continue
        for chunk in document_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_rows.append(
                {
                    "filingId": filing_id,
                    "chunkIndex": chunk.get("chunkIndex"),
                    "pageNumber": chunk.get("pageNumber"),
                    "sectionLabel": chunk.get("sectionLabel"),
                    "text": chunk.get("text"),
                }
            )

    pd.DataFrame(filing_rows).to_parquet(
        filings_path, index=False, engine="pyarrow"
    )
    pd.DataFrame(
        chunk_rows,
        columns=[
            "filingId",
            "chunkIndex",
            "pageNumber",
            "sectionLabel",
            "text",
        ],
    ).to_parquet(chunks_path, index=False, engine="pyarrow")
    return filings_path
