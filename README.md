# Financial Pipeline Extractor (`fin_pipeline`)

`fin_pipeline` is a Python ingestion pipeline for financial filings. It reads local PDFs and HTML filings, extracts text and tables, validates the output, and persists structured filing records and graph relationships into SurrealDB.

The current implementation supports:

- PDF ingestion with PyMuPDF4LLM Markdown extraction and OCR fallback when native extraction fails
- HTML/XBRL extraction for SEC-style filings
- Filing metadata extraction for company name, filing date, form type, exchange, and CIK
- Camelot table extraction into structured Markdown records with page, row, header, and accuracy metadata
- Graph creation for company and filing relationships in SurrealDB
- Retry-aware database calls, connection pooling, and stale-record cleanup

## Project purpose

This package is designed for financial-document processing pipelines that need to:

- ingest SEC-style filings and private PDF reports
- normalize extracted text and tables into a consistent schema
- maintain a graph of company and filing relationships in SurrealDB
- support local ingestion workflows and SEC filing retrieval workflows

## Current architecture

```text
financial-pipeline/
├── pyproject.toml
├── README.md
├── src/
│   └── fin_pipeline/
│       ├── __init__.py
│       ├── cli.py
│       ├── crawler.py
│       ├── pipeline.py
│       ├── sec_edgar.py
│       ├── config/
│       │   ├── __init__.py
│       │   ├── constants.py
│       │   ├── logger.py
│       │   ├── settings.py
│       │   └── structured_logging.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── connection.py
│       │   ├── connection_pool.py
│       │   ├── db.py
│       │   └── relations.py
│       ├── models/
│       │   ├── __init__.py
│       │   └── schemas.py
│       └── utils/
│           ├── __init__.py
│           ├── crypto.py
│           ├── db_utils.py
│           ├── html_parser.py
│           ├── ocr_engine.py
│           ├── pdf_parser.py
│           └── retry.py
├── tests/
│   ├── conftest.py
│   ├── test_crawler.py
│   ├── test_pipeline.py
│   ├── test_pdf_parser.py
│   ├── test_sec_edgar.py
│   └── __init__.py
└── .env.example (if present in your local checkout)
```

## Key capabilities

- **PDF ingestion**: `parse_pdf_layout()` uses `pymupdf4llm` to preserve headings, emphasis, lists, and document structure in Markdown
- **PDF table extraction**: `camelot-py` uses lattice extraction first and stream extraction as a fallback, producing normalized Markdown tables and string-only headers
- **OCR fallback**: `extract_text_via_ocr()` processes PDFs when PyMuPDF4LLM cannot extract native text
- **HTML/XBRL parsing**: `parse_html_file()` and related helpers parse document text and Inline XBRL metadata via BeautifulSoup
- **Metadata normalization**: common fields are normalized across PDF and HTML sources:
  - `stockName`
  - `filingDate`
  - `filingType`
  - `exchange`
  - `cik`
- **Folder-aware local scanning**: structured local PDF paths and filenames provide company, stock code, report type, and reporting year metadata before document parsing
- **Markdown cleanup**: repeated page headers and footers are removed, bullets are normalized, and spacing is consolidated
- **Graph persistence**: ingested records can create company and filing entities and relationships in SurrealDB
- **Pipeline resilience**: repeated network/database calls use retry logic; pooled connections are supported; stale records are cleaned before overwrite
- **Structured logging**: `loguru` writes human-readable terminal output and JSON log files

## Directory behavior and ingestion flow

The processing pipeline is organized as follows:

1. Detect input type
   - PDF or HTML
2. Parse document content
   - PDF text/tables and metadata
   - HTML text/tables and Inline XBRL metadata
3. Validate payload
   - Pydantic schema validation in `models/schemas.py`
4. Persist filing record
   - SurrealDB insert/upsert logic
5. Create graph relations
   - `has_filing` and `references_filing` edges
6. Clean stale or partial data
   - avoids leaving incomplete prior versions behind

## Metadata extraction behavior

The pipeline currently tries to extract metadata from both HTML and PDF input in a consistent way.

### HTML metadata

The HTML parser reads common Inline XBRL values such as:

- `EntityRegistrantName`
- `DocumentPeriodEndDate`
- `DocumentType`
- `EntityCentralIndexKey`

The extraction is performed by `extract_filing_metadata()` in `src/fin_pipeline/utils/html_parser.py`.

### PDF text and table extraction

PDF text is extracted with `pymupdf4llm.to_markdown(..., page_chunks=True)` so page content can retain Markdown structure such as headings, bold text, and lists. Post-processing removes repeated page furniture, normalizes bullet characters, and fixes excess whitespace.

Tables are extracted with Camelot using the lattice flavor first and the stream flavor when lattice extraction is unavailable. Each table includes normalized `headers`, `rowCount`, `pageNumber`, `accuracy`, and Markdown content. Table cells are whitespace-normalized and escaped for safe Markdown rendering.

If native Markdown extraction fails, the parser falls back to the concurrent PyMuPDF-rendered Tesseract OCR pipeline in `extract_text_via_ocr()`.

### PDF metadata

The PDF parser tries a two-step approach:

1. embedded PDF properties (`reader.metadata`)
2. regex-based extraction from the cleaned Markdown text

This includes detection for:

- company names near "Inc", "Corp", "LLC", etc.
- filing types such as `10-K`, `10-Q`, `8-K`, `S-1`, `S-3`, `S-4`
- dates like `As of December 31, 2025`
- CIK values in text such as `CIK: 0000320193`
- exchange names via the configured exchange mapping

The PDF parser normalizes dates to `YYYY-MM-DD` before validation.

### Caller precedence rules

Metadata supplied by the caller remains authoritative. The pipeline only fills missing values from parsed output when the provided value is empty, null, or `UNKNOWN`.

This allows local extraction to act as a recovery path without overwriting trusted upstream metadata.

### Local PDF naming convention

The local scanner recognizes paths such as:

```text
Filings Data/
└── romania/
   └── Banca Transilvania/
      └── 131662.ar.en.2018.pdf
```

From this layout it populates:

| Metadata field | Source                              | Example              |
| -------------- | ----------------------------------- | -------------------- |
| `stockName`    | Immediate parent directory          | `Banca Transilvania` |
| `stockCode`    | Filename prefix                     | `131662`             |
| `filingType`   | Filename report code                | `ANNUAL_REPORT`      |
| `filingDate`   | Filename year, inferred as year-end | `2018-12-31`         |
| `exchange`     | Local source default                | `LOCAL_FS`           |

The supported filename pattern is:

```text
{stock_code}.{ar|annual_report}.{language}.{year}.pdf
```

Ticker and exchange cannot be inferred reliably from this layout, so they remain `UNKNOWN` and `LOCAL_FS` respectively unless supplied through another metadata source. Files that do not match the pattern retain the scanner defaults.

## Requirements

### System dependencies

The project expects OCR-related system packages to be installed on the host machine.

For Ubuntu/Debian:

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr poppler-utils
```

For macOS with Homebrew:

```bash
brew install tesseract poppler
```

### Python requirements

Python 3.11+ is required. PDF processing uses:

- `pymupdf4llm` for structured Markdown text extraction
- `camelot-py` for table extraction
- `pypdf` for PDF metadata
- `pytesseract`, PyMuPDF, Pillow, and `pdf2image` for OCR fallback

## Installation

Install the package in the usual way:

```bash
pip install .
```

Development installation:

```bash
pip install -e ".[dev]"
```

Optional SEC enrichment helpers:

```bash
pip install -e ".[sec]"
```

## Environment configuration

The project reads a mix of environment variables and `.env` values via `src/fin_pipeline/config/settings.py`.

| Variable                 | Purpose                                                  | Typical value                      |
| ------------------------ | -------------------------------------------------------- | ---------------------------------- |
| `SURREAL_ENDPOINT`       | SurrealDB HTTP endpoint                                  | `http://127.0.0.1:8000`            |
| `SURREAL_USER`           | SurrealDB username                                       | `root`                             |
| `SURREAL_PASS`           | SurrealDB password                                       | `secret`                           |
| `SURREAL_NAMESPACE`      | SurrealDB namespace                                      | `finance`                          |
| `SURREAL_DATABASE`       | SurrealDB database                                       | `analytics`                        |
| `COMPANY_TABLE`          | Company table name used by the pipeline                  | `company`                          |
| `EDGAR_IDENTITY`         | SEC User-Agent string for optional edgartools enrichment | `Your Name your.email@example.com` |
| `FIN_PIPELINE_LOG_LEVEL` | Loguru log level                                         | `INFO`                             |
| `FIN_PIPELINE_LOG_DIR`   | Directory for JSON logs                                  | `logs`                             |

Example `.env`:

```dotenv
SURREAL_ENDPOINT=http://127.0.0.1:8000
SURREAL_USER=root
SURREAL_PASS=secret
SURREAL_NAMESPACE=finance
SURREAL_DATABASE=analytics
COMPANY_TABLE=company
EDGAR_IDENTITY="Your Name your.email@example.com"
FIN_PIPELINE_LOG_LEVEL=INFO
FIN_PIPELINE_LOG_DIR=logs
```

## CLI usage

The package exposes the `fin-pipeline` command via the entry point in `pyproject.toml`.

### 1. Scan a local folder of filings

```bash
fin-pipeline scan /path/to/financial_docs --source LOCAL --concurrency 4
```

This recursively scans a directory, detects file types, and ingests supported documents.

### 2. Process SEC Edgar HTML filing directories

```bash
fin-pipeline sec-edgar-html /path/to/sec_filings --concurrency 4
```

This expects a structure similar to:

```text
sec_filings/
└── sec-edgar-filings/
    ├── AAPL/
    │   └── 10-K/
    │       └── 0000320193-25-000079/
    │           └── primary-document.html
    └── MSFT/
        └── 10-K/
            └── 0000789019-25-000091/
                └── primary-document.html
```

### 3. Process a CSV of ticker or CIK references

```bash
fin-pipeline sec-edgar-csv ./companies.csv --download-dir ./sec_downloads
```

The CSV can include `ticker` and/or `cik`, and optional `forms`, `year`, and `max_filings` columns.

### 4. Stream SEC filings from CSV without writing HTML to disk

```bash
fin-pipeline sec-edgar-stream ./companies.csv --year-range 2018-2025 --forms 10-K,10-Q
```

### 5. Ingest a single explicit file

```bash
fin-pipeline file ./downloads/apple_10k.pdf \
  --filing-id "sec_0000320193_2026_10K" \
  --ticker "AAPL" \
   --stock-name "Apple Inc." \
  --stock-code "320193" \
  --exchange "NASDAQ" \
  --type "10-K" \
  --source SEC
```

When the legal company name is known from the filing source, pass it with `--stock-name`. This value is treated as authoritative; PDF extraction is used only when the field is omitted.

### 6. Ingest a single HTML filing with metadata

```bash
fin-pipeline file ./downloads/filing.html \
  --filing-id "sec_manual_filing" \
  --ticker "AAPL" \
  --type "10-K"
```

## Python API usage

```python
import asyncio
from fin_pipeline.pipeline import run_ingestion_pipeline, process_entire_directory

async def main():
    custom_metadata = {
        "filingId": "manual_ingest_tsmc_01",
        "companyTicker": "TSM",
        "stockCode": "2330",
        "exchange": "NYSE",
        "filingType": "20-F",
        "referencedTickers": ["AAPL", "NVDA", "ASML"],
    }

    await run_ingestion_pipeline(custom_metadata, "./tsmc_annual.pdf", source="SEC")

    await process_entire_directory(
        "/shared/network/pdf_drop",
        source_type="LOCAL",
        concurrency_limit=2,
    )

asyncio.run(main())
```

## Data model and SurrealDB schema

The ingestion process maps data into a SurrealDB schema that includes a company node table and a filing table, plus graph relations.

Example schema concepts:

```surrealql
DEFINE TABLE company SCHEMAFULL;
DEFINE FIELD ticker ON TABLE company TYPE string UNIQUE;
DEFINE FIELD companyName ON TABLE company TYPE option<string>;
DEFINE FIELD exchange ON TABLE company TYPE option<string>;
DEFINE FIELD updatedAt ON TABLE company TYPE option<datetime>;

DEFINE TABLE exchange_filing SCHEMAFULL;
DEFINE FIELD filingId ON TABLE exchange_filing TYPE string;
DEFINE FIELD companyTicker ON TABLE exchange_filing TYPE string;
DEFINE FIELD stockName ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD filingType ON TABLE exchange_filing TYPE string;
DEFINE FIELD documentText ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD documentTables ON TABLE exchange_filing TYPE option<array<object>>;
DEFINE FIELD documentTables[*].accuracy ON TABLE exchange_filing TYPE option<float>;
DEFINE FIELD referencedTickers ON TABLE exchange_filing TYPE option<array<string>>;
DEFINE FIELD documentStatus ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD updatedAt ON TABLE exchange_filing TYPE datetime;

DEFINE TABLE has_filing SCHEMAFULL TYPE RELATION IN company OUT exchange_filing;
DEFINE TABLE references_filing SCHEMAFULL TYPE RELATION IN exchange_filing OUT company;
```

The database side is managed in:

- `src/fin_pipeline/db/db.py`
- `src/fin_pipeline/db/connection.py`
- `src/fin_pipeline/db/connection_pool.py`
- `src/fin_pipeline/db/relations.py`

## Validation and error handling

The pipeline applies a validation layer before writing to SurrealDB.

Current protections include:

- strict Pydantic validation via `ExchangeFilingModel`
- stale-record cleanup before each overwrite
- database retry and backoff logic
- graph relation cleanup so duplicate edges are not created endlessly
- explicit, targeted exception handling for database, HTML, PDF, and OCR failures
- OCR page failures that do not crash the entire document process

## Testing

The project test suite uses `pytest` to validate ingestion logic and parser behavior without writing live records to a production database.

Run the suite with:

```bash
pytest -v
```

or the shorter form:

```bash
pytest -q
```

Current tests cover:

- crawler behavior and directory scanning
- folder-aware local PDF metadata parsing
- ingestion pipeline success paths
- metadata extraction from HTML
- PDF table extraction and OCR failure handling
- SEC metadata/date handling edge cases
- SurrealDB connection helper behavior
- database upsert and delete logic

## Recent implementation notes

This project already includes several recent improvements:

- automatic metadata extraction for PDFs as well as HTML
- PyMuPDF4LLM-based structured Markdown extraction for PDF content
- Camelot-based table extraction with normalized headers and parsing accuracy
- folder-aware local scanning for structured PDF paths and filenames
- authoritative `--stock-name` support for explicit file ingestion
- table accuracy persistence through Pydantic and SurrealDB schemas
- retry-aware database access with exponential backoff
- HTTP connection pooling support for SurrealDB
- stricter exception handling in OCR and parse workflows
- centralized shared DB ID quoting utility
- updated logging and serialization behaviors
- improved handling for SEC filing edge cases and partial writes

## Support and extension points

The codebase is structured to be extended in a few typical ways:

- add richer PDF table classification or normalization layers
- add semantic text chunking for narrative analysis
- add more robust SEC company metadata enrichment
- extend graph relations beyond company filing references

## Summary

`fin_pipeline` provides a practical ingestion pipeline for financial document processing with a current focus on:

- local and SEC filing ingestion
- document text and table extraction
- metadata normalization across PDF and HTML formats
- SurrealDB persistence and company graph relationships
- production-oriented operational safeguards
