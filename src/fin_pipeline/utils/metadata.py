"""Shared metadata extraction helpers for PDF and HTML filings."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

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

_CANONICAL_FIELDS = (
    "stockName",
    "filingDate",
    "filingType",
    "exchange",
    "cik",
    "lei",
    "isin",
    "cusip",
    "sedol",
)


def _clean_metadata_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip().replace("\u00a0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def _set_candidate(
    candidates: Dict[str, Dict[str, Any]],
    field: str,
    value: Optional[str],
    source: str,
    confidence: float,
) -> None:
    cleaned = _clean_metadata_value(value)
    if cleaned is None:
        return
    current = candidates.get(field)
    if current is None or confidence > current["confidence"]:
        candidates[field] = {
            "value": cleaned,
            "source": source,
            "confidence": float(confidence),
            "raw": value,
        }


def _normalize_filing_type(filing_type: str) -> str:
    text = filing_type.strip()
    if not text:
        return text
    return text.title() if "report" in text.lower() else text.upper()


def _detect_exchange_from_text(text: str) -> Optional[str]:
    text_lower = text.lower()
    for name, normalized in sorted(
        EXCHANGE_MAPPING.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", text_lower):
            return normalized
    return None


def _normalize_html_date(value: str) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        return "-".join(match.groups())
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s*,?\s+(\d{4})",
        value,
        re.I,
    )
    if not match:
        return None
    normalized_date = match.group(0).replace("\u00a0", " ")
    normalized_date = re.sub(r"\s+", " ", normalized_date).strip()
    normalized_date = re.sub(r"\s*,\s*", ", ", normalized_date)
    try:
        return datetime.strptime(normalized_date, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def extract_metadata_from_text(text: str, source: str) -> Dict[str, Any]:
    """Extract metadata candidates from textual content using regex patterns."""
    candidates: Dict[str, Dict[str, Any]] = {}

    filing_match = FILING_TYPE_PATTERN.search(text)
    if filing_match:
        filing_value = filing_match.group(1)
        _set_candidate(
            candidates, "filingType", _normalize_filing_type(filing_value), source, 0.86
        )

    date_match = DATE_PATTERN.search(text)
    if date_match:
        parsed_date = parse_extracted_date_str(date_match.group(1))
        if parsed_date:
            _set_candidate(candidates, "filingDate", parsed_date, source, 0.84)

    cik_match = CIK_PATTERN.search(text)
    if cik_match:
        _set_candidate(candidates, "cik", cik_match.group(1), source, 0.9)

    lei_match = LEI_PATTERN.search(text)
    if lei_match:
        _set_candidate(candidates, "lei", lei_match.group(1).upper(), source, 0.82)

    isin_match = ISIN_PATTERN.search(text)
    if isin_match:
        _set_candidate(candidates, "isin", isin_match.group(1).upper(), source, 0.82)

    cusip_match = CUSIP_PATTERN.search(text)
    if cusip_match:
        _set_candidate(candidates, "cusip", cusip_match.group(1).upper(), source, 0.82)

    sedol_match = SEDOL_PATTERN.search(text)
    if sedol_match:
        _set_candidate(candidates, "sedol", sedol_match.group(1).upper(), source, 0.82)

    company_match = COMPANY_PATTERN.search(text)
    if company_match:
        company_name = company_match.group(1).strip()
        if 3 < len(company_name) < 200:
            _set_candidate(candidates, "stockName", company_name, source, 0.78)

    exchange = _detect_exchange_from_text(text)
    if exchange:
        _set_candidate(candidates, "exchange", exchange, source, 0.8)

    return merge_metadata_candidates(candidates)


def merge_metadata_candidates(candidates: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Convert metadata candidate records into a dict plus provenance/confidence maps."""
    result = {field: None for field in _CANONICAL_FIELDS}
    metadata_sources: Dict[str, str] = {}
    metadata_confidence: Dict[str, float] = {}

    for field, details in candidates.items():
        result[field] = details["value"]
        metadata_sources[field] = details["source"]
        metadata_confidence[field] = float(details["confidence"])

    if metadata_sources:
        result["metadataSources"] = metadata_sources
        result["metadataConfidence"] = metadata_confidence
    return result


def set_field_candidate(
    candidates: Dict[str, Dict[str, Any]],
    field: str,
    value: Optional[str],
    source: str,
    confidence: float,
) -> None:
    _set_candidate(candidates, field, value, source, confidence)
