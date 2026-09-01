import os
import glob
from typing import Generator, Tuple, Dict, Any
from fin_pipeline.utils.crypto import calculate_file_hash

def scan_directory(dir_path: str, recursive: bool = True) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """Scans local directories for financial PDF assets and prepares baseline meta attributes."""
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"Target path is not a valid directory: {dir_path}")

    search_pattern = os.path.join(dir_path, "**", "*.pdf") if recursive else os.path.join(dir_path, "*.pdf")
    pdf_files = glob.glob(search_pattern, recursive=recursive)

    for file_path in pdf_files:
        filename = os.path.basename(file_path)
        base_name, _ = os.path.splitext(filename)
        
        try:
            file_hash = calculate_file_hash(file_path)
            short_hash = file_hash[:8]
        except Exception:
            short_hash = str(os.path.getsize(file_path))

        metadata = {
            "filingId": f"local_{base_name}_{short_hash}",
            "companyTicker": "UNKNOWN",
            "stockCode": "UNKNOWN",
            "exchange": "LOCAL_FS",
            "filingType": "PRIVATE_REPORT",
            "title": base_name.replace("_", " ").replace("-", " ").title(),
            "filingDate": None, 
            "referencedTickers": []
        }
        yield file_path, metadata
