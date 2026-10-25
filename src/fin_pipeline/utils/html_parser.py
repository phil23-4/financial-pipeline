"""HTML text and table extraction for SEC Edgar filings."""

import os
import re
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger as log

from fin_pipeline.config.constants import EXCHANGE_MAPPING
from fin_pipeline.utils.metadata import (
    _normalize_html_date,
    merge_metadata_candidates,
    set_field_candidate,
)


def _ixbrl_value(soup: BeautifulSoup, field_name: str) -> str | None:
    """Read a standard Inline XBRL metadata field by its local name."""
    field_name = field_name.lower()
    for tag in soup.find_all(attrs={"name": True}):
        name = tag.get("name", "").lower()
        if name.rsplit(":", 1)[-1] == field_name:
            value = tag.get_text(" ", strip=True)
            if value:
                return value
    return None


def _candidate_value(
    field: str,
    value: str | None,
    source: str,
    confidence: float,
    candidates: dict[str, dict[str, Any]],
) -> None:
    """Record the strongest discovered value for a metadata field."""
    if not value:
        return
    cleaned = value.strip().replace("\u00a0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return
    current = candidates.get(field)
    if current is None or confidence > current["confidence"]:
        candidates[field] = {
            "value": cleaned,
            "source": source,
            "confidence": confidence,
        }


def extract_filing_metadata(html_content: str) -> dict[str, Any]:
    """Extract company and reporting-period metadata from Inline XBRL HTML."""
    soup = BeautifulSoup(html_content, "html.parser")
    candidates: dict[str, dict[str, Any]] = {}

    company_name = _ixbrl_value(soup, "EntityRegistrantName")
    if company_name:
        set_field_candidate(candidates, "stockName", company_name, "html_ixbrl", 0.92)

    filing_date = _ixbrl_value(soup, "DocumentPeriodEndDate")
    if filing_date:
        parsed_date = _normalize_html_date(filing_date)
        if parsed_date:
            set_field_candidate(
                candidates, "filingDate", parsed_date, "html_ixbrl", 0.94
            )

    filing_type = _ixbrl_value(soup, "DocumentType")
    if filing_type:
        set_field_candidate(candidates, "filingType", filing_type, "html_ixbrl", 0.93)

    cik = _ixbrl_value(soup, "EntityCentralIndexKey")
    if cik:
        set_field_candidate(candidates, "cik", cik, "html_ixbrl", 0.9)

    visible_text = soup.get_text(" ", strip=True).lower()
    exchange = next(
        (
            normalized
            for name, normalized in EXCHANGE_MAPPING.items()
            if name in visible_text
        ),
        None,
    )
    if exchange:
        set_field_candidate(candidates, "exchange", exchange, "html_text_scan", 0.72)

    result = merge_metadata_candidates(candidates)
    result.setdefault("stockName", None)
    result.setdefault("filingDate", None)
    result.setdefault("filingType", None)
    result.setdefault("exchange", None)
    result.setdefault("cik", None)
    return result


def enrich_filing_metadata_with_edgartools(
    metadata: dict[str, Any], accession_number: str
) -> dict[str, Any]:
    """Best-effort SEC metadata enrichment using edgartools.

    The local parser remains authoritative for document text and tables. This
    helper only fills missing metadata and returns unchanged data when the
    optional dependency, identity, or SEC network is unavailable.
    """
    import os

    from fin_pipeline.config.settings import EDGAR_IDENTITY

    if not (os.getenv("EDGAR_IDENTITY") or EDGAR_IDENTITY) or not metadata.get("cik"):
        return metadata

    from fin_pipeline.sec_edgar import SecEdgarFetchError, _retry_sec_request

    try:
        from edgar import Company

        filings = _retry_sec_request(
            lambda: Company(metadata["cik"]).get_filings(
                form=metadata.get("filingType"),
                accession_number=accession_number,
            ),
            f"metadata enrichment for filing {accession_number}",
        )
        if not filings:
            return metadata
        filing = filings[0]
        enriched = dict(metadata)
        for key, attributes in {
            "stockName": ("company_name", "companyName"),
            "filingDate": ("period_of_report", "periodOfReport"),
            "filingType": ("form",),
            "exchange": ("exchange",),
        }.items():
            if enriched.get(key) in (None, "", "UNKNOWN"):
                for attribute in attributes:
                    value = getattr(filing, attribute, None)
                    if value:
                        enriched[key] = (
                            value.isoformat()
                            if hasattr(value, "isoformat")
                            else str(value)
                        )
                        break
        return enriched
    except SecEdgarFetchError as exc:
        log.warning(
            f"SEC metadata enrichment unavailable for filing "
            f"{accession_number} after retries: {exc}"
        )
        return metadata
    except (AttributeError, ImportError, KeyError, OSError, TypeError, ValueError):
        return metadata


def extract_text_from_html(html_content: str) -> str:
    """Extract visible text from HTML while removing hidden Inline XBRL noise."""
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup.find_all(["ix:header", "ix:hidden", "ix:references"]):
        tag.decompose()

    for tag in soup.find_all(
        style=re.compile(
            r"display\s*:\s*none|visibility\s*:\s*hidden",
            re.I,
        )
    ):
        tag.decompose()

    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()

    text = soup.get_text("\n", strip=True)

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text.strip()


def _split_text_into_chunks(text: str, max_chars: int = 3000) -> list[dict[str, Any]]:
    """Group paragraphs into approximate 3,000-character pseudo-pages."""
    paragraphs: list[str] = []
    for paragraph in text.split("\n\n"):
        while len(paragraph) > max_chars:
            split_at = paragraph.rfind(" ", 0, max_chars)
            if split_at <= 0:
                split_at = max_chars - 1
            split_at += 1
            paragraphs.append(paragraph[:split_at])
            paragraph = paragraph[split_at:]
        if paragraph:
            paragraphs.append(paragraph)

    page_texts: list[str] = []
    current_paragraphs: list[str] = []
    current_length = 0
    for paragraph in paragraphs:
        added_length = len(paragraph) + (2 if current_paragraphs else 0)
        if current_paragraphs and current_length + added_length > max_chars:
            page_texts.append("\n\n".join(current_paragraphs))
            current_paragraphs = []
            current_length = 0
            added_length = len(paragraph)
        current_paragraphs.append(paragraph)
        current_length += added_length

    if current_paragraphs:
        page_texts.append("\n\n".join(current_paragraphs))

    return [
        {
            "chunkIndex": chunk_index,
            "pageNumber": chunk_index + 1,
            "sectionLabel": None,
            "text": page_text,
            "charStart": None,
            "charEnd": None,
        }
        for chunk_index, page_text in enumerate(page_texts)
        if page_text.strip()
    ]


def extract_tables_from_html(html_content: str) -> tuple:
    """Extract HTML tables and convert to markdown format.

    Returns:
        tuple: (tables_list, table_count, extraction_reason)
    """
    soup = BeautifulSoup(html_content, "html.parser")
    tables = soup.find_all("table")

    extracted_tables = []
    for table_idx, table in enumerate(tables):
        try:
            rows = table.find_all("tr")
            if not rows:
                continue

            # Extract headers
            headers = []
            first_row_cells = rows[0].find_all(["th", "td"])
            for cell in first_row_cells:
                headers.append(cell.get_text(strip=True))

            # Extract data rows
            markdown_lines = []
            if headers:
                markdown_lines.append("| " + " | ".join(headers) + " |")
                markdown_lines.append("|" + "".join(["------|" for _ in headers]))

            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if cells:
                    row_data = [cell.get_text(strip=True) for cell in cells]
                    markdown_lines.append("| " + " | ".join(row_data) + " |")

            markdown_table = "\n".join(markdown_lines)

            extracted_tables.append(
                {
                    "tableIndex": table_idx,
                    "sheetName": None,
                    "pageNumber": None,
                    "headers": headers,
                    "rowCount": len(rows) - 1,
                    "markdown": markdown_table,
                }
            )
        except (AttributeError, TypeError, ValueError) as exc:
            log.debug(f"Skipping malformed HTML table {table_idx}: {exc}")

    return extracted_tables, len(extracted_tables), "html_beautifulsoup"


def parse_html_content(html_content: str) -> dict[str, Any]:
    """Parse HTML already held in memory without creating a local file."""
    text = extract_text_from_html(html_content)
    tables, table_cnt, reason = extract_tables_from_html(html_content)
    return {
        "text": text,
        "chunks": _split_text_into_chunks(text),
        "tables": tables,
        "table_cnt": table_cnt,
        "reason": reason,
        **extract_filing_metadata(html_content),
    }


def parse_html_file(file_path: str) -> dict[str, Any]:
    """Parse an HTML SEC Edgar filing and extract text and tables.

    Args:
        file_path: Path to HTML file

    Returns:
        Dictionary with extracted content and metadata
    """
    if not os.path.exists(file_path):
        return {
            "text": "",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": "File not found",
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except UnicodeDecodeError:
        # Try alternative encoding
        with open(file_path, "r", encoding="latin-1") as f:
            html_content = f.read()
    except OSError as e:
        return {
            "text": "",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": f"Read error: {e!s}",
        }

    try:
        return parse_html_content(html_content)
    except (AttributeError, TypeError, ValueError) as e:
        return {
            "text": "",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": f"Parse error: {e!s}",
        }
