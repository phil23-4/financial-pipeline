import pdfplumber
import re
from typing import Dict, Any, Optional
from pypdf import PdfReader
from fin_pipeline.utils.ocr_engine import extract_text_via_ocr
from fin_pipeline.config.constants import EXCHANGE_MAPPING


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
    """Extract filing metadata using regex patterns from PDF text.
    
    Searches for common SEC filing patterns:
    - Filing types: 10-K, 10-Q, 8-K, S-1, etc.
    - Dates: "As of December 31, 2023" or similar patterns
    - Company name: Often in document header
    - CIK: 10-digit number appearing in cover page
    """
    metadata = {}
    
    # Filing type detection (10-K, 10-Q, 8-K, S-1, etc.)
    filing_type_pattern = r'\b(10-K|10-Q|10-Q/A|10-K/A|8-K|S-1|S-3|S-4|DEF 14A|PREM14A|SC 13G)\b'
    filing_match = re.search(filing_type_pattern, text, re.IGNORECASE)
    if filing_match:
        metadata['filing_type'] = filing_match.group(1).upper()
    
    # Date patterns: "As of March 31, 2023" or "December 31, 2023"
    date_pattern = r'(?:As of|Period Ending|Ended|For the (?:year|quarter) ended)\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})'
    date_match = re.search(date_pattern, text, re.IGNORECASE)
    if date_match:
        try:
            from datetime import datetime
            date_str = date_match.group(1).replace('\u00a0', ' ')
            date_str = re.sub(r'\s+', ' ', date_str).strip()
            date_str = re.sub(r'\s*,\s*', ', ', date_str)
            parsed = datetime.strptime(date_str, "%B %d, %Y")
            metadata['filing_date'] = parsed.strftime("%Y-%m-%d")
        except Exception:
            pass
    
    # CIK pattern: 10-digit number (usually on cover page)
    cik_pattern = r'\bCIK[:\s]+0*(\d{1,10})\b'
    cik_match = re.search(cik_pattern, text, re.IGNORECASE)
    if cik_match:
        metadata['cik'] = cik_match.group(1)
    
    # Company name: Often first substantial line or in document title
    # Try to find it near company name indicators
    company_pattern = r'(?:^|\n)\s*([A-Z][A-Za-z\s&\.,-]{5,80})\s+(?:Inc|Corp|LLC|Ltd|Company|Corporation)'
    company_match = re.search(company_pattern, text, re.MULTILINE)
    if company_match:
        company_name = company_match.group(1).strip()
        if len(company_name) > 3 and len(company_name) < 200:
            metadata['stock_name'] = company_name
    
    # Exchange detection based on content
    text_lower = text.lower()
    exchange = next(
        (normalized for name, normalized in EXCHANGE_MAPPING.items() if name in text_lower),
        None,
    )
    if exchange:
        metadata['exchange'] = exchange
    
    return metadata


def extract_filing_metadata(text: str, reader: Optional[PdfReader] = None) -> Dict[str, Any]:
    """Extract company and reporting-period metadata from PDF.
    
    Attempts multiple extraction strategies:
    1. PDF document properties (if available)
    2. Regex pattern matching on document text
    
    Returns a dict with keys matching HTML parser schema:
    - stockName: Company name
    - filingDate: Report period end date (YYYY-MM-DD format)
    - filingType: Form type (10-K, 10-Q, etc.)
    - exchange: Stock exchange (NYSE, NASDAQ, etc.)
    - cik: SEC Central Index Key
    
    Returns only fields that were successfully extracted.
    """
    metadata = {}
    
    # Try PDF properties first (most reliable if available)
    if reader:
        props = _extract_pdf_metadata_from_properties(reader)
        if props.get('title'):
            metadata['stock_name'] = props['title']
        if props.get('subject'):
            metadata['filing_type'] = props['subject']
        if props.get('keywords'):
            metadata['exchange'] = props['keywords']
    
    # Extract from text (covers both digital and OCR'd PDFs)
    text_metadata = _extract_filing_metadata_from_text(text)
    metadata.update(text_metadata)
    
    # Normalize keys to match HTML parser output
    result = {}
    if metadata.get('stock_name'):
        result['stockName'] = metadata['stock_name']
    if metadata.get('filing_date'):
        result['filingDate'] = metadata['filing_date']
    if metadata.get('filing_type'):
        result['filingType'] = metadata['filing_type']
    if metadata.get('exchange'):
        result['exchange'] = metadata['exchange']
    if metadata.get('cik'):
        result['cik'] = metadata['cik']
    
    return result

def parse_pdf_layout(file_path: str) -> dict:
    """Extract text, tables, and metadata from PDF with digital-native or OCR fallback.
    
    Attempts fast digital text extraction first. For scanned PDFs, falls back to 
    OCR processing via Tesseract. Automatically extracts filing metadata (company name,
    filing date, filing type, exchange, CIK) to match HTML parser behavior.
    
    Args:
        file_path: Absolute path to PDF file
        
    Returns:
        dict with keys:
            - text (str): Extracted text content from all pages
            - tables (list): List of extracted tables with structure metadata
            - table_cnt (int): Total number of tables extracted
            - reason (str): Extraction method used ('Digital Native' or 'OCR Scanner Fallback')
            - stockName (str, optional): Company name if extracted
            - filingDate (str, optional): Report period end date if extracted
            - filingType (str, optional): Form type if extracted
            - exchange (str, optional): Stock exchange if extracted
            - cik (str, optional): SEC Central Index Key if extracted
    """
    extracted_text = []
    extracted_tables = []
    table_cnt = 0

    reader = PdfReader(file_path)
    is_digital = any(page.extract_text().strip() for page in reader.pages[:2])

    if is_digital:
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text: extracted_text.append(text)
                
                for table in (page.extract_tables() or []):
                    if not table: continue
                    headers = [str(c).strip() for c in table if c]
                    rows = ["| " + " | ".join([str(cell).replace("\n", " ").strip() if cell else "" for cell in r]) + " |" for r in table]
                    rows.insert(1, "| " + " | ".join(["---"] * len(table)) + " |")
                    
                    extracted_tables.append({
                        "tableIndex": table_cnt,
                        "pageNumber": page_num,
                        "headers": headers,
                        "rowCount": len(table) - 1,
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
            **metadata
        }
    else:
        ocr_text = extract_text_via_ocr(file_path)
        metadata = extract_filing_metadata(ocr_text, reader)
        
        return {
            "text": ocr_text,
            "tables": [],
            "table_cnt": 0,
            "reason": "OCR Scanner Fallback",
            **metadata
        }
