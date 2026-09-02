# Financial Pipeline Extractor (`fin_pipeline`)

A production-grade, highly scalable modular Python package designed to extract metadata, text, and tabular structures from financial statements (**SEC 10-K, 20-F, 40-F**) and **local private entity PDFs**. Validated data is transformed into type-safe objects and ingested into an active **SurrealDB** graph database instances using strict `SCHEMAFULL` relational constraints.

## 🌟 Key Features

- **Dual Input Format Support**: Process both **PDF** (digital and scanned) and **HTML** document formats seamlessly
- **Dual Extraction Strategy**: High-speed digital-native text extraction with automatic `pytesseract` OCR fallbacks for scanned images or faxed financial reports.
- **Metadata Extraction**: Extracts company name, reporting date, filing form, exchange, and CIK from Inline XBRL HTML, PDF properties, and PDF text patterns.
- **Table-to-Markdown Engine**: Parses grid geometry and structural visual layouts into standardized Markdown strings while tracking page indexes and row weights.
- **SEC Edgar Directory Crawling**: Automatically discover and process HTML filings from local SEC Edgar directory structures with metadata extraction for company name, filing date, filing type, exchange, and document format.
- **Auto-Company Seeding**: Automatically creates company records when first referenced by filing metadata—no manual database bootstrapping required.
- **SurrealDB Graph Relate Automation**: Automatically calculates cryptographic file signatures and creates type-safe graph edges (`has_filing`, `references_filing`) with seamless company linking.
- **Stale Record Cleanup**: Intelligently removes incomplete partial records from earlier failed writes before each fresh ingestion to prevent data inconsistency.
- **Enterprise-Grade Log Management**: Asynchronous structured logs powered by `loguru`, delivering terminal views alongside single-line JSON log tracking outputs ready for ELK/Grafana Loki ingestion.
- **Controlled Processing**: Local PDF directory scans can run concurrently; SEC Edgar HTML filings are processed sequentially so each filing and its graph relations are committed before the next begins.

---

## 📂 Project Architecture

```text
financial-pipeline/
├── pyproject.toml           # Package configuration & system requirements
├── .env                     # Environment configuration (git-ignored)
├── .gitignore               # Git exclusion rules
├── src/
│   └── fin_pipeline/        # Main source workspace module
│       ├── __init__.py      # Package export points & logging init
│       ├── cli.py           # Command Line Interface (Click layer) - PDF, HTML, and SEC Edgar support
│       ├── crawler.py       # Directory scanning (PDF, HTML, SEC Edgar structure parser)
│       ├── pipeline.py      # Main ingestion orchestrator (parse → validate → DB upsert → graph relations)
│       ├── sec_edgar.py     # SEC ticker/CIK CSV fetching and streaming
│       ├── config/
│       │   ├── settings.py  # Environment mappings & DB credentials
│       │   └── logger.py    # Loguru configuration & JSON line serializer
│       ├── db/
│       │   ├── db.py        # Schema initialization, SQL helpers, connection utilities
│       │   ├── connection.py # HTTP-based SurrealDB adapter with stale record cleanup
│       │   ├── connection_pool.py # Reusable SurrealDB connection pool
│       │   └── relations.py # Auto-company creation & SurrealQL graph edge builder
│       ├── models/
│       │   └── schemas.py   # Strict Pydantic models matching SurrealDB SCHEMAFULL schema
│       └── utils/
│           ├── crypto.py    # Core file SHA256 checksum routines
│           ├── ocr_engine.py # Pytesseract image converter fallback
│           ├── pdf_parser.py # PDF text, metadata, and Markdown table extraction
│           ├── html_parser.py # HTML text, metadata, and table extraction
│           ├── retry.py      # Network retry/backoff helper
│           └── db_utils.py   # Shared database identifier utilities
└── tests/                   # Pytest automation suite with async database setup & validation
```

---

## 🛠️ Prerequisites & Installation

### 1. System Dependencies

Because this pipeline handles scanned pixel assets, you must install layout and OCR rendering binaries directly onto your underlying system architecture before running python compilation commands:

```bash
# Ubuntu/Debian Linux environment setup
sudo apt-get update && sudo apt-get install -y tesseract-ocr poppler-utils

# macOS environment setup (via Homebrew)
brew install tesseract poppler
```

### 2. Package Installation

Clone the repository workspace and compile it locally using standard pip commands:

```bash
# Standard distribution installation
pip install .

# Development installation (includes the pytest test dependencies)
pip install -e ".[dev]"

# Optional SEC metadata enrichment
pip install -e ".[sec]"
```

---

## ⚙️ Environment Variables Config

Configure your local system ledger parameters by exporting variables or passing them into your application containers:

| Variable Name            | Description                                     | Default Target Fallback   |
| :----------------------- | :---------------------------------------------- | :------------------------ |
| `SURREAL_ENDPOINT`       | HTTP SurrealDB listener endpoint                | `http://127.0.0.1:8000`   |
| `SURREAL_USER`           | Security login user root handle                 | `root`                    |
| `SURREAL_PASS`           | Connection security validation password         | `secret`                  |
| `SURREAL_NAMESPACE`      | Database validation cluster cluster namespace   | `finance`                 |
| `SURREAL_DATABASE`       | Target working database storage instance        | `analytics`               |
| `COMPANY_TABLE`          | Parent nodes table containing company entries   | `company`                 |
| `EDGAR_IDENTITY`         | SEC User-Agent identity for optional edgartools enrichment | empty (disabled) |
| `FIN_PIPELINE_LOG_LEVEL` | Terminal and file log filtration visibility     | `INFO`                    |
| `FIN_PIPELINE_LOG_DIR`   | Output destination directory path for telemetry | `logs`                    |

---

## 🧾 Metadata Extraction

Every parser returns the common metadata keys when they can be identified:

| Field | HTML source | PDF source |
| :---- | :---------- | :--------- |
| `stockName` | Inline XBRL `EntityRegistrantName` | PDF title and text near legal company suffixes |
| `filingDate` | Inline XBRL `DocumentPeriodEndDate` | Reporting-period date patterns such as `As of December 31, 2025` |
| `filingType` | Inline XBRL `DocumentType` | Form patterns such as `10-K`, `10-Q`, and `8-K` |
| `exchange` | Filing text | Filing text and configured exchange mapping |
| `cik` | Inline XBRL `EntityCentralIndexKey` | `CIK` labels in extracted text |

PDF parsing uses embedded document properties first, then falls back to text
patterns. Scanned PDFs use the same metadata extraction step after OCR. Dates
are normalized to `YYYY-MM-DD` before the pipeline's Pydantic validation.

Metadata supplied by the caller remains authoritative: the ingestion pipeline
only fills a field from parser output when the caller supplied no value, an
empty value, or `UNKNOWN`. This makes local extraction useful as a recovery
path without overwriting trusted upstream metadata.

Parser output also includes:

- `text`: normalized document text
- `tables`: extracted tables represented as Markdown with page and row metadata
- `table_cnt`: number of extracted tables
- `reason`: extraction strategy, such as `Digital Native` or `OCR Scanner Fallback`

The optional `sec` extra enables `edgartools` enrichment for SEC filings when
`EDGAR_IDENTITY` is configured. Local PDF and HTML parsing does not require
that extra.

---

## 💻 Usage Instructions

The package exposes a unified entry script called `fin-pipeline` globally inside your terminal once compilation completes successfully.

### Command Line Interface (CLI)

#### Scenario A: Crawling an Unstructured Local Directory

Recursively scan any local folder directory for text-based or scanned PDFs. The crawler automatically calculates file fingerprints and constructs baseline metadata layers required to fulfill SurrealDB constraints:

```bash
fin-pipeline scan /absolute/path/to/financial/reports --source LOCAL --concurrency 4
```

#### Scenario B: Processing SEC Edgar HTML Filings from Directory Structure

Discover and process SEC Edgar HTML filings stored in a local directory hierarchy. The pipeline extracts ticker, filing type, and accession number from the directory structure, then reads company name, fiscal period end date, and exchange from the HTML document's Inline XBRL and filing text.

When `EDGAR_IDENTITY` is configured, `edgartools` optionally enriches the filing with SEC submission metadata. BeautifulSoup remains the local parser for document text and tables, and any edgartools dependency, identity, network, or lookup failure falls back to locally extracted metadata.

**Expected directory structure:**
```
sec_filings/
└── sec-edgar-filings/
    ├── AAPL/
    │   ├── 10-K/
    │   │   ├── 0000320193-25-000079/
    │   │   │   └── primary-document.html
    │   │   └── 0000320193-24-000080/
    │   │       └── primary-document.html
    │   └── 20-F/
    │       └── 0000320193-23-000081/
    │           └── primary-document.html
    └── MSFT/
        └── 10-K/
            └── 0000789019-25-000091/
                └── primary-document.html
```

```bash
# Process all SEC Edgar HTML filings sequentially
fin-pipeline sec-edgar-html /path/to/sec_filings

# --concurrency is retained for CLI compatibility but is ignored for SEC HTML processing
fin-pipeline sec-edgar-html /path/to/sec_filings --concurrency 1

# Single file processing also works with automatic format detection
fin-pipeline file ./downloads/filing.html \
  --filing-id "sec_manual_filing" \
  --ticker "AAPL" \
  --type "10-K"
```

To enable SEC metadata enrichment, set a descriptive SEC User-Agent identity in `.env`:

```dotenv
EDGAR_IDENTITY=Your Name your.email@example.com
```

#### Scenario C: Ingesting an Explicit SEC Regulatory Filing

Register an individual document while applying explicit regulatory parameter attributes to ensure clean cross-references inside your database layer:

```bash
fin-pipeline file ./downloads/apple_10k.pdf \
  --filing-id "sec_0000320193_2026_10K" \
  --ticker "AAPL" \
  --stock-code "320193" \
  --exchange "NASDAQ" \
  --type "10-K" \
  --source SEC
```

#### Scenario D: Fetching SEC Filings from a Ticker/CIK CSV

Use `edgartools` to retrieve SEC primary HTML documents for companies listed in a CSV. Each row must contain either `ticker` or `cik`. Optional columns are `forms` (comma-separated forms such as `10-K,10-Q`), `year`, and `max_filings`.

Example `companies.csv`:

```csv
ticker,cik,forms,year,max_filings
AAPL,,10-K,2024,1
,0000070858,10-K,2023,1
```

Set an SEC-compliant identity and install the optional dependency before running:

```bash
pip install -e ".[sec]"
export EDGAR_IDENTITY="Your Name your.email@example.com"
fin-pipeline sec-edgar-csv ./companies.csv --download-dir ./sec_downloads
```

The command fetches each primary HTML document, stores it under `--download-dir`, parses its text and tables with BeautifulSoup, optionally enriches metadata with `edgartools`, and persists it through the existing sequential database and graph workflow. A failed filing stops the batch and is logged with its accession number.

To stream the same filings directly into the database without writing HTML files locally, use:

```bash
fin-pipeline sec-edgar-stream ./companies.csv \
   --year-range 2018-2025 \
   --forms 10-K,10-Q
```

`--year-range` is inclusive. The example searches 2018 through 2025. `--forms` accepts comma-separated SEC forms. These CLI options override `year` and `forms` values in the CSV rows; when omitted, row-level filters are used.

Streaming still processes one filing at a time and stores extracted text, tables, metadata, and hashes in SurrealDB. The raw HTML remains in memory only, so a failed filing must be fetched again when retried.

### Python Package Integration

You can easily import core components directly into your own data platforms or automated workflows:

```python
import asyncio
from fin_pipeline.pipeline import run_ingestion_pipeline, process_entire_directory

async def main():
    # 1. Processing a structured target document entry
    custom_metadata = {
        "filingId": "manual_ingest_tsmc_01",
        "companyTicker": "TSM",
        "stockCode": "2330",
        "exchange": "NYSE",
        "filingType": "20-F",
        "referencedTickers": ["AAPL", "NVDA", "ASML"]
    }
    await run_ingestion_pipeline(custom_metadata, "./tsmc_annual.pdf", source="SEC")

   # 2. Local PDF directory ingestion can run concurrently
    await process_entire_directory("/shared/network/pdf_drop", source_type="LOCAL", concurrency_limit=2)

   # SEC HTML directory ingestion is sequential and waits for each graph commit
   # await process_sec_edgar_html_directory("/shared/sec_filings")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔄 Pipeline Execution Flow

The ingestion pipeline follows a multi-stage transformation and persistence workflow:

```
PDF or HTML Input
   ↓
[1] Text & Layout Extraction
   ├─ PDF digital-native parsing with OCR fallback
   ├─ HTML text/table parsing and Inline XBRL metadata extraction
   └─ Optional edgartools SEC metadata enrichment
   ↓
[2] Pydantic Schema Validation
   └─ Type coercion & field validation
   ↓
[3] Stale Record Cleanup
   └─ Delete any incomplete partial records with same filing ID
   ↓
[4] Database Upsert
   └─ Write full validated payload to exchange_filing table
   ↓
[5] Auto-Company Seeding & Graph Relations
   ├─ Create company record if ticker not found
   ├─ Replace the exact existing edge, then create has_filing
   └─ Replace/create references_filing edges
   ↓
[6] Success Log
   └─ Success is reported only after all database and graph operations succeed
```

SEC HTML directory processing waits for each filing to finish all six stages before starting the next. Any parser, validation, database, or graph error stops the batch and identifies the failed accession.

### Auto-Company Creation Behavior

When a filing is ingested, the pipeline automatically discovers and seeds company records:

- **Owning Company**: Created from `companyTicker`, `stockName` (or ticker as fallback), and `exchange` metadata
- **Referenced Companies**: Auto-created for each ticker in `referencedTickers` array
- **Idempotent**: Duplicate tickers are checked before creation, and exact graph edges are replaced before recreation—no duplicate companies or relations are inserted

Example:
```python
# Ingesting a filing with referenced companies
custom_metadata = {
    "filingId": "sec_aapl_10k_2025",
    "companyTicker": "AAPL",
    "stockName": "Apple Inc.",
    "exchange": "NASDAQ",
    "referencedTickers": ["MSFT", "GOOGL", "NVIDIA"]
}

# Result: 4 companies auto-created (AAPL + MSFT + GOOGL + NVIDIA)
# Graph edges: AAPL→filing, filing→MSFT, filing→GOOGL, filing→NVIDIA
```

### Stale Record Cleanup

Before writing a fresh filing record, the pipeline removes any pre-existing record with the same `exchange_filing` ID that may be incomplete or stale from earlier failed writes. This prevents partial data from persisting and causing downstream inconsistencies. SEC accession IDs, including hyphens, are preserved as unique record keys, so different years remain separate records.

---

## 📊 Aligning with the SurrealDB Schema

The pipeline maps extractions to the following `SCHEMAFULL` database schema design rules. Pydantic validation runs before persistence, and embedded SurrealDB query errors are propagated. A filing is successful only when its record and graph relations are stored; sequential SEC processing stops at the first failure.

```surrealql
-- Company Node Table (auto-populated during ingestion)
DEFINE TABLE company SCHEMAFULL;
DEFINE FIELD ticker      ON TABLE company TYPE string UNIQUE;
DEFINE FIELD companyName ON TABLE company TYPE option<string>;
DEFINE FIELD exchange    ON TABLE company TYPE option<string>;
DEFINE FIELD updatedAt   ON TABLE company TYPE option<datetime>;
DEFINE INDEX idx_company_ticker ON TABLE company COLUMNS ticker UNIQUE;

-- Core Filing Table Setup
DEFINE TABLE exchange_filing SCHEMAFULL;
DEFINE FIELD filingId        ON TABLE exchange_filing TYPE string;
DEFINE FIELD companyTicker   ON TABLE exchange_filing TYPE string;
DEFINE FIELD stockCode       ON TABLE exchange_filing TYPE string;
DEFINE FIELD stockName       ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD exchange        ON TABLE exchange_filing TYPE string;
DEFINE FIELD filingType      ON TABLE exchange_filing TYPE string;
DEFINE FIELD documentText    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD documentTextLen ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD documentTables  ON TABLE exchange_filing TYPE option<array<object>>;
DEFINE FIELD referencedTickers ON TABLE exchange_filing TYPE option<array<string>>;
DEFINE FIELD documentStatus  ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD updatedAt       ON TABLE exchange_filing TYPE datetime;

-- Table Geometry Object Array Inner Constraint Matching
DEFINE FIELD documentTables[*]             ON TABLE exchange_filing TYPE object;
DEFINE FIELD documentTables[*].markdown    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD documentTables[*].rowCount    ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD documentTables[*].pageNumber  ON TABLE exchange_filing TYPE option<int>;

-- Graph Relations: Company → Filing Association
DEFINE TABLE has_filing SCHEMAFULL TYPE RELATION IN company OUT exchange_filing;
DEFINE FIELD createdAt ON TABLE has_filing TYPE option<datetime>;
DEFINE INDEX idx_hf_unique ON TABLE has_filing COLUMNS in, out UNIQUE;

-- Graph Relations: Filing → Referenced Company Cross-References
DEFINE TABLE references_filing SCHEMAFULL TYPE RELATION IN exchange_filing OUT company;
DEFINE FIELD createdAt ON TABLE references_filing TYPE option<datetime>;
DEFINE FIELD source    ON TABLE references_filing TYPE option<string>;
DEFINE INDEX idx_rf_unique ON TABLE references_filing COLUMNS in, out UNIQUE;
```

---

## 🧪 Testing Suite Execution

The repository contains tests using `pytest` and `unittest.mock` to simulate text extractions and verify database structures without writing real testing records into live databanks.

```bash
# Execute the testing suites with full logging context visibility
pytest -v
```

---

## 🔧 Recent Improvements & Fixes

### Session Stabilization + HTML Filing Support (Latest Release)

The pipeline has been hardened with production-ready enhancements and now supports both PDF and HTML financial document formats:

**New File Format Support**
- ✅ **HTML Parser** — BeautifulSoup-based extraction of text and tables from HTML documents
- ✅ **Automatic Format Detection** — Pipeline intelligently detects and routes PDF/HTML files to correct parsers
- ✅ **SEC Edgar Directory Crawler** — Scans local SEC Edgar filing structures and extracts ticker, filing type, and full hyphenated accession IDs
- ✅ **HTML Metadata Extraction** — Reads company name, fiscal period end date, filing type, and exchange from SEC HTML/XBRL content; stores `HTML` as the document type
- ✅ **Multi-Year Support** — Processes every accession directory, retaining each year's filing as a separate database record
- ✅ **Sequential SEC Processing** — Completes record persistence and graph relations before starting the next document; stops on failure

**Database Adapter & Persistence**
- ✅ **HTTP-based SurrealDB connection** using urllib instead of async WebSocket client for maximum stability
- ✅ **Full UPSERT with CONTENT** — Records now written with complete field payloads (no more partial no-op updates)
- ✅ **Datetime literal handling** — SurrealDB datetime fields receive proper `d'ISO8601Z'` format literals
- ✅ **Stale record cleanup** — `delete_record()` removes incomplete legacy records before fresh writes
- ✅ **None-value pruning** — Optional fields with null values stripped before DB commit to maintain schema compliance
- ✅ **Accession-safe record IDs** — Hyphenated accession numbers are escaped or parameterized so they cannot be truncated or interpreted as subtraction
- ✅ **Strict query error detection** — Errors returned inside SurrealDB result arrays now fail the filing instead of producing false success logs

**Graph Relations & Company Seeding**
- ✅ **Auto-company creation** — Companies automatically seeded during ingestion; no manual DB bootstrapping
- ✅ **Idempotent company checks** — Duplicate ticker creation prevented via unique constraint
- ✅ **Direct SQL RELATE statements** — Graph edges created with proper SurrealQL literal syntax
- ✅ **Idempotent graph edges** — Existing exact `has_filing` and `references_filing` edges are removed before recreation
- ✅ **Referenced company linking** — `references_filing` edges connect filings to all mentioned company tickers

**Logging & Observability**
- ✅ **Loguru compatibility** — Fixed `record["time"]` handling for current Loguru version
- ✅ **Structured JSON output** — All logs serialized as single-line JSON for ELK/Grafana integration
- ✅ **Safe exception handling** — Logger fallback prevents crashes on serialization errors

**Schema & Validation**
- ✅ **SCHEMAFULL enforcement** — Strict type checking at DB write time; rejected records include full error context
- ✅ **Pydantic model validation** — Python-side type coercion and field validation before DB commit
- ✅ **Field migration support** — Deprecated `documentContent` (blob) field automatically removed on schema init
- ✅ **Relation schema migration** — Existing graph relation timestamps are migrated to optional fields for compatible bare `RELATE` creation

### Test Coverage

All fixes validated with regression test suite:
- Schema initialization and edge table creation
- Logger serialization and exception handling
- Full UPSERT round-trip with datetime fields
- Pydantic model validation and error reporting
- Strict embedded database error detection and sequential batch failure handling
- SEC HTML metadata extraction and multi-year accession preservation

---
