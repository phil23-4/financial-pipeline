"""Constants for database tables, filing types, and other configuration values."""

# Database table names
FILING_TABLE = "exchange_filing"
COMPANY_TABLE_NAME = "company"
FILING_RELATION = "has_filing"
REFERENCE_RELATION = "references_filing"

# Default values for metadata
DEFAULT_TICKER = "UNKNOWN"
DEFAULT_EXCHANGE = "UNKNOWN"
DEFAULT_FILING_TYPE = "PRIVATE_REPORT"
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

# Exchange name mappings
EXCHANGE_MAPPING = {
    "new york stock exchange": "NYSE",
    "nasdaq stock market": "NASDAQ",
    "nasdaq": "NASDAQ",
    "london stock exchange": "LSE",
    "hong kong stock exchange": "HKEX",
    "toronto stock exchange": "TSX",
}
