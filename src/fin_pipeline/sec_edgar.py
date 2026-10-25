"""SEC filing retrieval from CSV company lists using edgartools."""

from __future__ import annotations

import csv
import html as html_module
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

from bs4 import BeautifulSoup

from fin_pipeline.config.logger import pipeline_logger as log
from fin_pipeline.config.settings import EDGAR_IDENTITY

_STRUCTURED_FORMS = {"10-K", "10-Q", "20-F", "40-F", "8-K", "S-1", "S-3", "S-4"}
_HTML_FETCH_ATTEMPTS = 3
_Result = TypeVar("_Result")
_EXHIBIT_SECTION_KEYWORDS = {
    "annual information form": "business",
    "information form": "business",
    "management's discussion": "mdna",
    "management discussion": "mdna",
    "md&a": "mdna",
    "operating and financial review": "mdna",
    "audited financial": "financial_statements",
    "consolidated financial statements": "financial_statements",
    "financial statements": "financial_statements",
}
_SECTION_MARKERS = {
    "business": "Annual Information Form",
    "mdna": "Management's Discussion and Analysis",
    "financial_statements": "Audited Financial Statements",
}


class SecEdgarFetchError(RuntimeError):
    """Raised when a filing cannot be fetched after retrying transient errors."""


def _retry_sec_request(
    operation: Callable[[], _Result],
    description: str,
) -> _Result:
    """Retry transient SEC network failures and report exhausted requests."""
    from httpx import NetworkError, TimeoutException

    for attempt in range(1, _HTML_FETCH_ATTEMPTS + 1):
        try:
            return operation()
        except (NetworkError, TimeoutException) as exc:
            if attempt == _HTML_FETCH_ATTEMPTS:
                raise SecEdgarFetchError(
                    f"Unable to complete {description} after "
                    f"{_HTML_FETCH_ATTEMPTS} attempts: {exc}"
                ) from exc

            delay = 2 ** (attempt - 1)
            log.warning(
                f"SEC request failed for {description} "
                f"(attempt {attempt}/{_HTML_FETCH_ATTEMPTS}): {exc}. "
                f"Retrying in {delay}s."
            )
            time.sleep(delay)

    raise RuntimeError("SEC request exited without a result")


def _fetch_filing_html(filing) -> str | None:
    """Fetch filing HTML, retrying transient SEC network failures."""
    accession = getattr(filing, "accession_no", "unknown")
    return _retry_sec_request(
        filing.html,
        f"HTML fetch for filing {accession}",
    )


def _map_exhibit_to_section(description: str, doc_type: str) -> str:
    desc = description.lower()
    for keyword, section in _EXHIBIT_SECTION_KEYWORDS.items():
        if keyword in desc:
            return section
    if doc_type == "EX-99.1":
        return "business"
    if doc_type == "EX-99.2":
        return "mdna"
    if doc_type == "EX-99.3":
        return "financial_statements"
    return "other"


def _classify_exhibit_by_content(content: str) -> str | None:
    """Classify an exhibit from its opening heading when it is recognizable."""
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(["head", "script", "style", "meta"]):
        tag.decompose()
    visible = soup.get_text(" ", strip=True)[:800].lower()
    if (
        "consolidated financial statements" in visible
        or "audited financial statements" in visible
    ):
        return "financial_statements"
    if (
        "management's discussion and analysis" in visible
        or "management discussion and analysis" in visible
        or "md&a" in visible
    ):
        return "mdna"
    if "annual information form" in visible:
        return "business"
    return None


def _exhibit_content(exhibit) -> str | None:
    """Extract usable HTML or text content from an SEC exhibit."""
    for method_name in ("html", "content", "text"):
        method = getattr(exhibit, method_name, None)
        if not method or not callable(method):
            continue
        try:
            candidate = method()
        except (AttributeError, TypeError, OSError) as exc:
            log.debug(f"Exhibit .{method_name}() failed: {type(exc).__name__}: {exc}")
            continue
        if isinstance(candidate, bytes):
            candidate = candidate.decode("utf-8", errors="replace")
        if not isinstance(candidate, str) or len(candidate) < 500:
            continue
        if candidate[:4] == "%PDF":
            log.warning("Exhibit is PDF, skipping")
            return None
        if "<" not in candidate[:500]:
            return f"<pre>{html_module.escape(candidate)}</pre>"
        return candidate
    return None


def _get_40f_exhibit_content(filing) -> tuple[str | None, dict[str, str]]:
    """Concatenate usable EX-99.* exhibits and add section headings."""
    try:
        exhibits = list(filing.exhibits)
    except (AttributeError, TypeError) as exc:
        log.warning(f"40-F {filing.accession_no}: .exhibits unavailable: {exc}")
        return None, {}

    parts: list[str] = []
    section_map: dict[str, str] = {}
    for exhibit in exhibits:
        doc_type = str(getattr(exhibit, "document_type", "") or "").upper().strip()
        if not (doc_type.startswith("EX-99.") or doc_type == "EX-99"):
            continue

        content = _exhibit_content(exhibit)
        if content is None:
            log.warning(
                f"40-F {filing.accession_no}: could not extract {doc_type}"
            )
            continue

        description = str(getattr(exhibit, "description", "") or "")
        section = _classify_exhibit_by_content(content)
        classification_method = "content"
        if section is None:
            section = _map_exhibit_to_section(description, doc_type)
            classification_method = "description/sequence"
        marker_label = _SECTION_MARKERS.get(section, "Exhibit")
        parts.append(f"<h1>{marker_label}</h1>\n{content}")
        section_map[doc_type] = section
        log.debug(
            f"40-F {filing.accession_no}: {doc_type} "
            f"({len(content)} chars) -> {section} ({classification_method})"
        )

    if not parts:
        log.warning(f"40-F {filing.accession_no}: no usable EX-99.* exhibits")
        return None, {}

    return "\n\n".join(parts), section_map


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


def _parse_fy_end_month(value: object | None) -> int | None:
    """Parse a fiscal year-end month from dates, numeric formats, or month names."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.month
    if isinstance(value, int) and 1 <= value <= 12:
        return value

    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).month
    except ValueError:
        pass

    numeric_match = re.fullmatch(r"(?:--?)?(\d{1,2})[-/]?(\d{1,2})", text)
    if numeric_match:
        month = int(numeric_match.group(1))
        if 1 <= month <= 12:
            return month

    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    month_match = re.match(r"^([a-z]+)", text.lower())
    if month_match:
        return month_names.get(month_match.group(1))
    return None


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
        try:
            company = _retry_sec_request(
                lambda identifier=identifier: Company(identifier),
                f"company lookup for {identifier}",
            )
            ticker = (
                row.get("ticker")
                or _retry_sec_request(
                    company.get_ticker,
                    f"ticker lookup for {identifier}",
                )
                or f"CIK{str(company.cik).zfill(10)}"
            )
            requested_forms = forms if forms is not None else _forms(row)
            requested_years = (
                list(range(year_range[0], year_range[1] + 1))
                if year_range
                else _year(row)
            )
            filings = _retry_sec_request(
                lambda company=company, requested_forms=requested_forms, requested_years=requested_years: company.get_filings(
                    form=requested_forms,
                    year=requested_years,
                    amendments=False,
                    trigger_full_load=True,
                ),
                f"filings lookup for {identifier}",
            )
        except Exception as exc:  # noqa: BLE001 - skip failed companies and continue
            log.error(
                f"Skipping {identifier}: filings lookup failed after "
                f"retries ({type(exc).__name__}: {exc}); continuing"
            )
            continue

        if not filings:
            log.info(f"Skipping {identifier}: no filings found for the requested filters")
            continue

        limit = _max_filings(row)
        for filing in filings[:limit] if limit else filings:
            try:
                form = str(filing.form).upper()
                section_map: dict[str, str] = {}
                if form == "40-F":
                    html_content, section_map = _get_40f_exhibit_content(filing)
                    if html_content is None:
                        log.warning(
                            f"Skipping 40-F {filing.accession_no} ({ticker}): "
                            "no retrievable exhibits"
                        )
                        continue
                    log.info(
                        f"40-F {filing.accession_no} ({ticker}): concatenated "
                        f"{len(section_map)} exhibits -> {len(html_content)} chars"
                    )
                else:
                    html_content = _fetch_filing_html(filing)
                    if not html_content:
                        log.warning(
                            f"Skipping {filing.accession_no}: no HTML primary document"
                        )
                        continue
            except Exception as exc:  # noqa: BLE001 - skip failed filings and continue
                log.error(
                    f"Skipping {filing.accession_no} ({ticker}): html retrieval failed "
                    f"({type(exc).__name__}: {exc})"
                )
                continue

            accession = filing.accession_no
            report_date = getattr(filing, "report_date", None)
            if isinstance(report_date, (date, datetime)):
                reporting_year = report_date.year
            elif report_date:
                reporting_year = date.fromisoformat(str(report_date)[:10]).year
            else:
                reporting_year = None
            period_end_date = str(report_date) if report_date else None
            fiscal_year_end_month = _parse_fy_end_month(
                getattr(filing, "fiscal_year_end", None)
            )
            if fiscal_year_end_month is None and period_end_date:
                fiscal_year_end_month = _parse_fy_end_month(period_end_date)
            form_upper = str(filing.form).upper()
            metadata = {
                "filingId": f"sec_{ticker.upper()}_{accession}",
                "companyTicker": ticker.upper(),
                "stockCode": "UNKNOWN",
                "exchange": "UNKNOWN",
                "filingType": filing.form,
                "sourceType": (
                    "structured_filing"
                    if str(filing.form).upper() in _STRUCTURED_FORMS
                    else "unknown"
                ),
                "reportingYear": reporting_year,
                "stockName": filing.company,
                "filingDate": report_date or filing.filing_date,
                "periodEndDate": period_end_date,
                "fiscalYearEndMonth": fiscal_year_end_month,
                "isAmendment": (form_upper.endswith("/A") or "AMEND" in form_upper),
                "language": "en",
                "pageCount": None,
                "industryCodeRaw": row.get("industry"),
                "referencedTickers": [],
                "cik": str(filing.cik).zfill(10),
                "exhibitSectionMap": json.dumps(section_map) if section_map else None,
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
