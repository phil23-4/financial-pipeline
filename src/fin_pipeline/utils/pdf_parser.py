import re
from collections.abc import Iterable
from math import isfinite
from typing import Any, TypedDict

import camelot
import pymupdf
import pymupdf4llm
from playa.exceptions import PDFTextExtractionNotAllowed
from pymupdf4llm.helpers.document_layout import OCRMode
from pymupdf4llm.ocr import rapidtess_api

from fin_pipeline.utils.metadata import (
    extract_metadata_from_text,
    set_field_candidate,
)


class ExtractedTable(TypedDict):
    tableIndex: int
    pageNumber: int | None
    headers: list[str]
    rows: list[list[str]]
    rowCount: int
    accuracy: float | None
    markdown: str


class PdfParseResult(TypedDict, total=False):
    text: str
    tables: list[ExtractedTable]
    table_cnt: int
    reason: str
    warnings: list[str]
    stockName: str | None
    filingDate: str | None
    filingType: str | None
    exchange: str | None
    cik: str | None
    lei: str | None
    isin: str | None
    cusip: str | None
    sedol: str | None
    metadataSources: dict[str, str]
    metadataConfidence: dict[str, float]


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


_GENERIC_PDF_PROPERTY_PATTERNS = (
    r"^untitled(?: document)?$",
    r"^(?:document|report|final|draft|copy)$",
    r"^(?:annual|integrated|esg|sustainability)\s+report$",
    r"^(?:company|corporate)\s+(?:report|document)$",
    r"^adobe indesign(?: -|:)?",
    r"^adobe acrobat(?: -|:)?",
    r"^microsoft (?:word|office)(?: -|:)?",
    r"^quarkxpress(?: -|:)?",
    r"^canva(?: -|:)?",
    r"^created with ",
    r"^produced by ",
)


def _is_usable_pdf_property(value: str | None) -> bool:
    """Reject common software/template defaults from PDF properties."""
    if not value:
        return False
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized or len(normalized) > 240:
        return False
    return not any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _GENERIC_PDF_PROPERTY_PATTERNS
    )


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
        if _is_usable_pdf_property(props.get("title")):
            title = props["title"]
            title_metadata = extract_metadata_from_text(
                title, "pdf_title_fallback", company_text=company_text or title
            )
            report_title_match = re.search(
                r"(?i)\b(?:annual report|integrated report|sustainability report|esg report)\b",
                title,
            )
            legal_title_match = re.search(
                r"(?i)\b(?:inc\.?|corp\.?|limited|ltd\.?|group|holdings|plc|ag|gmbh|sa|s\.a\.|bhd\.|pte\.?|llc)\b",
                title,
            )
            if title_metadata.get("stockName"):
                if report_title_match:
                    set_field_candidate(
                        candidate_map,
                        "stockName",
                        title_metadata["stockName"],
                        "pdf_properties",
                        0.82,
                    )
                elif legal_title_match:
                    set_field_candidate(
                        candidate_map,
                        "stockName",
                        title,
                        "pdf_properties",
                        0.84,
                    )
            elif not report_title_match or legal_title_match:
                set_field_candidate(
                    candidate_map,
                    "stockName",
                    title,
                    "pdf_properties",
                    0.84 if legal_title_match else 0.68,
                )
            if title_metadata.get("filingType"):
                set_field_candidate(
                    candidate_map,
                    "filingType",
                    title_metadata["filingType"],
                    "pdf_properties",
                    0.8,
                )
            if title_metadata.get("exchange"):
                set_field_candidate(
                    candidate_map,
                    "exchange",
                    title_metadata["exchange"],
                    "pdf_properties",
                    0.72,
                )
        if _is_usable_pdf_property(props.get("subject")):
            set_field_candidate(
                candidate_map, "filingType", props["subject"], "pdf_properties", 0.82
            )
        if _is_usable_pdf_property(props.get("keywords")):
            keyword_metadata = extract_metadata_from_text(
                props["keywords"], "pdf_property_keywords"
            )
            if keyword_metadata.get("exchange"):
                set_field_candidate(
                    candidate_map,
                    "exchange",
                    keyword_metadata["exchange"],
                    "pdf_properties",
                    0.72,
                )

    metadata_fields = [
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

    def add_text_candidates(
        extracted: dict[str, Any], default_source: str, confidence_scale: float
    ) -> None:
        for field in metadata_fields:
            if field not in extracted or extracted[field] is None:
                continue
            source = extracted.get("metadataSources", {}).get(field, default_source)
            confidence = extracted.get("metadataConfidence", {}).get(field, 0.8)
            set_field_candidate(
                candidate_map,
                field,
                extracted[field],
                source,
                confidence * confidence_scale,
            )

    priority_text = company_text.strip() if company_text else text[:8000]
    if priority_text:
        priority_source = "pdf_cover_text" if company_text else "pdf_text_regex"
        add_text_candidates(
            extract_metadata_from_text(
                priority_text, priority_source, company_text=priority_text
            ),
            priority_source,
            1.0,
        )

    if text and text != priority_text:
        add_text_candidates(
            extract_metadata_from_text(text, "pdf_text_regex"),
            "pdf_text_regex",
            0.75,
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
        non_empty = [line for line in lines if line]
        if len(non_empty) < 4:
            continue
        candidates = set(non_empty[:2] + non_empty[-2:])
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
    if value is None:
        return ""

    if isinstance(value, float) and not isfinite(value):
        return ""

    text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def _camelot_tables(
    file_path: str, warnings: list[str] | None = None
) -> list[ExtractedTable]:
    """Extract tables with Camelot, preferring ruled tables over text tables."""

    tables: list[Any] = []
    for flavor in ("lattice", "stream"):
        try:
            tables = camelot.read_pdf(file_path, pages="all", flavor=flavor)
        except PDFTextExtractionNotAllowed as exc:
            if warnings is not None:
                warnings.append(f"Camelot {flavor} extraction skipped: {exc}")
            break
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            if warnings is not None:
                warnings.append(f"Camelot {flavor} extraction failed: {exc}")
            tables = []
        if tables:
            break

    extracted: list[ExtractedTable] = []
    for index, table in enumerate(tables):
        try:
            rows = [
                [_cell_text(cell) for cell in row]
                for row in table.df.values.tolist()
            ]
            rows = [row for row in rows if any(row)]
            if not rows:
                continue
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
            first_row, remaining_rows = rows[0], rows[1:]
            has_header = _looks_like_table_header(first_row, remaining_rows)
            if has_header:
                headers = first_row
                body = remaining_rows
            else:
                headers = [f"Column {column + 1}" for column in range(width)]
                body = rows
            parsing_report = getattr(table, "parsing_report", {}) or {}
            markdown_rows = [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join("---" for _ in headers) + " |",
            ]
            markdown_rows.extend("| " + " | ".join(row) + " |" for row in body)
            page = getattr(table, "page", None)
            extracted.append(
                {
                    "tableIndex": index,
                    "pageNumber": int(page) if page else None,
                    "headers": [str(header) for header in headers],
                    "rows": body,
                    "rowCount": len(body),
                    "accuracy": parsing_report.get("accuracy"),
                    "markdown": "\n".join(markdown_rows),
                }
            )
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            if warnings is not None:
                warnings.append(f"Camelot table {index} skipped: {exc}")
    return extracted


def _looks_like_table_header(first_row: list[str], body: list[list[str]]) -> bool:
    """Conservatively decide whether the first extracted row is a header."""
    if not body or any(not cell for cell in first_row):
        return True
    header_terms = (
        "asset",
        "category",
        "date",
        "description",
        "name",
        "period",
        "revenue",
        "total",
        "value",
        "year",
    )
    normalized = [cell.casefold() for cell in first_row]
    return any(
        any(term in cell for term in header_terms) for cell in normalized
    )


def parse_pdf_layout(
    file_path: str, force_ocr: bool = False, extract_tables: bool = True
) -> PdfParseResult:
    """Extract Markdown, tables, and metadata using native PyMuPDF4LLM OCR."""
    warnings: list[str] = []
    extraction_reason = (
        "PyMuPDF4LLM + native hybrid OCR" if force_ocr else "PyMuPDF4LLM"
    )
    with pymupdf.open(file_path) as doc:
        try:
            page_chunks = pymupdf4llm.to_markdown(
                doc,
                page_chunks=True,
                use_ocr=OCRMode.SELECT_REMOVING_OLD,
                force_ocr=force_ocr,
                ocr_function=rapidtess_api.exec_ocr,
            )
        except (
            pymupdf.mupdf.FzErrorArgument,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            warnings.append(f"PyMuPDF4LLM OCR extraction failed: {exc}")
            try:
                page_chunks = pymupdf4llm.to_markdown(
                    doc,
                    page_chunks=True,
                    use_ocr=OCRMode.NEVER,
                    force_ocr=False,
                )
                extraction_reason = "PyMuPDF4LLM (OCR fallback)"
            except (
                pymupdf.mupdf.FzErrorArgument,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as fallback_exc:
                warnings.append(
                    f"PyMuPDF4LLM native text fallback failed: {fallback_exc}"
                )
                page_chunks = []

        if isinstance(page_chunks, str):
            page_texts = [page_chunks]
        else:
            page_texts = [chunk.get("text", "") for chunk in page_chunks]
        text = _clean_markdown_text(page_texts)
        tables = _camelot_tables(file_path, warnings) if extract_tables else []
        if not extract_tables:
            warnings.append("Camelot table extraction disabled")
        if tables:
            text += "\n\n## Extracted Tables\n\n" + "\n\n".join(
                table["markdown"] for table in tables
            )
        metadata = extract_filing_metadata(
            text, doc, company_text="\n".join(page_texts[:3])
        )

    if not text:
        warnings.append("No usable text was extracted from the PDF")
        if extraction_reason == "PyMuPDF4LLM (OCR fallback)":
            extraction_reason = "PyMuPDF4LLM (OCR fallback; no native text)"

    result: PdfParseResult = {
        "text": text,
        "tables": tables,
        "table_cnt": len(tables),
        "reason": extraction_reason,
        "warnings": warnings,
        **metadata,
    }
    return result
