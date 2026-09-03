"""SEC filing retrieval from CSV company lists using edgartools."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterator

from fin_pipeline.config.settings import EDGAR_IDENTITY
from fin_pipeline.config.logger import pipeline_logger as log


def read_company_csv(csv_path: str) -> Iterator[dict]:
    """Yield normalized company/filter rows from a CSV file.

    Required per row: ``ticker`` or ``cik``.
    Optional: ``forms`` (or ``form``), ``year``, and ``max_filings``.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV must include a header row")
        for line_number, raw_row in enumerate(reader, start=2):
            row = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in raw_row.items()
            }
            if not row.get("ticker") and not row.get("cik"):
                raise ValueError(f"CSV row {line_number} requires ticker or cik")
            yield row


def _forms(row: dict) -> list[str] | None:
    value = row.get("forms") or row.get("form")
    return (
        [item.strip().upper() for item in value.split(",") if item.strip()] or None
        if value
        else None
    )


def _year(row: dict) -> int | None:
    value = row.get("year")
    return int(value) if value else None


def _max_filings(row: dict) -> int | None:
    value = row.get("max_filings")
    return int(value) if value else None


def _stream_filings_from_csv(
    csv_path: str,
    year_range: tuple[int, int] | None = None,
    forms: list[str] | None = None,
) -> Iterator[tuple[str, dict]]:
    """Fetch primary HTML documents and yield their content and metadata."""
    if not (os.getenv("EDGAR_IDENTITY") or EDGAR_IDENTITY):
        raise RuntimeError(
            "EDGAR_IDENTITY is required for SEC downloads, e.g. "
            "Your Name your.email@example.com"
        )

    from edgar import Company, set_identity

    set_identity(os.getenv("EDGAR_IDENTITY") or EDGAR_IDENTITY)
    for row in read_company_csv(csv_path):
        identifier = row.get("cik") or row.get("ticker")
        company = Company(identifier)
        ticker = (
            row.get("ticker")
            or company.get_ticker()
            or f"CIK{str(company.cik).zfill(10)}"
        )
        requested_forms = forms if forms is not None else _forms(row)
        requested_years = (
            list(range(year_range[0], year_range[1] + 1)) if year_range else _year(row)
        )
        filings = company.get_filings(
            form=requested_forms,
            year=requested_years,
            amendments=False,
            trigger_full_load=True,
        )
        limit = _max_filings(row)
        for filing in filings[:limit] if limit else filings:
            html_content = filing.html()
            if not html_content:
                log.warning(f"Skipping {filing.accession_no}: no HTML primary document")
                continue

            accession = filing.accession_no
            metadata = {
                "filingId": f"sec_{ticker.upper()}_{accession}",
                "companyTicker": ticker.upper(),
                "stockCode": "UNKNOWN",
                "exchange": "UNKNOWN",
                "filingType": filing.form,
                "stockName": filing.company,
                "filingDate": getattr(filing, "report_date", None)
                or filing.filing_date,
                "referencedTickers": [],
                "cik": str(filing.cik).zfill(10),
            }
            yield html_content, metadata


def fetch_filings_from_csv(
    csv_path: str, download_dir: str
) -> Iterator[tuple[Path, dict]]:
    """Fetch primary HTML documents and yield locally saved file/metadata pairs."""
    destination = Path(download_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for html_content, metadata in _stream_filings_from_csv(csv_path):
        file_path = (
            destination
            / f"{metadata['companyTicker']}_{metadata['filingId'].removeprefix('sec_').removeprefix(metadata['companyTicker'] + '_')}_primary.html"
        )
        file_path.write_text(html_content, encoding="utf-8")
        yield file_path, metadata


def stream_filings_from_csv(
    csv_path: str,
    year_range: tuple[int, int] | None = None,
    forms: list[str] | None = None,
) -> Iterator[tuple[str, dict]]:
    """Yield SEC primary HTML content and metadata without writing to disk."""
    return _stream_filings_from_csv(csv_path, year_range=year_range, forms=forms)
