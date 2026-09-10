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
    """Parse identity and report details from a local PDF path and filename."""
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
            metadata["metadataSources"]["filingType"] = "filename_report_type"
            metadata["metadataConfidence"]["filingType"] = 0.75

            prefix = normalized_name[: report_match.start()].strip(" -:()")
            prefix = re.sub(r"(?i)\b(?:fy|fiscal year)\s*$", "", prefix).strip()
            code_match = re.fullmatch(
                r"(?i)(?P<code>[a-z0-9][a-z0-9./]{1,20})", prefix
            )
            if code_match and not re.fullmatch(r"(?:19|20)\d{2}", prefix):
                metadata["stockCode"] = code_match.group("code")
                metadata["metadataSources"]["stockCode"] = "filename_code"
                metadata["metadataConfidence"]["stockCode"] = 0.75
            elif prefix:
                metadata["filenameCompanyName"] = prefix

        if year_match:
            metadata["filingDate"] = f"{year_match.group(0)}-12-31"
            metadata["metadataSources"]["filingDate"] = "filename_year_inferred"
            metadata["metadataConfidence"]["filingDate"] = 0.55

    company_path = os.path.dirname(file_path)
    company_name = os.path.basename(company_path).replace("_", " ").strip()
    if metadata.get("filenameCompanyName"):
        metadata["stockName"] = metadata["filenameCompanyName"]
        metadata["metadataSources"]["stockName"] = "filename_company_name"
        metadata["metadataConfidence"]["stockName"] = 0.65
    elif company_name and company_name != ".":
        metadata["stockName"] = company_name
        metadata["metadataSources"]["stockName"] = "parent_directory"
        metadata["metadataConfidence"]["stockName"] = 0.95

    metadata.pop("filenameCompanyName", None)

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
