import re
import pdfplumber
from typing import Any, Dict, Optional

from pypdf import PdfReader

from fin_pipeline.config.constants import (
    CIK_PATTERN,
    COMPANY_PATTERN,
    CUSIP_PATTERN,
    DATE_PATTERN,
    EXCHANGE_MAPPING,
    FILING_TYPE_PATTERN,
    ISIN_PATTERN,
    LEI_PATTERN,
    SEDOL_PATTERN,
    parse_extracted_date_str,
)
from fin_pipeline.utils.ocr_engine import extract_text_via_ocr


def _extract_pdf_metadata_from_properties(reader: PdfReader) -> Dict[str, Optional[str]]:
    """Extract metadata from PDF document properties.

    Attempts to read metadata embedded by the PDF creator.
    """
    try:
        metadata = reader.metadata
        if not metadata:
            return {}

        # Normalize metadata keys (they might have different casings)
        normalized = {}
        for key, value in metadata.items():
            if key and isinstance(value, str):
                normalized[key.lower()] = value.strip()

        return normalized
    except Exception:
        return {}


def _extract_filing_metadata_from_text(text: str) -> Dict[str, Optional[str]]:
    """Extract filing metadata using regex patterns from PDF text."""
    metadata: Dict[str, Optional[str]] = {}

    filing_match = FILING_TYPE_PATTERN.search(text)
    if filing_match:
        matched_str = filing_match.group(1)
        metadata["filing_type"] = (
            matched_str.title()
            if "report" in matched_str.lower()
            else matched_str.upper()
        )

    date_match = DATE_PATTERN.search(text)
    if date_match:
        parsed_date = parse_extracted_date_str(date_match.group(1))
        if parsed_date:
            metadata["filing_date"] = parsed_date

    # Security & Corporate Identifiers
    cik_match = CIK_PATTERN.search(text)
    if cik_match:
        metadata["cik"] = cik_match.group(1)

    lei_match = LEI_PATTERN.search(text)
    if lei_match:
        metadata["lei"] = lei_match.group(1).upper()

    isin_match = ISIN_PATTERN.search(text)
    if isin_match:
        metadata["isin"] = isin_match.group(1).upper()

    cusip_match = CUSIP_PATTERN.search(text)
    if cusip_match:
        metadata["cusip"] = cusip_match.group(1).upper()

    sedol_match = SEDOL_PATTERN.search(text)
    if sedol_match:
        metadata["sedol"] = sedol_match.group(1).upper()

    # Company name extraction
    company_match = COMPANY_PATTERN.search(text)
    if company_match:
        company_name = company_match.group(1).strip()
        if 3 < len(company_name) < 200:
            metadata["stock_name"] = company_name

    # Exchange detection based on broad mapping dictionary
    text_lower = text.lower()
    exchange = next(
        (
            normalized
            for name, normalized in sorted(
                EXCHANGE_MAPPING.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            )
            if re.search(
                rf"(?<!\w){re.escape(name.lower())}(?!\w)",
                text_lower,
            )
        ),
        None,
    )
    if exchange:
        metadata["exchange"] = exchange

    return metadata


def extract_filing_metadata(text: str, reader: Optional[PdfReader] = None) -> Dict[str, Any]:
    """Extract company and reporting-period metadata from PDF.

    Attempts multiple extraction strategies:
    1. PDF document properties (if available)
    2. Regex pattern matching on document text
    3. Internationalized patterns for global filings

    Returns a dict with keys matching HTML parser schema:
    - stockName: Company name
    - filingDate: Report period end date (YYYY-MM-DD format)
    - filingType: Form type (10-K, 10-Q, etc.)
    - exchange: Stock exchange (NYSE, NASDAQ, etc.)
    - cik: SEC Central Index Key
    - lei: Legal Entity Identifier (Global)
    - isin: International Securities Identification Number (Global)

    Returns only fields that were successfully extracted.
    """
    metadata = {}

    # Try PDF properties first (most reliable if available)
    if reader:
        props = _extract_pdf_metadata_from_properties(reader)
        if props.get("title"):
            metadata["stock_name"] = props["title"]
        if props.get("subject"):
            metadata["filing_type"] = props["subject"]
        if props.get("keywords"):
            metadata["exchange"] = props["keywords"]

    # Extract from text (covers both digital and OCR'd PDFs)
    text_metadata = _extract_filing_metadata_from_text(text)
    metadata.update(text_metadata)

    # Normalize keys to match parser output
    result = {}
    if metadata.get("stock_name"):
        result["stockName"] = metadata["stock_name"]
    if metadata.get("filing_date"):
        result["filingDate"] = metadata["filing_date"]
    if metadata.get("filing_type"):
        result["filingType"] = metadata["filing_type"]
    if metadata.get("exchange"):
        result["exchange"] = metadata["exchange"]

    # Include both SEC and International Identifiers
    if metadata.get("cik"):
        result["cik"] = metadata["cik"]
    if metadata.get("lei"):
        result["lei"] = metadata["lei"]
    if metadata.get("isin"):
        result["isin"] = metadata["isin"]
    if metadata.get("cusip"):
        result["cusip"] = metadata["cusip"]
    if metadata.get("sedol"):
        result["sedol"] = metadata["sedol"]

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