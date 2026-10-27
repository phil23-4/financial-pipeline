# Financial Pipeline Extractor (`fin_pipeline`)

`fin_pipeline` is a Python ingestion pipeline for financial filings. It reads
local PDFs and HTML filings, extracts text and tables, validates the output,
and persists structured filing records and graph relationships into SurrealDB.

The pipeline supports the Forbes Global 2000 corpus (2,000 companies, fiscal
years 2018–2025). It handles two disclosure sources: SEC EDGAR filings
(Form 10-K, 20-F, 40-F) with XBRL-tagged financial facts, and glossy annual
reports (PDFs from company websites) that require OCR and heuristic
extraction. Structured filings receive section-level labels and canonical
XBRL facts; glossy reports receive text and chunk extraction for narrative
analysis.

## Current implementation supports

- PDF ingestion via PyMuPDF4LLM (Markdown structure preservation) with
  optional hybrid OCR (RapidOCR detection + Tesseract recognition)
- HTML/Inline-XBRL extraction for SEC-style filings via BeautifulSoup
- Filing metadata extraction: company name, filing date, period end date,
  form type, exchange, CIK, LEI, ISIN, CUSIP, SEDOL, language, page count
- Camelot table extraction (lattice-first, stream-fallback) with page,
  row, header, and accuracy metadata
- Section labeling for Form 10-K and 20-F via Item header regex
- XBRL fact extraction from SEC bulk datasets (companyfacts.zip and
  quarterly FSDS archives), with 17 canonical concepts
- Graph creation for company and filing relationships in SurrealDB
- Post-ingestion enrichment: GICS industry codes, reportingYear backfill,
  CIK backfill
- Retry-aware database calls, connection pooling, resumable batches
- Structured JSON logging via loguru

## Project purpose

This package is designed for research-grade financial-document processing
pipelines that need to:

- ingest SEC filings and private PDF reports into a unified schema
- preserve page-level provenance for narrative analysis
- extract financial facts from standardized XBRL taxonomies
- maintain a graph of company and filing relationships
- support resumable batch ingestion across thousands of documents

## System requirements and pre-flight configuration

**CRITICAL: SurrealDB HTTP body limits must be raised before ingestion.**
SurrealDB 3.x defaults to 4 MiB on `/rpc` and 1 MiB on `/sql`. Many SEC
filings exceed these limits (for example, large bank Form 20-F filings can
reach 6–7 MB of JSON payload). Writes will be rejected with HTTP 413
without these settings:

```bash
SURREAL_HTTP_MAX_RPC_BODY_SIZE=128MiB \
SURREAL_HTTP_MAX_SQL_BODY_SIZE=16MiB \
SURREAL_LOG_FILE_ENABLED=true \
surreal start surrealkv:data/analytics_data --log info
```

The 128 MiB `/rpc` limit provides approximately 20× headroom over the
largest observed filing and 5× over the estimated worst case in the Forbes
2000 corpus. The 16 MiB `/sql` limit covers metadata-only writes.

Python 3.12 is the tested version. Python 3.11+ is declared as a minimum
in `pyproject.toml`, but only 3.12 is exercised in CI and local development.

## Current architecture

```text
financial-pipeline/
├── pyproject.toml
├── README.md
├── AGENTS.md                       # durable instructions for doc generation
├── data/
│   └── industry_mapping.csv        # GICS codes (curation artifact)
├── docs/                           # design, ops, and debugging docs
├── src/
│   └── fin_pipeline/
│       ├── __init__.py
│       ├── cli.py                  # click-based CLI
│       ├── crawler.py              # local directory scanning
│       ├── enrichment.py           # post-ingestion GICS + backfills
│       ├── export.py               # Parquet export
│       ├── pipeline.py             # orchestration
│       ├── preferred_filter.py     # preferred-share ticker filter
│       ├── sec_edgar.py            # EDGAR client + 40-F exhibit concat
│       ├── config/
│       │   ├── __init__.py
│       │   ├── constants.py
│       │   ├── logger.py           # reads FIN_PIPELINE_LOG_* vars
│       │   ├── settings.py         # reads SURREAL_*, EDGAR_* vars
│       │   └── structured_logging.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── connection.py       # HTTP adapter, merge(), retries
│       │   ├── connection_pool.py
│       │   ├── db.py               # schema DDL, query helpers
│       │   └── relations.py        # graph edges
│       ├── models/
│       │   ├── __init__.py
│       │   └── schemas.py          # ExchangeFilingModel, etc.
│       └── utils/
│           ├── __init__.py
│           ├── classify.py         # filing type → sourceType
│           ├── crypto.py
│           ├── db_utils.py
│           ├── html_parser.py
│           ├── metadata.py
│           ├── pdf_parser.py
│           ├── retry.py
│           ├── sections.py         # section labeling
│           ├── xbrl_bulk.py        # bulk download + Parquet cache
│           └── xbrl_facts.py       # per-filing fallback extractor
└── tests/
    ├── conftest.py
    ├── test_cli.py
    ├── test_crawler.py
    ├── test_enrichment.py
    ├── test_pdf_parser.py
    ├── test_pipeline.py
    ├── test_preferred_filter.py
    ├── test_sec_edgar.py
    ├── test_schema_integration.py
    └── test_xbrl_bulk.py
```

## Key capabilities

- **PDF ingestion**: `parse_pdf_layout()` in `utils/pdf_parser.py` uses
  `pymupdf4llm.to_markdown(..., page_chunks=True)` to preserve headings,
  emphasis, lists, and document structure in Markdown. Page-level chunks
  retain page numbers for provenance.
- **PDF table extraction**: `camelot-py` is invoked with the lattice flavor
  first and the stream flavor as fallback. Each table is normalized to
  Markdown with string-only headers, escaped cells, and an accuracy metric
  from Camelot's parsing report.
- **Hybrid OCR**: PyMuPDF4LLM detects scanned or incomplete text layers and
  invokes `rapidtess_api.exec_ocr`, combining RapidOCR region detection with
  Tesseract recognition while preserving layout. OCR is **opt-in per page**
  by default; force-OCR (`--ocr` or `force_ocr=True`) applies it to every
  page. `--no-ocr` disables it entirely.
- **HTML/Inline XBRL parsing**: `parse_html_file()` uses BeautifulSoup.
  Hidden containers (`<ix:header>`, `<ix:hidden>`, `<ix:references>`,
  `display:none` elements) are decomposed before text extraction, so
  taxonomy URLs do not pollute visible text. Text extraction uses `\n` as
  a tag separator to preserve header boundaries for section labeling.
- **Metadata normalization**: nine fields are extracted from both HTML and
  PDF sources — `stockName`, `filingDate`, `filingType`, `exchange`, `cik`,
  `lei`, `isin`, `cusip`, `sedol` — with per-field source tags and
  confidence scores recorded in `metadataSources` and `metadataConfidence`.
- **Section labeling**: `utils/sections.py::label_sections_structured`
  matches Form 10-K Item headers (1, 1A, 7, 8) and Form 20-F Item headers
  (3.D, 4, 5, 6, 8, 15) to six canonical labels: `business`,
  `risk_factors`, `mdna`, `financial_statements`, `corporate_governance`,
  `other`. Section labels are **not applied to Form 40-F** (see Known
  Limitations).
- **XBRL fact extraction**: `utils/xbrl_bulk.py` downloads
  `companyfacts.zip` (~1.4 GB) and quarterly FSDS archives from SEC, parses
  them locally, and writes a Parquet cache keyed on
  `(accession_no, concept)`. At ingestion time, `xbrlFacts` is populated by
  cache lookup — no per-filing HTTP requests.
- **Enrichment**: `enrichment.py::enrich_industry_codes` reads
  `data/industry_mapping.csv`, does a three-tier lookup (CIK → companyKey
  → ticker), and writes `gicsCode`, `gicsSector`, `industryConfidence`.
  Also backfills `reportingYear` from `periodEndDate` and `cik` from a
  ticker-to-CIK CSV.
- **Preferred-share filter**: `preferred_filter.py` detects tickers like
  `GS-PD`, `C-PR`, `MS-PA` (dash + 2+ uppercase letters). Only drops rows
  when a plain-ticker sibling exists in the same CSV; preserves dual-class
  common shares (`BRK-A`, `BRK-B`, `BF-A`, `BF-B`).
- **Graph persistence**: `relations.py::establish_graph_relations` creates
  `has_filing` (company → filing) and `references_filing` (filing →
  referenced company) edges. Company nodes are keyed by
  SHA256(uppercase ticker + normalized name) to disambiguate same-ticker
  companies.
- **Resumability**: `--skip-existing` skips filings already in the DB by
  `filingId`; `--skip-by-hash` also skips content-hash matches, catching
  renamed or moved PDFs. Both flags work on all batch commands.

## Ingestion flows

### SEC streaming (`sec-edgar-stream`)

1. Pre-load existing `sec_*` filingIds and documentHashes (resume)
2. For each CSV row, resolve CIK, call edgartools `get_filings(form=...)`
3. For Form 40-F: concatenate `EX-99.*` exhibits with `<h1>` markers
4. Otherwise: fetch primary HTML via `filing.html()`
5. Compute `documentHash`, apply skip filters
6. Parse via `run_html_content_pipeline`: extract text, metadata, chunks,
   sections; load XBRL facts from bulk cache; validate; persist

### PDF scanning (`scan`, `scan-mixed`, `file`)

1. Pre-load existing `local_*` filingIds and hashes (resume)
2. For each PDF: compute SHA-256, derive filingId as
   `local_{basename}_{hash[:8]}`
3. Infer metadata from parent directory and filename
4. Parse via `parse_pdf_layout`: Markdown text, tables, OCR as needed
5. Detect language with `langdetect` on first 5,000 chars
6. Persist

### Bulk XBRL download (`xbrl-bulk`)

1. Download `companyfacts.zip` to `xbrl_bulk/` if absent
2. Download quarterly FSDS archives for the requested year range
3. Parse each archive, filter to target CIK list, extract 17 canonical
   concepts with instant-vs-duration and priority-order selection
4. Write `xbrl_bulk/xbrl_facts.parquet`
5. Completed archives receive `.done` checkpoints and are reused

## Metadata extraction

### HTML

Reads Inline XBRL tags via BeautifulSoup: `EntityRegistrantName` (stockName),
`DocumentPeriodEndDate` (periodEndDate, filingDate), `DocumentType`
(filingType), `EntityCentralIndexKey` (cik), `CurrentFiscalYearEndDate`
(fiscalYearEndMonth). Function:
`utils/html_parser.py::extract_filing_metadata`.

### PDF

Two-step: embedded PDF properties (`pymupdf.Document.metadata`), then
regex on cleaned Markdown text. Extracts company names near legal suffixes
("Inc", "Corp", "LLC"), form types (10-K, 10-Q, 8-K, 20-F, etc.), dates,
CIK values, and exchange names via `EXCHANGE_MAPPING`. Dates normalized
to `YYYY-MM-DD` before validation.

### Precedence

Caller-supplied metadata is authoritative. The pipeline fills missing
fields from parsed output only when the caller value is empty, null, or
`UNKNOWN`. This allows the parser to act as a recovery path without
overwriting trusted metadata.

### Local PDF naming conventions

`scan` and `scan-mixed` recognize structured paths:

```text
Filings Data/
└── romania/
    └── Banca Transilvania/
        └── 131662.ar.en.2018.pdf
```

Populated metadata:

| Field | Source | Example |
|---|---|---|
| `stockName` | Immediate parent directory | `Banca Transilvania` |
| `stockCode` | Filename prefix | `131662` |
| `filingType` | Filename report code | `ANNUAL_REPORT` |
| `filingDate` | Filename year, inferred as year-end | `2018-12-31` |
| `exchange` | Local source default | `LOCAL_FS` |

Supported filename pattern: `{stock_code}.{ar|annual_report}.{language}.{year}.pdf`.

For country/ticker layouts, the ticker is read from an uppercase
parent directory (`TLV`, `AAPL`) while other fields come from the filename.

## Installation

```bash
# Base install
pip install .

# Development (adds pytest, ruff, etc.)
pip install -e ".[dev]"

# SEC metadata enrichment (edgartools)
pip install -e ".[sec]"

# SEC bulk XBRL downloads and Parquet output
pip install -e ".[xbrl-bulk]"

# Optional language detection for PDFs
pip install langdetect
```

System dependencies: Tesseract OCR must be installed on the host.
`brew install tesseract` on macOS, `apt install tesseract-ocr` on Debian/Ubuntu.

## Environment configuration

Environment variables are read by two modules:

- `config/settings.py` reads `SURREAL_*`, `COMPANY_TABLE`, `EDGAR_IDENTITY`,
  and the project log path used for diagnostic files.
- `config/logger.py` reads `FIN_PIPELINE_LOG_LEVEL` and
  `FIN_PIPELINE_LOG_DIR` at import time.

| Variable | Read by | Purpose | Typical value |
|---|---|---|---|
| `SURREAL_ENDPOINT` | `settings.py` | SurrealDB HTTP endpoint | `http://127.0.0.1:8000` |
| `SURREAL_USER` | `settings.py` | SurrealDB username | `root` |
| `SURREAL_PASS` | `settings.py` | SurrealDB password | `secret` |
| `SURREAL_NAMESPACE` | `settings.py` | SurrealDB namespace | `finance` |
| `SURREAL_DATABASE` | `settings.py` | SurrealDB database | `analytics` |
| `SURREAL_HTTP_MAX_RPC_BODY_SIZE` | **SurrealDB server** | Max body size on `/rpc` | `128MiB` (required) |
| `SURREAL_HTTP_MAX_SQL_BODY_SIZE` | **SurrealDB server** | Max body size on `/sql` | `16MiB` (required) |
| `COMPANY_TABLE` | `settings.py` | Company table name | `company` |
| `EDGAR_IDENTITY` | `settings.py` | SEC User-Agent with contact email | `Your Name your.email@example.com` |
| `FIN_PIPELINE_LOG_LEVEL` | `logger.py` | Loguru log level | `INFO` |
| `FIN_PIPELINE_LOG_DIR` | `logger.py` | Directory for JSON logs | `logs` |

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

The SurrealDB body-size limits are set on the **SurrealDB server process**,
not the pipeline. See "System requirements" above for the exact invocation.

## CLI usage

### Batch ingestion

**Scan a local folder of PDFs:**

```bash
fin-pipeline scan /path/to/financial_docs --source LOCAL --concurrency 4
```

**Scan a mixed PDF and HTML folder:**

```bash
fin-pipeline scan-mixed /path/to/documents --source LOCAL
```

**Process SEC EDGAR HTML directory layout:**

```bash
fin-pipeline sec-edgar-html /path/to/sec_filings --concurrency 4
```

**Fetch and ingest SEC filings from a CSV (writes HTML to disk):**

```bash
fin-pipeline sec-edgar-csv ./companies.csv --download-dir ./sec_downloads
```

**Stream SEC filings from a CSV (no disk writes):**

```bash
fin-pipeline sec-edgar-stream ./companies.csv \
    --year-range 2018-2025 \
    --forms 10-K,20-F,40-F \
    --skip-existing
```

**Download and parse SEC bulk XBRL data:**

```bash
fin-pipeline xbrl-bulk --start-year 2018 --end-year 2025 \
    --cik-csv companies.csv
```

The CIK CSV must include a `cik` column. Omitting `--cik-csv` processes all
available CIKs. Pass `--force` to re-download archives that already have
`.done` checkpoints. FSDS quarters not yet published return HTTP 404 and
are skipped.

**Post-ingestion enrichment:**

```bash
fin-pipeline enrich --mapping data/industry_mapping.csv
```

Populates `gicsCode`, `gicsSector`, `industryConfidence` from the mapping
CSV, backfills `reportingYear` from `periodEndDate`, and backfills `cik`
from a ticker-to-CIK CSV if provided. Idempotent — safe to re-run.

**Filter preferred-share tickers from a CSV:**

```bash
fin-pipeline filter-preferred --input sec_companies.csv --dry-run
fin-pipeline filter-preferred --input sec_companies.csv
```

Detects tickers like `GS-PD`, `C-PR`, `MS-PA` and drops them **only when a
plain-ticker sibling exists** in the same CSV. Preserves `BRK-A`, `BRK-B`,
`BF-A`, `BF-B` (dual-class common shares).

**Export DB to Parquet:**

```bash
fin-pipeline export --output-dir ./export
```

### Single-file ingestion

```bash
fin-pipeline file ./downloads/apple_10k.pdf \
  --filing-id "sec_0000320193_2026_10K" \
  --ticker "AAPL" \
  --stock-name "Apple Inc." \
  --stock-code "320193" \
  --exchange "NASDAQ" \
  --type "10-K" \
  --source SEC \
  --no-ocr
```

`--stock-name` is treated as authoritative; PDF extraction is used only when
the field is omitted. `--force` overrides both `--skip-existing` and
`--skip-by-hash` for a single-file ingestion.

### Resumable batches

`--skip-existing` and `--skip-by-hash` are available on `scan`, `scan-mixed`,
`sec-edgar-html`, `sec-edgar-csv`, `sec-edgar-stream`, and `file`. They pre-load
existing filing IDs and content hashes at batch start (one DB query) and skip
matching files at yield time. Re-running an aborted batch resumes from where it
stopped, not from the beginning.

```bash
fin-pipeline scan /path/to/pdfs --source LOCAL --skip-existing --skip-by-hash
fin-pipeline sec-edgar-stream ./companies.csv --year-range 2018-2025 \
    --forms 10-K,20-F,40-F --skip-existing
```

## Python API

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

    await run_ingestion_pipeline(
        custom_metadata, "./tsmc_annual.pdf", source="SEC"
    )

    await process_entire_directory(
        "/shared/network/pdf_drop",
        source_type="LOCAL",
        concurrency_limit=2,
        skip_existing=True,
        skip_by_hash=True,
    )

asyncio.run(main())
```

## Data model

The pipeline writes to three tables in SurrealDB, all `SCHEMAFULL`:

- `exchange_filing` — one record per ingested filing
- `company` — one record per legal entity, keyed on a deterministic hash
- `has_filing` / `references_filing` — graph edges

`ExchangeFilingModel` (in `models/schemas.py`) declares the full schema.
Fields fall into eight groups: identity, classification, temporal, document
content, extracted data, enrichment, provenance, processing.

Seven fields are stored as **JSON-encoded strings**, not native SurrealDB
objects: `documentChunks`, `documentTables`, `xbrlFacts`, `xbrlProvenance`,
`metadataSources`, `metadataConfidence`, `exhibitSectionMap`. This is
required because SurrealDB 2.x/3.x coerces strings matching `word:word` into
RecordID references during `CONTENT` writes, which rejects valid filing
text (e.g., `Note:Unless`, `M:68` in mortality tables). Read paths decode
with `encoding::json::decode(field)` in SurrealQL or `json.loads()` in
Python.

See `docs/DESIGN_RATIONALE.md` §4 for the full field list and §10.2 for the
JSON-encoding decision rationale.

### Schema excerpt

The `db.py::_build_schema_sql()` function is the authoritative schema DDL.
The excerpt below covers only the highest-traffic fields. See
`docs/DESIGN_RATIONALE.md` Appendix A for the complete definition.

```surrealql
DEFINE TABLE exchange_filing SCHEMAFULL;

-- Identity
DEFINE FIELD IF NOT EXISTS filingId     ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS companyTicker ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS cik           ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS stockName     ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS exchange      ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS filingType    ON TABLE exchange_filing TYPE string;

-- Classification
DEFINE FIELD IF NOT EXISTS sourceType    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS isAmendment   ON TABLE exchange_filing TYPE bool;

-- Temporal
DEFINE FIELD IF NOT EXISTS filingDate       ON TABLE exchange_filing TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS periodEndDate    ON TABLE exchange_filing TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS fiscalYearEndMonth ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS reportingYear    ON TABLE exchange_filing TYPE option<int>;

-- Document content (nested fields are JSON strings, see above)
DEFINE FIELD IF NOT EXISTS documentText        ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentChunks      ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentTables      ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentTableCnt    ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS documentHash        ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentStatus      ON TABLE exchange_filing TYPE option<string>;

-- Extracted data
DEFINE FIELD IF NOT EXISTS xbrlFacts       ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS xbrlProvenance  ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS xbrlSource      ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS xbrlCurrency    ON TABLE exchange_filing TYPE option<string>;

-- Enrichment
DEFINE FIELD IF NOT EXISTS gicsCode           ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS gicsSector         ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS industryCodeRaw    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS industryConfidence ON TABLE exchange_filing TYPE option<float>;

-- Provenance
DEFINE FIELD IF NOT EXISTS metadataSources    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS metadataConfidence ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS language           ON TABLE exchange_filing TYPE option<string>;

-- Processing
DEFINE FIELD IF NOT EXISTS updatedAt ON TABLE exchange_filing TYPE datetime;
```

**On `sourceType`:** the field is nullable at the database layer
(`TYPE option<string>`). The default value `"unknown"` is applied by the
Pydantic model `ExchangeFilingModel.sourceType` in
`src/fin_pipeline/models/schemas.py`, not by SurrealDB. Pre-existing records
written before this field was introduced will have `sourceType IS NONE`
until re-ingested or backfilled.

The database layer is managed in:

- `src/fin_pipeline/db/db.py` — schema DDL and raw SQL helpers
- `src/fin_pipeline/db/connection.py` — HTTP adapter, JSON serialization,
  `merge()` for partial updates
- `src/fin_pipeline/db/connection_pool.py` — connection pooling
- `src/fin_pipeline/db/relations.py` — graph edges

Company graph nodes are keyed by SHA256(uppercase ticker + normalized
company name). If the company name is unavailable, the pipeline skips
creating a company node rather than merging by ticker alone — this preserves
separate identities for companies that share a ticker across exchanges.

## Metadata fields added in 2025-10

Nine fields are populated by the metadata extraction pass:
`periodEndDate`, `fiscalYearEndMonth`, `language`, `pageCount`,
`industryCodeRaw`, `gicsCode`, `gicsSector`, `industryConfidence`,
`isAmendment`.

Three fields are populated by the XBRL selection pass: `xbrlCurrency`,
`xbrlProvenance`, `xbrlSource`.

All twelve are optional and default to `None`. Existing rows remain valid;
re-ingest or run `fin-pipeline enrich` to populate.

## Validation and error handling

- Strict Pydantic validation via `ExchangeFilingModel` before any DB write
- Stale-record cleanup (`DELETE` then `UPSERT`) before each overwrite
- Database retry with exponential backoff (`utils/retry.py`)
- Per-filing and per-company try/except in `sec_edgar.py` so one failure
  does not abort the batch
- Targeted exception handling for PDF, HTML, OCR, and database failures
- OCR page failures isolated per-page; one bad page does not abort the
  document

## Testing

```bash
pytest -q          # quick
pytest -v          # verbose
pytest --cov=fin_pipeline --cov-report=html
```

Test files and coverage:

| File | Coverage |
|---|---|
| `test_cli.py` | CLI flags, filing type normalization, sourceType derivation |
| `test_crawler.py` | Directory scanning, filename metadata parsing, skip filters |
| `test_pdf_parser.py` | PyMuPDF extraction, Camelot tables, OCR policy, page count |
| `test_pipeline.py` | End-to-end ingestion with mocked parsers/DB, JSON round-trip, skip logic |
| `test_sec_edgar.py` | EDGAR client, 40-F exhibit concatenation, resume pre-load |
| `test_xbrl_bulk.py` | Concept selection, ground-truth tests for GOOG/AAPL/MSFT |
| `test_enrichment.py` | GICS lookup priority, dry-run, industryCodeRaw preservation |
| `test_preferred_filter.py` | Preferred-share detection, dual-class preservation |
| `test_schema_integration.py` | Live DB round-trip (gated on `SURREAL_INTEGRATION=1`) |

Ground-truth tests in `test_xbrl_bulk.py` verify XBRL extraction against
hardcoded expected values from actual filings (GOOG FY2023, AAPL FY2024,
MSFT FY2024). These are the primary acceptance gate for the XBRL path —
they detect any regression in fact selection that unit tests cannot.

## Known limitations

- **40-F section labels are not populated.** Form 40-F exhibit-based
  structure does not map cleanly to Item-based section taxonomy. Content is
  retrieved (500 KB–2 MB per filing), but chunks have `sectionLabel = None`.
  See `docs/DESIGN_RATIONALE.md` §10.6.
- **Camelot returns zero tables on some glossy PDFs.** Tables in SINOFERT-
  style reports are whitespace-aligned only, with no ruled borders. Lattice
  mode skips them; stream mode fails on mixed CJK/English. `<table>` blocks
  exist in `documentText` but not in `documentTables`.
- **Non-December fiscal year ends** default to `{year}-12-31` in the PDF
  path. Japanese (March), Indian (March), Australian (June), UK (April)
  filers will have incorrect `periodEndDate` unless the regex-based
  cover-page extractor is enabled.
- **Non-USD XBRL facts are rejected.** ~10–15% of SEC filings (mostly Form
  20-F from EU and Japanese filers) have `xbrlFacts = None`.
- **Non-English glossy reports** have `language` populated but no
  language-appropriate keyword extraction downstream.
- **Preferred-share tickers in the source CSV** (e.g., `GS-PD`, `C-PR`)
  share a CIK with their plain-ticker equivalents. Analyses should join on
  `cik`, not `companyTicker`, or apply `fin-pipeline filter-preferred`.

## Recent implementation notes

- Metadata extraction populated for period end, fiscal year end, language,
  page count, GICS codes
- XBRL fact extraction switched to bulk download (companyfacts.zip +
  quarterly FSDS) with 17 canonical concepts and four-gate selection
  (consolidated filter, period match, instant/duration, priority order)
- Section labeling added for 10-K and 20-F (regex-based, six canonical
  labels)
- Resume support added for all batch commands (`--skip-existing`,
  `--skip-by-hash`)
- Preferred-share filter added (`filter-preferred` CLI command)
- Enrichment pass added (`enrich` CLI command) for GICS codes and backfills
- `cik` field added to schema and populated end-to-end (verified against
  AAPL/GOOG/MSFT)
- JSON-string encoding for seven nested fields (required for arbitrary
  filing text with SurrealDB's RecordID coercion)
- SurrealDB body-size limits raised via env vars rather than code splitting
  (see "System requirements")

## Support and extension points

- Add richer table classification for glossy PDFs (parse `<table>` blocks
  from `documentText` when Camelot returns zero rows)
- Add language-specific keyword lists for non-English AI signal extraction
- Add LLM-based section labeling for 40-F (content-based classifier rather
  than keyword matching)
- Add segment-aware XBRL extraction (requires dimensional parsing; enables
  `segmentRevenue` re-add)
- Add FX conversion for non-USD XBRL facts
- Extend graph relations beyond company-filing edges

## Summary

`fin_pipeline` provides a production-oriented ingestion pipeline for
financial document processing focused on:

- SEC filings (10-K, 20-F, 40-F) and local PDF reports
- Structured text, table, and XBRL extraction
- Page-level chunking with section labels for narrative analysis
- Metadata normalization with source and confidence tracking
- Resumable batch ingestion across thousands of documents
- SurrealDB persistence with graph relationships
- Post-ingestion enrichment for GICS classification and derived fields
