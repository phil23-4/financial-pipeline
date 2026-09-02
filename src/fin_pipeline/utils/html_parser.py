"""HTML text and table extraction for SEC Edgar filings."""

import os
from bs4 import BeautifulSoup
from typing import Dict, List, Any
import re
from fin_pipeline.config.constants import EXCHANGE_MAPPING


def _ixbrl_value(soup: BeautifulSoup, field_name: str) -> str | None:
    """Read a standard Inline XBRL metadata field by its local name."""
    field_name = field_name.lower()
    for tag in soup.find_all(attrs={"name": True}):
        name = tag.get("name", "").lower()
        if name.rsplit(":", 1)[-1] == field_name:
            value = tag.get_text(" ", strip=True)
            if value:
                return value
    return None


def extract_filing_metadata(html_content: str) -> Dict[str, Any]:
    """Extract company and reporting-period metadata from Inline XBRL HTML."""
    soup = BeautifulSoup(html_content, 'html.parser')
    company_name = _ixbrl_value(soup, "EntityRegistrantName")
    filing_date = _ixbrl_value(soup, "DocumentPeriodEndDate")
    filing_type = _ixbrl_value(soup, "DocumentType")
    cik = _ixbrl_value(soup, "EntityCentralIndexKey")
    visible_text = soup.get_text(" ", strip=True).lower()
    exchange = next(
        (normalized for name, normalized in EXCHANGE_MAPPING.items() if name in visible_text),
        None,
    )

    if filing_date:
        parsed_date = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", filing_date)
        if parsed_date:
            filing_date = "-".join(parsed_date.groups())
        else:
            parsed_date = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s*,?\s+(\d{4})", filing_date, re.I)
            if parsed_date:
                from datetime import datetime
                normalized_date = parsed_date.group(0).replace("\u00a0", " ")
                normalized_date = re.sub(r"\s+", " ", normalized_date).strip()
                normalized_date = re.sub(r"\s*,\s*", ", ", normalized_date)
                filing_date = datetime.strptime(normalized_date, "%B %d, %Y").strftime("%Y-%m-%d")

    return {
        "stockName": company_name,
        "filingDate": filing_date,
        "filingType": filing_type,
        "exchange": exchange,
        "cik": cik,
    }


def enrich_filing_metadata_with_edgartools(
    metadata: Dict[str, Any], accession_number: str
) -> Dict[str, Any]:
    """Best-effort SEC metadata enrichment using edgartools.

    The local parser remains authoritative for document text and tables. This
    helper only fills missing metadata and returns unchanged data when the
    optional dependency, identity, or SEC network is unavailable.
    """
    import os
    from fin_pipeline.config.settings import EDGAR_IDENTITY

    if not (os.getenv("EDGAR_IDENTITY") or EDGAR_IDENTITY) or not metadata.get("cik"):
        return metadata

    try:
        from edgar import Company

        filings = Company(metadata["cik"]).get_filings(
            form=metadata.get("filingType"), accession_number=accession_number
        )
        if not filings:
            return metadata
        filing = filings[0]
        enriched = dict(metadata)
        for key, attributes in {
            "stockName": ("company_name", "companyName"),
            "filingDate": ("period_of_report", "periodOfReport"),
            "filingType": ("form",),
            "exchange": ("exchange",),
        }.items():
            if enriched.get(key) in (None, "", "UNKNOWN"):
                for attribute in attributes:
                    value = getattr(filing, attribute, None)
                    if value:
                        enriched[key] = value.isoformat() if hasattr(value, "isoformat") else str(value)
                        break
        return enriched
    except Exception:
        return metadata

def extract_text_from_html(html_content: str) -> str:
    """Extract plain text from HTML content, removing scripts and styles."""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()
    
    # Get text
    text = soup.get_text()
    
    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)
    
    return text

def extract_tables_from_html(html_content: str) -> tuple:
    """Extract HTML tables and convert to markdown format.
    
    Returns:
        tuple: (tables_list, table_count, extraction_reason)
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    
    extracted_tables = []
    for table_idx, table in enumerate(tables):
        try:
            rows = table.find_all('tr')
            if not rows:
                continue
            
            # Extract headers
            headers = []
            first_row_cells = rows[0].find_all(['th', 'td'])
            for cell in first_row_cells:
                headers.append(cell.get_text(strip=True))
            
            # Extract data rows
            markdown_lines = []
            if headers:
                markdown_lines.append('| ' + ' | '.join(headers) + ' |')
                markdown_lines.append('|' + ''.join(['------|' for _ in headers]))
            
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                if cells:
                    row_data = [cell.get_text(strip=True) for cell in cells]
                    markdown_lines.append('| ' + ' | '.join(row_data) + ' |')
            
            markdown_table = '\n'.join(markdown_lines)
            
            extracted_tables.append({
                "tableIndex": table_idx,
                "sheetName": None,
                "pageNumber": None,
                "headers": headers,
                "rowCount": len(rows) - 1,
                "markdown": markdown_table
            })
        except Exception as e:
            pass
    
    return extracted_tables, len(extracted_tables), "html_beautifulsoup"


def parse_html_content(html_content: str) -> Dict[str, Any]:
    """Parse HTML already held in memory without creating a local file."""
    text = extract_text_from_html(html_content)
    tables, table_cnt, reason = extract_tables_from_html(html_content)
    return {
        "text": text,
        "tables": tables,
        "table_cnt": table_cnt,
        "reason": reason,
        **extract_filing_metadata(html_content),
    }

def parse_html_file(file_path: str) -> Dict[str, Any]:
    """Parse an HTML SEC Edgar filing and extract text and tables.
    
    Args:
        file_path: Path to HTML file
    
    Returns:
        Dictionary with extracted content and metadata
    """
    if not os.path.exists(file_path):
        return {
            "text": "",
            "tables": [],
            "table_cnt": 0,
            "reason": "File not found"
        }
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except UnicodeDecodeError:
        # Try alternative encoding
        with open(file_path, 'r', encoding='latin-1') as f:
            html_content = f.read()
    except Exception as e:
        return {
            "text": "",
            "tables": [],
            "table_cnt": 0,
            "reason": f"Read error: {str(e)}"
        }
    
    try:
        return parse_html_content(html_content)
    except Exception as e:
        return {"text": "", "tables": [], "table_cnt": 0, "reason": f"Parse error: {str(e)}"}
