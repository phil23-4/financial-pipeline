"""Constants for database tables, filing types, and other configuration values."""

import re
from datetime import datetime

# Database table names
FILING_TABLE = "exchange_filing"
COMPANY_TABLE_NAME = "company"
FILING_RELATION = "has_filing"
REFERENCE_RELATION = "references_filing"

# Default values for metadata
DEFAULT_TICKER = "UNKNOWN"
DEFAULT_EXCHANGE = "UNKNOWN"
DEFAULT_FILING_TYPE = "ANNUAL_REPORT"
LOCAL_EXCHANGE = "LOCAL_FS"

# Filing status values
STATUS_PROCESSED = "PROCESSED"
STATUS_FAILED = "FAILED"
STATUS_PENDING = "PENDING"

# Source types
SOURCE_LOCAL = "LOCAL"
SOURCE_SEC = "SEC"

# File types
FILE_TYPE_PDF = "pdf"
FILE_TYPE_HTML = "html"
FILE_TYPE_UNKNOWN = "unknown"

# Extraction methods
EXTRACTION_DIGITAL_NATIVE = "Digital Native"
EXTRACTION_OCR_FALLBACK = "OCR Scanner Fallback"

# Error message prefixes
ERROR_LAYOUT = "Layout Error"
ERROR_PARSING = "Parsing Error"
ERROR_VALIDATION = "Validation Error"
ERROR_DATABASE = "Database Error"

# Comprehensive global stock exchange name mappings
EXCHANGE_MAPPING = {
    # North America
    "new york stock exchange": "NYSE",
    "nyse": "NYSE",
    "nasdaq stock market": "NASDAQ",
    "nasdaq": "NASDAQ",
    "nyse american": "NYSE American",
    "toronto stock exchange": "TSX",
    "tsx": "TSX",
    "tsx venture exchange": "TSXV",
    "cboe Canada": "NEO",
    "bolsa mexicana de valores": "BMV",
    
    # Europe
    "london stock exchange": "LSE",
    "lse": "LSE",
    "euronext paris": "EURONEXT",
    "euronext amsterdam": "EURONEXT",
    "euronext brussels": "EURONEXT",
    "euronext lisbon": "EURONEXT",
    "euronext milan": "EURONEXT",
    "borsa italiana": "BIT",
    "frankfurt stock exchange": "FWB",
    "deutsche börse": "XETRA",
    "xetra": "XETRA",
    "six swiss exchange": "SIX",
    "bolsa de madrid": "BME",
    "nasdaq stockholm": "STO",
    "nasdaq copenhagen": "CPH",
    "nasdaq helsinki": "HEL",
    "oslo børs": "OSE",
    "oslo bors": "OSE",
    "vienna stock exchange": "WBAG",
    "warsaw stock exchange": "GPW",
    
    # Asia-Pacific
    "tokyo stock exchange": "TSE",
    "tse": "TSE",
    "hong kong stock exchange": "HKEX",
    "hkex": "HKEX",
    "shanghai stock exchange": "SSE",
    "shenzhen stock exchange": "SZSE",
    "singapore exchange": "SGX",
    "sgx": "SGX",
    "australian securities exchange": "ASX",
    "asx": "ASX",
    "korea exchange": "KRX",
    "krx": "KRX",
    "taiwan stock exchange": "TWSE",
    "national stock exchange of india": "NSE",
    "bse limited": "BSE",
    "bombay stock exchange": "BSE",
    "bursa malaysia": "MYX",
    "stock exchange of thailand": "SET",
    "indonesia stock exchange": "IDX",
    "new zealand exchange": "NZX",
    "philippine stock exchange": "PSE",

    # Latin America
    "b3": "B3",
    "brasil, bolsa, balcão": "B3",
    "bolsa de comercio de santiago": "BCS",
    "bolsa de valores de colombia": "BVC",
    "bolsa de valores de lima": "BVL",

    # Middle East & Africa
    "jse limited": "JSE",
    "johannesburg stock exchange": "JSE",
    "saudi exchange": "TADAWUL",
    "tadawul": "TADAWUL",
    "dubai financial market": "DFM",
    "abu dhabi securities exchange": "ADX",
    "tel aviv stock exchange": "TASE",
    "qatar stock exchange": "QSE",
}

# Pre-compiled Regex Patterns for Document Extraction
# 1. Expanded Filing Types (US SEC, Foreign Private Issuers, and Global)
FILING_TYPE_PATTERN = re.compile(
    r'\b('
    # Amended SEC filings must precede their base forms.
    r'10-K/A|10-K|10-Q/A|10-Q|8-K/A|8-K|20-F/A|20-F|40-F/A|40-F|'
    r'6-K|11-K|'
    # Registration & Proxy
    r'S-1|S-3|S-4|S-8|F-1|F-3|F-4|DEF 14A|PRE 14A|PX14A6G|ARS|'
    # Ownership & Funds
    r'13F-HR|13F|13G|13D|N-CSR|N-PORT|'
    # Global / Non-SEC
    r'Annual Report(?: and Accounts)?|Interim Report|'
    r'Half-Year(?:ly)? (?:Financial )?Report|Quarterly Report|'
    r'Financial Statements|Integrated Report|Sustainability Report|'
    r'ESG Report|Proxy Statement|Information Circular|'
    r'Management Discussion and Analysis|MD&A|Prospectus|'
    r'Offering Memorandum|Base Prospectus|Universal Registration Document|'
    r'Preliminary Results|Earnings Release'
    r')\b',
    re.IGNORECASE,
)

# 2. Expanded Identifiers (Handling prefixes like "Number", "Code", "No.", or "#" and spacing)
CIK_PATTERN = re.compile(r'\b(?:CIK|Central Index Key)(?:[\s#:]|No\.|Number)*0*(\d{1,10})\b', re.IGNORECASE)
LEI_PATTERN = re.compile(r'\bLEI(?:[\s#:]|Code|Number)*([0-9A-Z]{20})\b', re.IGNORECASE)
ISIN_PATTERN = re.compile(r'\bISIN(?:[\s#:]|Code|Number)*([A-Z]{2}[A-Z0-9]{9}\d)\b', re.IGNORECASE)
CUSIP_PATTERN = re.compile(r'\bCUSIP(?:[\s#:]|Code|Number)*([0-9A-Z]{9})\b', re.IGNORECASE)
SEDOL_PATTERN = re.compile(r'\bSEDOL(?:[\s#:]|Code|Number)*([0-9A-Z]{7})\b', re.IGNORECASE)

# 3. Exhaustive Global Company Entity Suffixes
COMPANY_PATTERN = re.compile(
    # Strict capital first letter, allows alphanumeric, spaces, and common name punctuation
    r'(?:^|\n)\s*([A-Z][A-Za-z0-9\s&\.,\'-]{4,100})\s+'
    # Case-insensitive inline flag for the legal suffix
    r'(?i:'
    # North America
    r'Inc\.?|Incorporated|Corp\.?|Corporation|LLC|L\.L\.C\.|LP|L\.P\.|LLP|'
    # UK, Ireland & Commonwealth
    r'Ltd\.?|Limited|PLC|P\.L\.C\.|Public Limited Company|DAC|'
    # DACH (Germany, Austria, Switzerland)
    r'AG|A\.G\.|GmbH(?:\s*&\s*Co\.?\s*KG)?|KGaA|'
    # France, Spain, Latin America
    r'SA|S\.A\.|SAS|S\.A\.S\.|SL|S\.L\.|SAB\s+de\s+CV|S\.A\.B\.\s+de\s+C\.V\.|'
    # Italy
    r'SpA|S\.p\.A\.|Srl|S\.r\.l\.|'
    # Benelux
    r'NV|N\.V\.|BV|B\.V\.|'
    # Nordics
    r'AB|A/S|ASA|Oyj|ApS|'
    # Asia-Pacific
    r'Pte\.?\s+Ltd\.?|Bhd\.?|Tbk|K\.K\.|'
    # General Corporate Groupings
    r'SE|Societas Europaea|Group|Holdings|Holding|Trust|Fund'
    r')\b',
    re.MULTILINE
)

# 4. Exhaustive Date Pattern Matching
DATE_PATTERN = re.compile(
    r'(?:As of|Period Ending|Ended|For the (?:year|quarter|period|half-year) ended|Year ended|Date of Report)\s*'
    r'(?:on\s*)?'
    r'('
    # Pattern A: "31st December 2023", "December 31, 2023", "31 Dec. 2023"
    r'\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z\.]{3,9}\s+,?\s*\d{4}|'
    r'[A-Za-z\.]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}|'
    # Pattern B: ISO Formats ("2023-12-31", "2023/12/31", "31/12/2023")
    r'\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}[-/]\d{2}[-/]\d{4}'
    r')',
    re.IGNORECASE
)

# Comprehensive Date Format Strings for datetime.strptime
DATE_FORMATS = (
    # Standard US / Euro Full Month
    "%B %d, %Y", "%d %B %Y", "%B %d %Y", "%d %B, %Y",
    # Abbreviated Month (Jan / Dec)
    "%b %d, %Y", "%d %b %Y", "%b %d %Y", "%d %b, %Y",
    # ISO & Slash Standard Formats
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
)

def parse_extracted_date_str(raw_date_str: str) -> str | None:
    """Cleans ordinal indicators and normalizes date strings into YYYY-MM-DD format."""
    # Strip periods (e.g., "Dec." -> "Dec"), non-breaking spaces, and extra whitespace
    cleaned = raw_date_str.replace('\u00a0', ' ').replace('.', '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Strip ordinal suffixes ("31st" -> "31", "2nd" -> "2")
    cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*,\s*', ', ', cleaned)

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    return None