# Financial Pipeline Extractor (`fin_pipeline`)

A production-grade, highly scalable modular Python package designed to extract metadata, text, and tabular structures from financial statements (**SEC 10-K, 20-F, 40-F**) and **local private entity PDFs**. Validated data is transformed into type-safe objects and ingested into an active **SurrealDB** graph database instances using strict `SCHEMAFULL` relational constraints.

## 🌟 Key Features

- **Dual Extraction Strategy**: High-speed digital-native text extraction with automatic `pytesseract` OCR fallbacks for scanned images or faxed financial reports.
- **Table-to-Markdown Engine**: Parses grid geometry and structural visual layouts into standardized Markdown strings while tracking page indexes and row weights.
- **Auto-Company Seeding**: Automatically creates company records when first referenced by filing metadata—no manual database bootstrapping required.
- **SurrealDB Graph Relate Automation**: Automatically calculates cryptographic file signatures and creates type-safe graph edges (`has_filing`, `references_filing`) with seamless company linking.
- **Stale Record Cleanup**: Intelligently removes incomplete partial records from earlier failed writes before each fresh ingestion to prevent data inconsistency.
- **Enterprise-Grade Log Management**: Asynchronous structured logs powered by `loguru`, delivering terminal views alongside single-line JSON log tracking outputs ready for ELK/Grafana Loki ingestion.
- **Concurrently Scheduled Pipeline**: Asynchronous IO engine with customizable execution semaphores to regulate system workloads during intense OCR operations.

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
│       ├── cli.py           # Command Line Interface (Click layer)
│       ├── crawler.py       # Recursive directory scanning file explorer
│       ├── pipeline.py      # Main ingestion orchestrator (parse → validate → DB upsert → graph relations)
│       ├── config/
│       │   ├── settings.py  # Environment mappings & DB credentials
│       │   └── logger.py    # Loguru configuration & JSON line serializer
│       ├── db/
│       │   ├── db.py        # Schema initialization, SQL helpers, connection utilities
│       │   ├── connection.py# HTTP-based SurrealDB adapter with stale record cleanup
│       │   └── relations.py # Auto-company creation & SurrealQL graph edge builder
│       ├── models/
│       │   └── schemas.py   # Strict Pydantic models matching SurrealDB SCHEMAFULL schema
│       └── utils/
│           ├── crypto.py    # Core file SHA256 checksum routines
│           ├── ocr_engine.py# Pytesseract image converter fallback
│           └── pdf_parser.py# Layout bounding box scanner and markdown table builder
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

# Development installation (includes Pytest, Black formatter, and code verification modules)
pip install -e .[dev]
```

---

## ⚙️ Environment Variables Config

Configure your local system ledger parameters by exporting variables or passing them into your application containers:

| Variable Name            | Description                                     | Default Target Fallback   |
| :----------------------- | :---------------------------------------------- | :------------------------ |
| `SURREAL_ENDPOINT`       | WebSockets database listener endpoint           | `ws://localhost:8000/rpc` |
| `SURREAL_USER`           | Security login user root handle                 | `root`                    |
| `SURREAL_PASS`           | Connection security validation password         | `root`                    |
| `SURREAL_NAMESPACE`      | Database validation cluster cluster namespace   | `finance`                 |
| `SURREAL_DATABASE`       | Target working database storage instance        | `analytics`               |
| `COMPANY_TABLE`          | Parent nodes table containing company entries   | `company`                 |
| `FIN_PIPELINE_LOG_LEVEL` | Terminal and file log filtration visibility     | `INFO`                    |
| `FIN_PIPELINE_LOG_DIR`   | Output destination directory path for telemetry | `logs`                    |

---

## 💻 Usage Instructions

The package exposes a unified entry script called `fin-pipeline` globally inside your terminal once compilation completes successfully.

### Command Line Interface (CLI)

#### Scenario A: Crawling an Unstructured Local Directory

Recursively scan any local folder directory for text-based or scanned PDFs. The crawler automatically calculates file fingerprints and constructs baseline metadata layers required to fulfill SurrealDB constraints:

```bash
fin-pipeline scan /absolute/path/to/financial/reports --source LOCAL --concurrency 4
```

#### Scenario B: Ingesting an Explicit SEC Regulatory Filing

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

    # 2. Alternatively, ingest an entire shared network storage path concurrently
    await process_entire_directory("/shared/network/pdf_drop", source_type="LOCAL", concurrency_limit=2)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔄 Pipeline Execution Flow

The ingestion pipeline follows a multi-stage transformation and persistence workflow:

```
PDF Input
   ↓
[1] Text & Layout Extraction
   ├─ Digital-native parsing (first pass)
   └─ OCR fallback for scanned images
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
   ├─ RELATE company→filing edge (has_filing)
   └─ RELATE filing→referenced_companies edges (references_filing)
   ↓
[6] Success Log
   └─ Complete structured JSON log entry
```

### Auto-Company Creation Behavior

When a filing is ingested, the pipeline automatically discovers and seeds company records:

- **Owning Company**: Created from `companyTicker`, `stockName` (or ticker as fallback), and `exchange` metadata
- **Referenced Companies**: Auto-created for each ticker in `referencedTickers` array
- **Idempotent**: Duplicate tickers are checked before creation—no duplicates inserted

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

Before writing a fresh filing record, the pipeline removes any pre-existing record with the same `exchange_filing` ID that may be incomplete or stale from earlier failed writes. This prevents partial data from persisting and causing downstream inconsistencies.

---

## 📊 Aligning with the SurrealDB Schema

The pipeline maps out extractions to strictly comply with the following `SCHEMAFULL` database schema design rules. If the record includes text layer errors or type validation mismatches, the transaction is rejected to guarantee data cleanliness.

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
DEFINE FIELD createdAt ON TABLE has_filing TYPE datetime;
DEFINE INDEX idx_hf_unique ON TABLE has_filing COLUMNS in, out UNIQUE;

-- Graph Relations: Filing → Referenced Company Cross-References
DEFINE TABLE references_filing SCHEMAFULL TYPE RELATION IN exchange_filing OUT company;
DEFINE FIELD createdAt ON TABLE references_filing TYPE datetime;
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

### Session Stabilization (Latest Release)

The pipeline has been hardened with the following production-ready enhancements:

**Database Adapter & Persistence**
- ✅ **HTTP-based SurrealDB connection** using urllib instead of async WebSocket client for maximum stability
- ✅ **Full UPSERT with CONTENT** — Records now written with complete field payloads (no more partial no-op updates)
- ✅ **Datetime literal handling** — SurrealDB datetime fields receive proper `d'ISO8601Z'` format literals
- ✅ **Stale record cleanup** — `delete_record()` removes incomplete legacy records before fresh writes
- ✅ **None-value pruning** — Optional fields with null values stripped before DB commit to maintain schema compliance

**Graph Relations & Company Seeding**
- ✅ **Auto-company creation** — Companies automatically seeded during ingestion; no manual DB bootstrapping
- ✅ **Idempotent company checks** — Duplicate ticker creation prevented via unique constraint
- ✅ **Direct SQL RELATE statements** — Graph edges created with proper SurrealQL literal syntax
- ✅ **Referenced company linking** — `references_filing` edges connect filings to all mentioned company tickers

**Logging & Observability**
- ✅ **Loguru compatibility** — Fixed `record["time"]` handling for current Loguru version
- ✅ **Structured JSON output** — All logs serialized as single-line JSON for ELK/Grafana integration
- ✅ **Safe exception handling** — Logger fallback prevents crashes on serialization errors

**Schema & Validation**
- ✅ **SCHEMAFULL enforcement** — Strict type checking at DB write time; rejected records include full error context
- ✅ **Pydantic model validation** — Python-side type coercion and field validation before DB commit
- ✅ **Field migration support** — Deprecated `documentContent` (blob) field automatically removed on schema init

### Test Coverage

All fixes validated with regression test suite:
- Schema initialization and edge table creation
- Logger serialization and exception handling
- Full UPSERT round-trip with datetime fields
- Pydantic model validation and error reporting

---
