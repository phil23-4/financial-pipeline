import re
import pdfplumber
from typing import Any, Dict, Optional

from pypdf import PdfReader

from fin_pipeline.utils.metadata import extract_metadata_from_text, merge_metadata_candidates, set_field_candidate
from fin_pipeline.utils.ocr_engine import extract_text_via_ocr


def _clean_metadata_value(value: Optional[str]) -> Optional[str]:
    """Normalize whitespace and common PDF noise in extracted metadata values."""
    if value is None:
        return None
    cleaned = str(value).strip().replace("\u00a0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def _update_candidate_metadata(
    candidates: Dict[str, Dict[str, Any]],
    field: str,
    value: Optional[str],
    source: str,
    confidence: float,
) -> None:
    """Keep the highest-confidence match for each metadata field."""
    parsed_value = _clean_metadata_value(value)
    if not parsed_value:
        return

    current = candidates.get(field)
    if current is None or confidence > current["confidence"]:
        candidates[field] = {
            "value": parsed_value,
            "source": source,
            "confidence": confidence,
            "raw": value,
        }


def _extract_pdf_metadata_from_properties(reader: PdfReader) -> Dict[str, Optional[str]]:
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
    except Exception:
        return {}


def extract_filing_metadata(text: str, reader: Optional[PdfReader] = None) -> Dict[str, Any]:
    """Extract company and reporting-period metadata from PDF.

    Attempts multiple extraction strategies with confidence tracking so callers can
    understand where each field was sourced from.
    """
    candidate_map: Dict[str, Dict[str, Any]] = {}

    if reader:
        props = _extract_pdf_metadata_from_properties(reader)
        if props.get("title"):
            set_field_candidate(candidate_map, "stockName", props["title"], "pdf_properties", 0.88)
        if props.get("subject"):
            set_field_candidate(candidate_map, "filingType", props["subject"], "pdf_properties", 0.82)
        if props.get("keywords"):
            set_field_candidate(candidate_map, "exchange", props["keywords"], "pdf_properties", 0.72)

    text_metadata = extract_metadata_from_text(text, "pdf_text_regex")
    for field in ["stockName", "filingDate", "filingType", "exchange", "cik", "lei", "isin", "cusip", "sedol"]:
        if field in text_metadata and text_metadata[field] is not None:
            candidate_map[field] = {
                "value": text_metadata[field],
                "source": text_metadata.get("metadataSources", {}).get(field, "pdf_text_regex"),
                "confidence": text_metadata.get("metadataConfidence", {}).get(field, 0.8),
            }

    result: Dict[str, Any] = {
        "stockName": None,
        "filingDate": None,
        "filingType": None,
        "exchange": None,
        "cik": None,
        "lei": None,
        "isin": None,
        "cusip": None,
        "sedol": None,
    }
    metadata_sources: Dict[str, str] = {}
    metadata_confidence: Dict[str, float] = {}

    for field, details in candidate_map.items():
        result[field] = details["value"]
        metadata_sources[field] = details["source"]
        metadata_confidence[field] = details["confidence"]

    if metadata_sources:
        result["metadataSources"] = metadata_sources
        result["metadataConfidence"] = metadata_confidence

    return result


def parse_pdf_layout(file_path: str) -> dict:
    """Extract text, tables, and metadata from a PDF."""
    extracted_text = []
    extracted_tables = []
    table_cnt = 0

    reader = PdfReader(file_path)

    # extract_text() may return None for scanned or malformed pages.
    is_digital = any(
        (page.extract_text() or "").strip()
        for page in reader.pages[:2]
    )

    if is_digital:
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text: extracted_text.append(text)

                # Two-pass extraction: Attempt default grid extraction first;
                # fallback to 'text' strategy for borderless/stylized annual report tables
                tables = page.extract_tables()
                if not tables:
                    tables = page.extract_tables({
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text"
                    })

                for table in (tables or []):
                    # Filter out empty or broken table structures safely
                    valid_rows = [r for r in table if r and any(cell is not None for cell in r)]
                    if not valid_rows:
                        continue

                    # Safe column length estimation using valid rows
                    num_cols = max(len(r) for r in valid_rows)
                    headers = [str(c).strip() if c else "" for c in valid_rows[0]]

                    rows = [
                        "| " + " | ".join([str(cell).replace("\n", " ").strip() if cell else "" for cell in r]) + " |"
                        for r in valid_rows
                    ]

                    # Insert Markdown table delimiter row safely
                    rows.insert(1, "| " + " | ".join(["---"] * num_cols) + " |")

                    extracted_tables.append({
                        "tableIndex": table_cnt,
                        "pageNumber": page_num,
                        "headers": headers,
                        "rowCount": len(valid_rows) - 1,
                        "markdown": "\n".join(rows)
                    })
                    table_cnt += 1

        full_text = "\n".join(extracted_text)
        metadata = extract_filing_metadata(full_text, reader)

        return {
            "text": full_text,
            "tables": extracted_tables,
            "table_cnt": table_cnt,
            "reason": "Digital Native",
            **metadata,
        }

    ocr_text = extract_text_via_ocr(file_path)
    metadata = extract_filing_metadata(ocr_text, reader)

    return {
        "text": ocr_text,
        "tables": [],
        "table_cnt": 0,
        "reason": "OCR Scanner Fallback",
        **metadata,
    }