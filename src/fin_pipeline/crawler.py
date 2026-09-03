import os
import glob
from typing import Generator, Tuple, Dict, Any
from fin_pipeline.utils.crypto import calculate_file_hash
from fin_pipeline.config.constants import (
    DEFAULT_TICKER,
    DEFAULT_EXCHANGE,
    DEFAULT_FILING_TYPE,
    LOCAL_EXCHANGE,
)
from loguru import logger as log


def scan_directory(
    dir_path: str, recursive: bool = True
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
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
        except (IOError, OSError) as e:
            log.debug(f"Hash calculation failed for {file_path}: {e}")
            short_hash = str(os.path.getsize(file_path))

        metadata = {
            "filingId": f"local_{base_name}_{short_hash}",
            "companyTicker": DEFAULT_TICKER,
            "stockCode": DEFAULT_TICKER,
            "exchange": LOCAL_EXCHANGE,
            "filingType": DEFAULT_FILING_TYPE,
            "title": base_name.replace("_", " ").replace("-", " ").title(),
            "filingDate": None,
            "referencedTickers": [],
            "metadataSources": {
                "companyTicker": "directory_scan_default",
                "exchange": "directory_scan_default",
                "filingType": "directory_scan_default",
            },
            "metadataConfidence": {
                "companyTicker": 0.0,
                "exchange": 0.0,
                "filingType": 0.0,
            },
        }
        yield file_path, metadata


def scan_sec_edgar_html_directory(
    base_path: str,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
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
