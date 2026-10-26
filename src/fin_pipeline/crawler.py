import glob
import os
import re
from calendar import monthrange
from collections.abc import Generator
from typing import Any

from loguru import logger as log

from fin_pipeline.config.constants import (
    DEFAULT_EXCHANGE,
    DEFAULT_FILING_TYPE,
    DEFAULT_TICKER,
    LOCAL_EXCHANGE,
)
from fin_pipeline.utils.crypto import calculate_file_hash


def _filing_date_for_year(year: int, fiscal_year_end_month: int) -> str:
    last_day = monthrange(year, fiscal_year_end_month)[1]
    return f"{year:04d}-{fiscal_year_end_month:02d}-{last_day:02d}"


def _parse_local_pdf_metadata(
    file_path: str, base_name: str, fiscal_year_end_month: int = 12
) -> dict[str, Any]:
    """Parse identity and report details from a local PDF path and filename."""
    metadata = {
        "stockName": None,
        "stockCode": DEFAULT_TICKER,
        "companyTicker": DEFAULT_TICKER,
        "filingType": DEFAULT_FILING_TYPE,
        "filingDate": None,
        "sourceType": "unknown",
        "reportingYear": None,
        "metadataSources": {},
        "metadataConfidence": {},
    }

    structured_name_match = re.fullmatch(
        r"(?P<company_name>.+)_(?P<year>\d{4})_"
        r"(?:(?P<stock_code>[^_]+)_)?"
        r"(?P<report_code>ar|annual[_-]?report|sustainability[_-]?report|"
        r"integrated[_-]?report|esg[_-]?report)"
        r"(?:_(?P<language>[a-z]{2}))?_(?P<page>\d+)",
        base_name,
        re.IGNORECASE,
    )
    if structured_name_match:
        values = structured_name_match.groupdict()
        company_name = re.sub(r"[_-]+", " ", values["company_name"]).strip()
        parent_directory = os.path.basename(os.path.dirname(file_path))
        report_name = re.sub(r"[_-]+", " ", values["report_code"]).lower()
        report_type = {
            "annual report": "ANNUAL_REPORT",
            "ar": "ANNUAL_REPORT",
            "sustainability report": "SUSTAINABILITY_REPORT",
            "integrated report": "INTEGRATED_REPORT",
            "esg report": "ESG_REPORT",
        }[report_name]
        metadata.update(
            {
                "stockName": company_name,
                "stockCode": values["stock_code"] or DEFAULT_TICKER,
                "companyTicker": parent_directory.upper(),
                "filingType": report_type,
                "filingDate": _filing_date_for_year(
                    int(values["year"]), fiscal_year_end_month
                ),
                "sourceType": "glossy_report",
                "reportingYear": int(values["year"]),
            }
        )
        metadata["metadataSources"].update(
            {
                "stockName": "filename_company_name",
                "companyTicker": "parent_directory",
                "stockCode": "filename"
                if values["stock_code"]
                else "directory_scan_default",
                "filingType": "filename",
                "filingDate": "filename_year_inferred",
            }
        )
        metadata["metadataConfidence"].update(
            {
                "stockName": 0.9,
                "companyTicker": 0.95,
                "stockCode": 0.9 if values["stock_code"] else 0.0,
                "filingType": 0.9,
                "filingDate": 0.7,
            }
        )
        return metadata

    file_name_match = re.fullmatch(
        r"(?P<stock_code>[^.]+)\.(?P<report_code>ar|annual[_-]?report)\."
        r"(?P<language>[a-z]{2})\.(?P<year>\d{4})",
        base_name,
        re.IGNORECASE,
    )
    if file_name_match:
        values = file_name_match.groupdict()
        metadata.update(
            {
                "stockCode": values["stock_code"],
                "filingType": (
                    "ANNUAL_REPORT"
                    if values["report_code"].lower()
                    in {"ar", "annual_report", "annual-report"}
                    else DEFAULT_FILING_TYPE
                ),
                "filingDate": _filing_date_for_year(
                    int(values["year"]), fiscal_year_end_month
                ),
                "sourceType": "glossy_report",
                "reportingYear": int(values["year"]),
            }
        )
        metadata["metadataSources"].update(
            {
                "stockCode": "filename",
                "filingType": "filename",
                "filingDate": "filename_year_inferred",
            }
        )
        metadata["metadataConfidence"].update(
            {"stockCode": 0.9, "filingType": 0.9, "filingDate": 0.7}
        )
    else:
        normalized_name = re.sub(r"[_-]+", " ", base_name)
        normalized_name = re.sub(r"\s+", " ", normalized_name).strip()
        report_match = re.search(
            r"(?i)(?P<report>"
            r"annual\s+report|integrated\s+report|"
            r"sustainability\s+report|esg\s+report|"
            r"annualreport|integratedreport|sustainabilityreport|esgreport"
            r")",
            normalized_name,
        )
        year_match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", normalized_name)

        if report_match:
            report_name = re.sub(r"\s+", " ", report_match.group("report")).lower()
            report_type = {
                "annualreport": "ANNUAL_REPORT",
                "annual report": "ANNUAL_REPORT",
                "integratedreport": "INTEGRATED_REPORT",
                "integrated report": "INTEGRATED_REPORT",
                "sustainabilityreport": "SUSTAINABILITY_REPORT",
                "sustainability report": "SUSTAINABILITY_REPORT",
                "esgreport": "ESG_REPORT",
                "esg report": "ESG_REPORT",
            }[report_name]
            metadata["filingType"] = report_type
            metadata["sourceType"] = "glossy_report"
            metadata["metadataSources"]["filingType"] = "filename_report_type"
            metadata["metadataConfidence"]["filingType"] = 0.75

            prefix = normalized_name[: report_match.start()].strip(" -:()")
            prefix = re.sub(r"(?i)\b(?:fy|fiscal year)\s*$", "", prefix).strip()
            code_match = re.fullmatch(r"(?i)(?P<code>[a-z0-9][a-z0-9./]{1,20})", prefix)
            if code_match and not re.fullmatch(r"(?:19|20)\d{2}", prefix):
                metadata["stockCode"] = code_match.group("code")
                metadata["metadataSources"]["stockCode"] = "filename_code"
                metadata["metadataConfidence"]["stockCode"] = 0.75
            elif prefix:
                metadata["filenameCompanyName"] = prefix

        if year_match:
            reporting_year = int(year_match.group(0))
            metadata["filingDate"] = _filing_date_for_year(
                reporting_year, fiscal_year_end_month
            )
            metadata["reportingYear"] = reporting_year
            metadata["metadataSources"]["filingDate"] = "filename_year_inferred"
            metadata["metadataConfidence"]["filingDate"] = 0.55

    if metadata["sourceType"] != "glossy_report" and re.search(
        r"(?i)(?<![A-Z0-9])(?:10-K|10-Q|20-F|40-F|8-K|S-1|S-3|S-4)(?![A-Z0-9])",
        base_name,
    ):
        metadata["sourceType"] = "structured_filing"

    parent_directory = os.path.basename(os.path.dirname(file_path)).strip()
    if (
        metadata["companyTicker"] == DEFAULT_TICKER
        and len(parent_directory) <= 10
        and re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", parent_directory)
        and parent_directory != DEFAULT_TICKER
    ):
        metadata["companyTicker"] = parent_directory
        metadata["metadataSources"]["companyTicker"] = "parent_directory"
        metadata["metadataConfidence"]["companyTicker"] = 0.8

    company_path = os.path.dirname(file_path)
    company_name = os.path.basename(company_path).replace("_", " ").strip()
    if metadata.get("filenameCompanyName"):
        metadata["stockName"] = metadata["filenameCompanyName"]
        metadata["metadataSources"]["stockName"] = "filename_company_name"
        metadata["metadataConfidence"]["stockName"] = 0.65
    elif company_name and company_name != "." and metadata["companyTicker"] == DEFAULT_TICKER:
        metadata["stockName"] = company_name
        metadata["metadataSources"]["stockName"] = "parent_directory"
        metadata["metadataConfidence"]["stockName"] = 0.95

    metadata.pop("filenameCompanyName", None)

    return metadata


def scan_directory(
    dir_path: str,
    recursive: bool = True,
    fiscal_year_end_month: int = 12,
    skip_existing: bool = False,
    skip_by_hash: bool = False,
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """Scans local directories for financial PDF assets and prepares baseline meta attributes."""
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Target path is not a valid directory: {dir_path}")

    search_pattern = (
        os.path.join(dir_path, "**", "*.pdf")
        if recursive
        else os.path.join(dir_path, "*.pdf")
    )
    pdf_files = glob.glob(search_pattern, recursive=recursive)
    if skip_existing or skip_by_hash:
        from fin_pipeline.pipeline import _load_existing_filings

        existing_ids, existing_hashes = _load_existing_filings("local_")
        log.info(f"Resume check: {len(existing_ids)} local_* filings already in DB")
    else:
        existing_ids, existing_hashes = set(), set()

    total_scanned = 0
    total_yielded = 0
    skipped_by_id = 0
    skipped_by_hash = 0

    for file_path in pdf_files:
        filename = os.path.basename(file_path)
        base_name, _ = os.path.splitext(filename)

        try:
            file_hash = calculate_file_hash(file_path)
            full_hash = file_hash
            short_hash = file_hash[:8]
        except OSError as e:
            log.debug(f"Hash calculation failed for {file_path}: {e}")
            full_hash = None
            short_hash = str(os.path.getsize(file_path))

        local_metadata = _parse_local_pdf_metadata(
            file_path, base_name, fiscal_year_end_month
        )
        filing_id = f"local_{base_name}_{short_hash}"
        metadata = {
            "filingId": filing_id,
            "companyTicker": local_metadata["companyTicker"],
            "stockCode": local_metadata["stockCode"],
            "exchange": LOCAL_EXCHANGE,
            "filingType": local_metadata["filingType"],
            "stockName": local_metadata["stockName"],
            "title": base_name.replace("_", " ").replace("-", " ").title(),
            "filingDate": local_metadata["filingDate"],
            "sourceType": local_metadata["sourceType"],
            "reportingYear": local_metadata["reportingYear"],
            "referencedTickers": [],
            "metadataSources": {
                "companyTicker": "directory_scan_default",
                "exchange": "directory_scan_default",
                **local_metadata["metadataSources"],
            },
            "metadataConfidence": {
                "companyTicker": 0.0,
                "exchange": 0.0,
                **local_metadata["metadataConfidence"],
            },
        }
        total_scanned += 1
        if skip_existing and filing_id in existing_ids:
            log.debug(f"Skipping {file_path} (filingId exists)")
            skipped_by_id += 1
            continue
        if skip_by_hash and full_hash and full_hash in existing_hashes:
            log.debug(
                f"Skipping {file_path} (documentHash matches existing record; "
                "file may have been renamed)"
            )
            skipped_by_hash += 1
            continue
        total_yielded += 1
        yield file_path, metadata

    if skip_existing or skip_by_hash:
        log.info(
            f"Scan resume summary: {total_scanned} scanned, "
            f"{total_yielded} yielded, {skipped_by_id} skipped by ID, "
            f"{skipped_by_hash} skipped by hash"
        )


def scan_documents(
    dir_path: str,
    recursive: bool = True,
    fiscal_year_end_month: int = 12,
    skip_existing: bool = False,
    skip_by_hash: bool = False,
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """Scan local directories for PDF and HTML assets."""
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Target path is not a valid directory: {dir_path}")

    search_pattern = (
        os.path.join(dir_path, "**", "*") if recursive else os.path.join(dir_path, "*")
    )
    if skip_existing or skip_by_hash:
        from fin_pipeline.pipeline import _load_existing_filings

        existing_ids, existing_hashes = _load_existing_filings("local_")
        log.info(f"Resume check: {len(existing_ids)} local_* filings already in DB")
    else:
        existing_ids, existing_hashes = set(), set()

    total_scanned = 0
    total_yielded = 0
    skipped_by_id = 0
    skipped_by_hash = 0

    for file_path in glob.glob(search_pattern, recursive=recursive):
        if not os.path.isfile(file_path) or os.path.splitext(file_path)[
            1
        ].lower() not in {
            ".pdf",
            ".htm",
            ".html",
        }:
            continue

        filename = os.path.basename(file_path)
        base_name, _ = os.path.splitext(filename)
        try:
            file_hash = calculate_file_hash(file_path)
            full_hash = file_hash
            short_hash = file_hash[:8]
        except OSError as e:
            log.debug(f"Hash calculation failed for {file_path}: {e}")
            full_hash = None
            short_hash = str(os.path.getsize(file_path))

        local_metadata = _parse_local_pdf_metadata(
            file_path, base_name, fiscal_year_end_month
        )
        filing_id = f"local_{base_name}_{short_hash}"
        metadata = {
            "filingId": filing_id,
            "companyTicker": local_metadata["companyTicker"],
            "stockCode": local_metadata["stockCode"],
            "exchange": LOCAL_EXCHANGE,
            "filingType": local_metadata["filingType"],
            "stockName": local_metadata["stockName"],
            "title": base_name.replace("_", " ").replace("-", " ").title(),
            "filingDate": local_metadata["filingDate"],
            "sourceType": local_metadata["sourceType"],
            "reportingYear": local_metadata["reportingYear"],
            "referencedTickers": [],
            "metadataSources": {
                "companyTicker": "directory_scan_default",
                "exchange": "directory_scan_default",
                **local_metadata["metadataSources"],
            },
            "metadataConfidence": {
                "companyTicker": 0.0,
                "exchange": 0.0,
                **local_metadata["metadataConfidence"],
            },
        }
        total_scanned += 1
        if skip_existing and filing_id in existing_ids:
            log.debug(f"Skipping {file_path} (filingId exists)")
            skipped_by_id += 1
            continue
        if skip_by_hash and full_hash and full_hash in existing_hashes:
            log.debug(
                f"Skipping {file_path} (documentHash matches existing record; "
                "file may have been renamed)"
            )
            skipped_by_hash += 1
            continue
        total_yielded += 1
        yield file_path, metadata

    if skip_existing or skip_by_hash:
        log.info(
            f"Scan resume summary: {total_scanned} scanned, "
            f"{total_yielded} yielded, {skipped_by_id} skipped by ID, "
            f"{skipped_by_hash} skipped by hash"
        )


def scan_sec_edgar_html_directory(
    base_path: str,
    skip_existing: bool = False,
    skip_by_hash: bool = False,
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """Scan SEC Edgar HTML directory structure.

    Expected structure: {base_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html

    Args:
        base_path: Root directory containing sec-edgar-filings folder

    Yields:
        (file_path, metadata) tuples for each primary-document.html found
    """
    if not os.path.isdir(base_path):
        raise NotADirectoryError(f"Target path is not a valid directory: {base_path}")

    sec_edgar_root = os.path.join(base_path, "sec-edgar-filings")
    if not os.path.isdir(sec_edgar_root):
        # Try treating base_path as the direct root
        sec_edgar_root = base_path

    if skip_existing or skip_by_hash:
        from fin_pipeline.pipeline import _load_existing_filings

        existing_ids, existing_hashes = _load_existing_filings("sec_")
        log.info(f"Resume check: {len(existing_ids)} sec_* filings already in DB")
    else:
        existing_ids, existing_hashes = set(), set()

    total_scanned = 0
    total_yielded = 0
    skipped_by_id = 0
    skipped_by_hash = 0

    # Walk through: sec-edgar-filings/TICKER/FILING_TYPE/ACCESSION_NUMBER/
    for ticker_dir in os.listdir(sec_edgar_root):
        ticker_path = os.path.join(sec_edgar_root, ticker_dir)
        if not os.path.isdir(ticker_path) or ticker_dir.startswith("."):
            continue

        ticker = ticker_dir.upper()

        # Walk through filing types (10-K, 20-F, etc.)
        for filing_type_dir in os.listdir(ticker_path):
            filing_type_path = os.path.join(ticker_path, filing_type_dir)
            if not os.path.isdir(filing_type_path) or filing_type_dir.startswith("."):
                continue

            filing_type = filing_type_dir

            # Walk through accession numbers
            for accession_dir in os.listdir(filing_type_path):
                accession_path = os.path.join(filing_type_path, accession_dir)
                if not os.path.isdir(accession_path) or accession_dir.startswith("."):
                    continue

                accession_number = accession_dir

                # Look for primary-document.html
                html_file = os.path.join(accession_path, "primary-document.html")
                if os.path.isfile(html_file):
                    # Create filing ID from accession number
                    filing_id = f"sec_{ticker}_{accession_number}"
                    total_scanned += 1
                    if skip_existing and filing_id in existing_ids:
                        log.debug(f"Skipping {html_file} (filingId exists)")
                        skipped_by_id += 1
                        continue

                    if skip_by_hash:
                        try:
                            file_hash = calculate_file_hash(html_file)
                        except OSError as exc:
                            log.debug(f"Hash calculation failed for {html_file}: {exc}")
                            file_hash = None
                        if file_hash and file_hash in existing_hashes:
                            log.debug(
                                f"Skipping {html_file} (documentHash matches existing "
                                "record; file may have been renamed)"
                            )
                            skipped_by_hash += 1
                            continue

                    metadata = {
                        "filingId": filing_id,
                        "companyTicker": ticker,
                        "stockCode": DEFAULT_TICKER,
                        "exchange": DEFAULT_EXCHANGE,
                        "filingType": filing_type,
                        "title": f"{ticker} {filing_type}",
                        "filingDate": None,
                        "sourceType": (
                            "structured_filing"
                            if re.fullmatch(
                                r"(?i)(?:10-K|10-Q|20-F|40-F|8-K|S-1|S-3|S-4)",
                                filing_type,
                            )
                            else "unknown"
                        ),
                        "reportingYear": None,
                        "referencedTickers": [],
                        "metadataSources": {
                            "companyTicker": "directory_scan_sec",
                            "exchange": "directory_scan_sec",
                            "filingType": "directory_scan_sec",
                        },
                        "metadataConfidence": {
                            "companyTicker": 0.6,
                            "exchange": 0.6,
                            "filingType": 0.7,
                        },
                    }

                    total_yielded += 1
                    yield html_file, metadata

    if skip_existing or skip_by_hash:
        log.info(
            f"Scan resume summary: {total_scanned} scanned, "
            f"{total_yielded} yielded, {skipped_by_id} skipped by ID, "
            f"{skipped_by_hash} skipped by hash"
        )
