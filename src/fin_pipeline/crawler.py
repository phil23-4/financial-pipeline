import glob
import os
import re
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


def _parse_local_pdf_metadata(file_path: str, base_name: str) -> dict[str, Any]:
    """Parse identity and report details from the supported local PDF layout."""
    metadata = {
        "stockName": None,
        "stockCode": DEFAULT_TICKER,
        "filingType": DEFAULT_FILING_TYPE,
        "filingDate": None,
        "metadataSources": {},
        "metadataConfidence": {},
    }

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
                "filingDate": f"{values['year']}-12-31",
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

    company_path = os.path.dirname(file_path)
    company_name = os.path.basename(company_path).replace("_", " ").strip()
    if company_name:
        metadata["stockName"] = company_name
        metadata["metadataSources"]["stockName"] = "parent_directory"
        metadata["metadataConfidence"]["stockName"] = 0.95

    return metadata


def scan_directory(
    dir_path: str, recursive: bool = True
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

    for file_path in pdf_files:
        filename = os.path.basename(file_path)
        base_name, _ = os.path.splitext(filename)

        try:
            file_hash = calculate_file_hash(file_path)
            short_hash = file_hash[:8]
        except OSError as e:
            log.debug(f"Hash calculation failed for {file_path}: {e}")
            short_hash = str(os.path.getsize(file_path))

        local_metadata = _parse_local_pdf_metadata(file_path, base_name)
        metadata = {
            "filingId": f"local_{base_name}_{short_hash}",
            "companyTicker": DEFAULT_TICKER,
            "stockCode": local_metadata["stockCode"],
            "exchange": LOCAL_EXCHANGE,
            "filingType": local_metadata["filingType"],
            "stockName": local_metadata["stockName"],
            "title": base_name.replace("_", " ").replace("-", " ").title(),
            "filingDate": local_metadata["filingDate"],
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
        yield file_path, metadata


def scan_sec_edgar_html_directory(
    base_path: str,
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

                    metadata = {
                        "filingId": filing_id,
                        "companyTicker": ticker,
                        "stockCode": DEFAULT_TICKER,
                        "exchange": DEFAULT_EXCHANGE,
                        "filingType": filing_type,
                        "title": f"{ticker} {filing_type}",
                        "filingDate": None,
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

                    yield html_file, metadata
