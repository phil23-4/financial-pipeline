import re
from collections.abc import Iterable
from typing import Any

import camelot
import pymupdf
import pymupdf4llm
from pymupdf4llm.helpers.document_layout import OCRMode
from pymupdf4llm.ocr import rapidtess_api

from fin_pipeline.utils.metadata import (
    extract_metadata_from_text,
    set_field_candidate,
)


def _extract_pdf_metadata_from_properties(
    reader: pymupdf.Document,
) -> dict[str, str | None]:
    """Extract metadata from PDF document properties.

    Attempts to read metadata embedded by the PDF creator.
    """
    try:
        metadata = reader.metadata
        if not metadata:
            return {}

        normalized = {}
        for key, value in metadata.items():
            if key and isinstance(value, str):
                normalized[key.lower()] = value.strip()

        return normalized
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return {}


def extract_filing_metadata(
    text: str,
    reader: pymupdf.Document | None = None,
    company_text: str | None = None,
) -> dict[str, Any]:
    """Extract company and reporting-period metadata from PDF.

    Attempts multiple extraction strategies with confidence tracking so callers can
    understand where each field was sourced from.
    """
    candidate_map: dict[str, dict[str, Any]] = {}

    if reader:
        props = _extract_pdf_metadata_from_properties(reader)
        if props.get("title"):
            set_field_candidate(
                candidate_map, "stockName", props["title"], "pdf_properties", 0.88
            )
        if props.get("subject"):
            set_field_candidate(
                candidate_map, "filingType", props["subject"], "pdf_properties", 0.82
            )
        if props.get("keywords"):
            set_field_candidate(
                candidate_map, "exchange", props["keywords"], "pdf_properties", 0.72
            )

    text_metadata = extract_metadata_from_text(
        text, "pdf_text_regex", company_text=company_text
    )
    for field in [
        "stockName",
        "filingDate",
        "filingType",
        "exchange",
        "cik",
        "lei",
        "isin",
        "cusip",
        "sedol",
    ]:
        if field in text_metadata and text_metadata[field] is not None:
            set_field_candidate(
                candidate_map,
                field,
                text_metadata[field],
                text_metadata.get("metadataSources", {}).get(field, "pdf_text_regex"),
                text_metadata.get("metadataConfidence", {}).get(field, 0.8),
            )

    result: dict[str, Any] = {
        k: None
        for k in [
            "stockName",
            "filingDate",
            "filingType",
            "exchange",
            "cik",
            "lei",
            "isin",
            "cusip",
            "sedol",
        ]
    }
    metadata_sources: dict[str, str] = {}
    metadata_confidence: dict[str, float] = {}

    for field, details in candidate_map.items():
        result[field] = details["value"]
        metadata_sources[field] = details["source"]
        metadata_confidence[field] = details["confidence"]

    if metadata_sources:
        result["metadataSources"] = metadata_sources
        result["metadataConfidence"] = metadata_confidence

    return result


def _clean_markdown_text(page_texts: Iterable[str]) -> str:
    """Remove repeated page furniture and normalize common PDF text artifacts."""
    pages = [[line.strip() for line in text.splitlines()] for text in page_texts]
    line_counts: dict[str, int] = {}
    for lines in pages:
        candidates = set(lines[:3] + lines[-3:])
        for line in candidates:
            if line and len(line) < 160:
                line_counts[line] = line_counts.get(line, 0) + 1

    repeated = {
        line for line, count in line_counts.items() if count >= 2 and len(pages) > 1
    }
    cleaned_pages = []
    for lines in pages:
        cleaned = []
        for line in lines:
            if line in repeated:
                continue
            line = re.sub(r"^[\u2022\u2023\u25e6\u2043]\s*", "- ", line)
            line = re.sub(r"^(?:o|▪|◦)\s+", "- ", line)
            line = re.sub(r"[ \t]+", " ", line).strip()
            cleaned.append(line)

        while cleaned and not cleaned[0]:
            cleaned.pop(0)
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        cleaned_pages.append("\n".join(cleaned))

    markdown = "\n\n".join(page for page in cleaned_pages if page)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def _cell_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def _camelot_tables(file_path: str) -> list[dict]:
    """Extract tables with Camelot, preferring ruled tables over text tables."""

    try:
        tables = camelot.read_pdf(file_path, pages="all", flavor="lattice")
    except (OSError, RuntimeError, TypeError, ValueError):
        tables = camelot.read_pdf(file_path, pages="all", flavor="stream")
    if not tables:
        tables = camelot.read_pdf(file_path, pages="all", flavor="stream")

    extracted = []
    for index, table in enumerate(tables):
        rows = [[_cell_text(cell) for cell in row] for row in table.df.values.tolist()]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        headers = rows[0]
        if not any(headers):
            headers = [f"Column {column + 1}" for column in range(width)]
        body = rows[1:]
        parsing_report = getattr(table, "parsing_report", {}) or {}
        markdown_rows = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        markdown_rows.extend("| " + " | ".join(row) + " |" for row in body)
        extracted.append(
            {
                "tableIndex": index,
                "pageNumber": int(table.page) if table.page else None,
                "headers": [str(header) for header in headers],
                "rowCount": len(body),
                "accuracy": parsing_report.get("accuracy"),
                "markdown": "\n".join(markdown_rows),
            }
        )
    return extracted


def parse_pdf_layout(file_path: str, force_ocr: bool = False) -> dict:
    """Extract Markdown, tables, and metadata using native PyMuPDF4LLM OCR."""
    with pymupdf.open(file_path) as doc:
        page_chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            use_ocr=OCRMode.SELECT_REMOVING_OLD,
            force_ocr=force_ocr,
            ocr_function=rapidtess_api.exec_ocr,
        )

        if isinstance(page_chunks, str):
            page_texts = [page_chunks]
        else:
            page_texts = [chunk.get("text", "") for chunk in page_chunks]
        text = _clean_markdown_text(page_texts)
        try:
            tables = _camelot_tables(file_path)
        except (OSError, RuntimeError, TypeError, ValueError):
            tables = []
        if tables:
            text += "\n\n## Extracted Tables\n\n" + "\n\n".join(
                table["markdown"] for table in tables
            )
        metadata = extract_filing_metadata(
            text, doc, company_text="\n".join(page_texts[:3])
        )

    return {
        "text": text,
        "tables": tables,
        "table_cnt": len(tables),
        "reason": "PyMuPDF4LLM + native hybrid OCR" if force_ocr else "PyMuPDF4LLM",
        **metadata,
    }
