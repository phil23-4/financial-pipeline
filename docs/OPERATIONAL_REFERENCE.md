# Operational reference

This document is the operator's guide for running `fin_pipeline` batches on
new data. It covers prerequisites, installation, SurrealDB configuration,
pre-batch checks, the complete CLI reference, CSV input formats, a typical
workflow, timing and resource estimates, post-batch verification, the
environment-variable reference, and a glossary. Every technical claim cites
its source inline; where the source cannot be verified, the claim is marked
with a TODO.

## 1. Prerequisites

### Python version

The package declares `requires-python = ">=3.11"`
(`Verified from pyproject.toml:10`). The Trove classifiers list Python 3.11
through 3.14 (`Verified from pyproject.toml:31-34`). The README states that
Python 3.12 is the tested version and 3.11+ is the declared minimum
(`Verified from README.md:63-64`).

### System dependencies

- **Tesseract OCR.** The hybrid OCR path (RapidOCR detection plus Tesseract
  recognition) requires Tesseract on the host. macOS: `brew install
  tesseract`; Debian/Ubuntu: `apt install tesseract-ocr`
  (`Verified from README.md:280-281`).
- **SurrealDB 3.x.** The server body-limit environment variables are
  SurrealDB 3.x settings (`Verified from README.md:47`). A developer run
  observed version `3.1.5` (`Verified from logs/surrealdb.log.2026-10-26`,
  line "Running 3.1.5 for macos on aarch64").

### Disk space

Plan for two independent stores plus the XBRL bulk download directory.

- `xbrl_bulk/` — the bulk XBRL working directory. An observed developer run
  holds 4.6 GB total, comprising `companyfacts.zip` (1,411,517,904 bytes,
  ≈1.41 GB; README says ~1.4 GB at `README.md:157`), 32 quarterly FSDS
  archives (≈63–128 MB each), and the derived `xbrl_facts.parquet`
  (22,168,600 bytes, ≈22 MB). Sizes observed via `du -sh` and `ls -la` in the
  working directory.
- `data/analytics_data/` — the SurrealDB `surrealkv` store, 1.0 GB in an
  observed developer run (`du -sh data/analytics_data/`).

These are observed point-in-time sizes, not guarantees; the archives and the
store grow with the corpus.

### Network access

The pipeline fetches SEC data over HTTPS. The bulk XBRL path downloads from
`https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip`
(`Verified from src/fin_pipeline/utils/xbrl_bulk.py:21-23`) and
`https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip`
(`Verified from src/fin_pipeline/utils/xbrl_bulk.py:24-27`). The stream path
uses `edgartools` to resolve companies and fetch primary HTML
(`Verified from src/fin_pipeline/sec_edgar.py:295`). SEC download paths
require an `EDGAR_IDENTITY` User-Agent string
(`Verified from src/fin_pipeline/sec_edgar.py:289-293`).

## 2. Installation

Run each step from the repository root.

1. Install the base package in editable mode.

   ```bash
   pip install -e .
   ```

   Verification: `fin-pipeline --help` prints the command group
   (`Verified from pyproject.toml:84-85`, which registers the `fin-pipeline`
   entry point to `fin_pipeline.cli:main`).

2. Install the SEC retrieval dependency.

   ```bash
   pip install -e ".[sec]"
   ```

   Verification: `python -c "import edgar; print(edgar.__version__)"`
   succeeds. The `sec` extra pins `edgartools>=5.55.0`
   (`Verified from pyproject.toml:63-65`).

3. Install the Parquet export dependency.

   ```bash
   pip install -e ".[export]"
   ```

   Verification: `python -c "import pandas, pyarrow"` succeeds. The `export`
   extra is `pandas` and `pyarrow` (`Verified from pyproject.toml:66-69`).

4. Install the bulk XBRL dependency (pandas and pyarrow for the Parquet
   cache).

   ```bash
   pip install -e ".[xbrl-bulk]"
   ```

   Verification: `python -c "import pandas, pyarrow"` succeeds
   (`Verified from pyproject.toml:70-73`).

The optional `langdetect` dependency for PDF language detection is already in
the core dependencies (`Verified from pyproject.toml:52`).

## 3. SurrealDB configuration

SurrealDB must be started with raised HTTP body limits before any ingestion
run. The defaults (4 MiB on `/rpc`, 1 MiB on `/sql`) reject large filings with
HTTP 413 (`Verified from README.md:47-50`; the default limits are also
documented in the `/rpc` helper docstring at `src/fin_pipeline/db/db.py:90`).

Set the two server environment variables with exact syntax:

```bash
SURREAL_HTTP_MAX_RPC_BODY_SIZE=128MiB
SURREAL_HTTP_MAX_SQL_BODY_SIZE=16MiB
```

The 128 MiB `/rpc` limit gives approximately 20× headroom over the largest
observed filing and 5× over the estimated worst case in the Forbes Global 2000
corpus; the 16 MiB `/sql` limit covers metadata-only writes
(`Verified from README.md:59-61`). The rationale for raising the limit via an
environment variable rather than splitting large payloads in application code
is documented in section 10.8 of the design rationale
(`Verified from docs/DESIGN_RATIONALE.md:757-772`): a single-record write
preserves transactional integrity and avoids reconstructing a filing from
fragments on read, while the in-process routing threshold
`_SQL_UPSERT_LIMIT_BYTES = 900_000` still sends statements larger than 900,000
bytes through `/rpc` (`Verified from src/fin_pipeline/db/connection.py:10,
117-126`).

Startup command with all flags:

```bash
SURREAL_HTTP_MAX_RPC_BODY_SIZE=128MiB \
SURREAL_HTTP_MAX_SQL_BODY_SIZE=16MiB \
SURREAL_LOG_FILE_ENABLED=true \
surreal start surrealkv:data/analytics_data --log info
```

(`Verified from README.md:52-57`.)

Verification that the limits took effect: confirm the server process has the
environment set and is serving on the configured endpoint.

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
```

Expected output: `200`. The endpoint defaults to `http://127.0.0.1:8000`
(`Verified from src/fin_pipeline/config/settings.py:53`). The body limits are
process-level settings, so the environment must be present on the process that
launched `surreal start`, not only in the pipeline's `.env`.

## 4. Pre-batch checklist

Run these verifications in order before starting a batch.

1. SurrealDB is reachable.

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
   ```

   Expected: `200`.

2. The body-limit environment variables are present in the server process
   (see section 3). Expected: the server was launched with the two
   `SURREAL_HTTP_MAX_*_BODY_SIZE` assignments.

3. `EDGAR_IDENTITY` is set, for SEC download paths.

   ```bash
   env | grep EDGAR_IDENTITY
   ```

   Expected: a non-empty value such as `Your Name your.email@example.com`.
   The stream path raises `RuntimeError` when neither
   `os.getenv("EDGAR_IDENTITY")` nor the `.env` value is set
   (`Verified from src/fin_pipeline/sec_edgar.py:289-293`).

4. Tesseract is installed (required for OCR on scanned PDFs).

   ```bash
   tesseract --version
   ```

   Expected: a version banner
   (`Verified from README.md:280-281`).

5. The GICS mapping file exists, for the `enrich` step.

   ```bash
   test -f data/industry_mapping.csv && echo present
   ```

   Expected: `present`. The default mapping path is
   `data/industry_mapping.csv` (`Verified from src/fin_pipeline/cli.py:359`).

6. The XBRL bulk cache is present, if ingestion should populate `xbrlFacts`.

   ```bash
   test -f xbrl_bulk/xbrl_facts.parquet && echo present
   ```

   Expected: `present`. The ingestion path loads this cache at
   `src/fin_pipeline/pipeline.py:98-119`.

7. Disk space is sufficient (see section 1). Expected: at least the observed
   4.6 GB for `xbrl_bulk/` plus 1.0 GB for the SurrealDB store, with headroom
   for growth.

8. Run a dry run over a single known-good input to validate end-to-end
   parsing without writes (see section 7).

## 5. CLI reference

All commands are registered on the `main` click group
(`Verified from src/fin_pipeline/cli.py:35`). Flags and defaults are extracted
verbatim from `cli.py`; none are invented.

### `scan`

Recursively scan a local folder for PDFs and ingest them.

```bash
fin-pipeline scan DIR_PATH [--source {LOCAL,SEC}] [--concurrency N]
    [--ocr/--no-ocr] [--dry-run] [--skip-existing] [--skip-by-hash]
```

| element | name | default | help |
| --- | --- | --- | --- |
| argument | `dir_path` | — | path, exists, dir only |
| option | `--source` / `-s` | `LOCAL` | Source metadata label (Choice LOCAL/SEC) |
| option | `--concurrency` / `-c` | `3` | Max simultaneous OCR processes |
| option | `--ocr/--no-ocr` | `True` | Enable or disable PDF OCR |
| option | `--dry-run` | flag | Parse and validate without database writes |
| option | `--skip-existing` | flag | Skip filings already in the DB by filingId |
| option | `--skip-by-hash` | flag | Skip files whose documentHash already exists |

(`Verified from src/fin_pipeline/cli.py:41-59`.)

Example:

```bash
fin-pipeline scan /path/to/financial_docs --source LOCAL --concurrency 4
```

Expected output: a `📂 Scanning target directory:` banner, per-filing log
lines, then `✨ Directory processing task successfully closed out.`
(`Verified from src/fin_pipeline/cli.py:70-82`).

### `scan-mixed`

Recursively scan a local folder for both PDFs and HTML files.

```bash
fin-pipeline scan-mixed DIR_PATH [--source {LOCAL,SEC}] [--concurrency N]
    [--ocr/--no-ocr] [--dry-run] [--skip-existing] [--skip-by-hash]
```

Flags and defaults are identical to `scan` except the concurrency help reads
"Max simultaneous parsing processes"
(`Verified from src/fin_pipeline/cli.py:86-104`).

Example:

```bash
fin-pipeline scan-mixed /path/to/documents --source LOCAL
```

Expected output: a `📂 Scanning mixed document directory:` banner and a
`✨ Mixed directory processing task successfully closed out.` completion line
(`Verified from src/fin_pipeline/cli.py:115-127`).

### `sec-edgar-html`

Process SEC EDGAR HTML filings from a local directory structure.

```bash
fin-pipeline sec-edgar-html DIR_PATH [--concurrency N] [--dry-run]
    [--skip-existing] [--skip-by-hash]
```

| element | name | default | help |
| --- | --- | --- | --- |
| argument | `dir_path` | — | path, exists, dir only |
| option | `--concurrency` / `-c` | `3` | Max simultaneous HTML processing tasks |
| option | `--dry-run` | flag | Parse and validate without database writes |
| option | `--skip-existing` | flag | Skip filings already in the DB by filingId |
| option | `--skip-by-hash` | flag | Skip files whose documentHash already exists |

(`Verified from src/fin_pipeline/cli.py:131-143`.)

Expected directory structure:
`{dir_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html`
(`Verified from src/fin_pipeline/cli.py:151-156`).

Example:

```bash
fin-pipeline sec-edgar-html /path/to/sec_filings --concurrency 4
```

Expected output: `✨ SEC Edgar HTML processing task successfully closed out.`

### `sec-edgar-csv`

Fetch SEC filings for ticker/CIK rows in a CSV and ingest them, writing the
downloaded HTML to disk.

```bash
fin-pipeline sec-edgar-csv CSV_PATH [--download-dir DIR] [--dry-run]
    [--skip-existing] [--skip-by-hash]
```

| element | name | default | help |
| --- | --- | --- | --- |
| argument | `csv_path` | — | path, exists, file only |
| option | `--download-dir` | `sec_downloads` | Directory for downloaded primary HTML documents |
| option | `--dry-run` | flag | Download, parse, and validate without database writes |
| option | `--skip-existing` | flag | Skip filings already in the DB by filingId |
| option | `--skip-by-hash` | flag | Skip files whose documentHash already exists |

(`Verified from src/fin_pipeline/cli.py:172-181`.)

Example:

```bash
fin-pipeline sec-edgar-csv ./companies.csv --download-dir ./sec_downloads
```

Expected output: `✨ SEC CSV processing task successfully closed out.` On
failure the command raises `ClickException("SEC CSV processing failed.")`
(`Verified from src/fin_pipeline/cli.py:200-202`).

### `sec-edgar-stream`

Fetch SEC filings from a CSV and ingest HTML directly from memory, without
writing files to disk.

```bash
fin-pipeline sec-edgar-stream CSV_PATH [--year-range START-END]
    [--forms LIST] [--dry-run] [--skip-existing] [--skip-by-hash]
```

| element | name | default | help |
| --- | --- | --- | --- |
| argument | `csv_path` | — | path, exists, file only |
| option | `--year-range` | None | Inclusive filing year range, for example 2018-2025 |
| option | `--forms` | None | Comma-separated SEC forms, for example 10-K,10-Q |
| option | `--dry-run` | flag | Fetch, parse, and validate without database writes |
| option | `--skip-existing` | flag | Skip filings already in the DB by filingId |
| option | `--skip-by-hash` | flag | Skip filings whose documentHash already exists |

(`Verified from src/fin_pipeline/cli.py:206-211`.)

`--year-range` is validated as `START-END` with `START <= END`
(`Verified from src/fin_pipeline/cli.py:222-232`); `--forms` is split on
commas, upper-cased, and requires at least one non-empty form
(`Verified from src/fin_pipeline/cli.py:233-241`).

Example:

```bash
fin-pipeline sec-edgar-stream ./companies.csv \
    --year-range 2018-2025 --forms 10-K,20-F,40-F --skip-existing
```

Expected output: `✨ SEC streaming task successfully closed out.`

### `file`

Ingest a single explicit file (PDF or HTML) with provided structured metadata.

```bash
fin-pipeline file FILE_PATH --filing-id ID --ticker T --type TYPE
    [--stock-name NAME] [--stock-code CODE] [--exchange X]
    [--source {SEC,LOCAL}] [--ocr/--no-ocr] [--dry-run]
    [--skip-existing] [--skip-by-hash] [--force]
```

| element | name | default | help |
| --- | --- | --- | --- |
| argument | `file_path` | — | path, exists, file only |
| option | `--filing-id` | required | Unique ID for target document record |
| option | `--ticker` | required | Company unique trading ticker symbol |
| option | `--stock-name` | None | Legal company name, when known |
| option | `--stock-code` | `UNKNOWN` | Stock identifier exchange code |
| option | `--exchange` | `UNKNOWN` | Name of the listing asset exchange platform |
| option | `--type` (`filing_type`) | required | Filing format label (e.g., 10-K, 20-F) |
| option | `--source` | `SEC` | Choice SEC/LOCAL |
| option | `--ocr/--no-ocr` | `True` | Enable or disable PDF OCR |
| option | `--dry-run` | flag | Parse and validate the file without writing to SurrealDB |
| option | `--skip-existing` | flag | Skip if filingId already exists in DB |
| option | `--skip-by-hash` | flag | Skip if documentHash already exists in DB |
| option | `--force` | flag | Re-ingest even when existing skip flags are set |

(`Verified from src/fin_pipeline/cli.py:259-286`.)

`--force` sets both skip flags to `False` (`Verified from
src/fin_pipeline/cli.py:313-315`).

Example:

```bash
fin-pipeline file ./0000320193-25-000079_aapl-20250927.pdf \
  --filing-id "sec_0000320193_2026_10K" --ticker "AAPL" \
  --stock-name "Apple Inc." --exchange "NASDAQ" --type "10-K" --source SEC
```

Expected output: `📥 Processing individual file:` then
`✨ Document registration sequence closed out.`

### `enrich`

Post-ingestion enrichment pass for GICS fields and `reportingYear`.

```bash
fin-pipeline enrich [--mapping PATH] [--dry-run] [--limit N]
    [--skip-industry] [--skip-year] [--backfill-cik] [--cik-csv PATH]
    [--diagnostic]
```

| element | name | default | help |
| --- | --- | --- | --- |
| option | `--mapping` / `-m` | `data/industry_mapping.csv` | Path to the industry_mapping.csv file |
| option | `--dry-run` | flag | Report what would change without writing to the database |
| option | `--limit` | None | Limit records scanned (for testing) |
| option | `--skip-industry` | flag | Skip GICS enrichment |
| option | `--skip-year` | flag | Skip reportingYear backfill |
| option | `--backfill-cik` | flag | Backfill missing CIKs from a company CSV |
| option | `--cik-csv` | None | CSV with Ticker and CIK columns |
| option | `--diagnostic` | flag | Print low-quality extraction counts (read-only) |

(`Verified from src/fin_pipeline/cli.py:359-366`.)

`--cik-csv` is required when `--backfill-cik` is set
(`Verified from src/fin_pipeline/cli.py:390-391`).

Example:

```bash
fin-pipeline enrich --mapping data/industry_mapping.csv
```

Expected output: `Loaded mapping: N rows (...)`, `📊 Industry enrichment:`,
`📅 reportingYear backfill:`, then `✨ Enrichment complete`.

### `filter-preferred`

Filter preferred-share tickers from a CSV while preserving dual-class common
shares.

```bash
fin-pipeline filter-preferred --input CSV [--output CSV]
    [--delete-ingested] [--dry-run]
```

| element | name | default | help |
| --- | --- | --- | --- |
| option | `--input` / `-i` | required | Input CSV (e.g. sec_companies.csv) |
| option | `--output` / `-o` | None | Output CSV path |
| option | `--delete-ingested` | flag | Also delete preferred-share records when a plain sibling remains |
| option | `--dry-run` | flag | Report what would change without writing files or database records |

(`Verified from src/fin_pipeline/cli.py:465-483`.)

When `--output` is omitted it defaults to
`{input.removesuffix('.csv')}_clean.csv` (`Verified from
src/fin_pipeline/cli.py:496-497`).

Example:

```bash
fin-pipeline filter-preferred --input sec_companies.csv --dry-run
```

Expected output: `🔍 Preferred-share filter (DRY RUN):` with input/output row
counts, `Dropped`, and `Kept (flag)` counts.

### `export`

Export filing records and document chunks to Parquet.

```bash
fin-pipeline export --output-dir DIR [--source-type TYPE]
    [--company-ticker TICKER] [--filing-date-from YYYY-MM-DD]
    [--filing-date-to YYYY-MM-DD]
```

| element | name | default | help |
| --- | --- | --- | --- |
| option | `--output-dir` | required | Directory for filings.parquet and chunks.parquet |
| option | `--source-type` | None | Filter by source classification (Choice, case-insensitive) |
| option | `--company-ticker` | None | Filter by exact company ticker |
| option | `--filing-date-from` | None | Inclusive filing date start (YYYY-MM-DD) |
| option | `--filing-date-to` | None | Inclusive filing date end (YYYY-MM-DD) |

(`Verified from src/fin_pipeline/cli.py:560-575`.)

`--source-type` accepts `structured_filing`, `glossy_report`, or `unknown`,
case-insensitively (`Verified from src/fin_pipeline/cli.py:566-572`).

Example:

```bash
fin-pipeline export --output-dir ./export
```

Expected output: `Exported filing and chunk Parquet files to <dir>`
(`Verified from src/fin_pipeline/cli.py:598-600`).

### `xbrl-bulk`

Download SEC bulk XBRL data and write normalized facts to Parquet.

```bash
fin-pipeline xbrl-bulk [--download-dir DIR] [--start-year Y] [--end-year Y]
    [--skip-companyfacts] [--cik-csv PATH] [--force]
```

| element | name | default | help |
| --- | --- | --- | --- |
| option | `--download-dir` | `xbrl_bulk` | Directory for companyfacts.zip and FSDS ZIPs |
| option | `--start-year` | `2018` | (no help text) |
| option | `--end-year` | `2025` | (no help text) |
| option | `--skip-companyfacts` | flag | Skip companyfacts.zip download/parse (use FSDS only) |
| option | `--cik-csv` | None | CSV with a 'cik' column. If omitted, parse all CIKs in the bulk data |
| option | `--force` | flag | Re-download archives, ignoring checkpoints |

(`Verified from src/fin_pipeline/cli.py:604-624`.)

Example:

```bash
fin-pipeline xbrl-bulk --start-year 2018 --end-year 2025 --cik-csv companies.csv
```

Expected output: `✅ xbrl-bulk complete:` with `Filings processed`, `Total
facts`, `Output Parquet`, and `Runtime` (`Verified from
src/fin_pipeline/cli.py:766-772`).

## 6. CSV input format

### `sec-edgar-stream` and `sec-edgar-csv`

The row reader is `read_company_csv` (`Verified from
src/fin_pipeline/sec_edgar.py:187-204`).

- **Required columns.** At least one of `ticker` or `cik` per row; a row with
  neither raises `ValueError("CSV row <n> requires ticker or cik")`
  (`Verified from src/fin_pipeline/sec_edgar.py:202-203`).
- **Optional columns.** `forms` (or `form`), `year`, and `max_filings`
  (`Verified from src/fin_pipeline/sec_edgar.py:190-191`). `industry` is read
  into `industryCodeRaw` (`Verified from src/fin_pipeline/sec_edgar.py:421`).
- **Case sensitivity.** Column names are stripped and lower-cased before
  lookup, so `Ticker`, `TICKER`, and `ticker` are equivalent
  (`Verified from src/fin_pipeline/sec_edgar.py:198-200`); cell values are
  stripped of whitespace.
- **Missing columns.** A file with no header row raises
  `ValueError("CSV must include a header row")`
  (`Verified from src/fin_pipeline/sec_edgar.py:195-196`). An absent row-level
  `forms`/`year`/`max_filings` falls back to `None`
  (`Verified from src/fin_pipeline/sec_edgar.py:207-223`).

Example rows:

```csv
ticker,forms,year,max_filings
AAPL,"10-K,10-Q",2024,2
```

A CIK-only row uses a `cik` column instead:

```csv
cik
1652044
```

For a CIK-only row the stream path resolves the ticker from the SEC company
record (`Verified from src/fin_pipeline/sec_edgar.py:317-324`).

CLI `--year-range` and `--forms` override row-level filters
(`Verified from src/fin_pipeline/sec_edgar.py:325-330`).

### `xbrl-bulk` (`--cik-csv`)

The CIK CSV must contain a `cik` column, matched case-insensitively
(`Verified from src/fin_pipeline/utils/xbrl_bulk.py:760-775`). Missing the
column raises `ValueError("CIK CSV must include a 'cik' column")`
(`Verified from src/fin_pipeline/utils/xbrl_bulk.py:764-767`).

### `enrich` (`--cik-csv`)

The backfill CSV must contain both `Ticker` and `CIK` columns
(`Verified from src/fin_pipeline/enrichment.py:242-243`); missing them raises
`ValueError("CIK CSV must include Ticker and CIK columns")`.

## 7. Typical batch workflow

The following end-to-end example assumes the Forbes Global 2000 SEC subset.
Each timing figure is an estimate unless cited to an observed log.

1. Start SurrealDB with the raised body limits (section 3).
2. Build the XBRL bulk cache. Estimated at tens of minutes to hours depending
   on network bandwidth; the developer run downloaded 4.6 GB of archives.

   ```bash
   fin-pipeline xbrl-bulk --start-year 2018 --end-year 2025 --cik-csv sec.csv
   ```

3. Stream SEC filings into SurrealDB, resumably.

   ```bash
   fin-pipeline sec-edgar-stream ./sec.csv \
     --year-range 2018-2025 --forms 10-K,20-F,40-F --skip-existing
   ```

   Estimated per-filing wall time is sub-second to ~1 second for HTML (see
   section 8).

4. Enrich GICS codes and backfill `reportingYear`.

   ```bash
   fin-pipeline enrich --mapping data/industry_mapping.csv
   ```

5. Verify the batch (section 9).
6. Export for downstream analysis.

   ```bash
   fin-pipeline export --output-dir ./export
   ```

## 8. Timing and resource estimates

### Per-filing time

No benchmark is checked into the repository, so per-type wall-clock figures
are not formally specified (`Verified from docs/DESIGN_RATIONALE.md:630-631`,
which declines to claim resume-vs-cold-start latency for this reason). Two
observed values exist in the developer's metrics log:

- **SEC HTML streamed filing**: approximately 0.12–1.08 seconds per filing,
  observed for real accessions such as `sec_TRPRF_0001232384-24-000015`
  (0.12 s), `sec_CVE_0001475260-24-000006` (0.30 s), and
  `sec_AAPL_0000320193-23-000106` (1.08 s)
  (`Verified from logs/pipeline_metrics.json`).
- **PDF via the `file` command**: approximately 13.4 seconds for a 697,442-byte
  AAPL 10-K (`0000320193-25-000079_aapl-20250927.pdf`), observed as
  `sec_0000320193_2026_10K` at 13.35–13.73 s
  (`Verified from logs/pipeline_metrics.json`).

These are point observations, not throughput guarantees; PDF time is dominated
by layout parsing and table extraction and scales with page count and OCR
load.

### Batch duration

Not measured in the repository. As an estimate: a batch of N SEC HTML filings
at the observed ~0.1–1.1 s per filing (parsing plus persistence) would run
roughly N × 1 second plus network latency and edgartools lookups. A batch of N
local PDFs at the observed ~13 s per PDF (with OCR) would run roughly N × 13
seconds divided by the `--concurrency` worker count (default 3). These are
extrapolations from the single observations above, not benchmarks.

### Disk usage growth

`xbrl_bulk/` grows by `companyfacts.zip` (≈1.41 GB) plus one FSDS archive per
quarter (≈63–128 MB each, 32 archives for 2018–2025), plus the ≈22 MB
`xbrl_facts.parquet` (section 1). The SurrealDB store holds ingested records;
its size is proportional to the number and text volume of filings.

### Memory usage per worker

No per-worker memory figure is documented in the repository. Each worker holds
one document's extracted Markdown text, chunks, and tables in memory during
parsing, and `--concurrency` (default 3) bounds the number of concurrent
workers. <!-- TODO: verify per-worker memory usage against a profiled run;
no memory measurement exists in the repository. -->

## 9. Post-batch verification

Run these SurrealQL queries through the `/sql` endpoint (or the `surreal sql`
CLI) after a batch. Field names are verified against the schema DDL in
`src/fin_pipeline/db/db.py:205-271`.

1. **Coverage query** (companies, filings).

   ```surrealql
   SELECT COUNT() AS filings, COUNT(companyTicker) AS with_ticker,
          COUNT(cik) AS with_cik FROM exchange_filing;
   ```

   Expected: a single row. Interpretation: `filings` is total records;
   `with_ticker` and `with_cik` should be non-zero after an SEC batch. `cik`
   and `companyTicker` are schema fields
   (`Verified from src/fin_pipeline/db/db.py:209, 211`).

2. **Filing type distribution.**

   ```surrealql
   SELECT filingType, COUNT() AS count FROM exchange_filing
     GROUP BY filingType ORDER BY count DESC;
   ```

   Expected: one row per form type (e.g. `10-K`, `20-F`, `40-F`, `8-K`).
   `filingType` is a schema field
   (`Verified from src/fin_pipeline/db/db.py:218`).

3. **Year distribution.**

   ```surrealql
   SELECT reportingYear, COUNT() AS count FROM exchange_filing
     GROUP BY reportingYear ORDER BY reportingYear;
   ```

   Expected: one row per year. `reportingYear` is a schema field
   (`Verified from src/fin_pipeline/db/db.py:241`). Rows with
   `reportingYear IS NONE` are present until the `enrich` backfill runs.

4. **Missing CIK count.**

   ```surrealql
   SELECT COUNT() AS missing_cik FROM exchange_filing WHERE cik IS NONE;
   ```

   Expected: a small or zero count after enrichment; the backfill query in
   `enrichment.py` targets exactly this condition (`cik IS NONE`,
   `Verified from src/fin_pipeline/enrichment.py:274`).

5. **Large filings check.** The requested field `chunks_bytes` does not exist
   in the schema; there is no `chunks_bytes` field in
   `src/fin_pipeline/db/db.py` or `src/fin_pipeline/models/schemas.py`. The
   equivalent large-filing checks use `documentSize` (source file bytes,
   `Verified from src/fin_pipeline/db/db.py:231`) and `documentTextLen`
   (extracted text characters, `Verified from src/fin_pipeline/db/db.py:235`).

   ```surrealql
   SELECT filingId, documentSize, documentTextLen, documentTableCnt
     FROM exchange_filing
     ORDER BY documentSize DESC LIMIT 20;
   ```

   Expected: the largest source files first. `documentSize` is populated from
   `os.path.getsize(file_path)` (`Verified from src/fin_pipeline/pipeline.py:386`);
   `documentTextLen` from `len(parsed["text"])`
   (`Verified from src/fin_pipeline/pipeline.py:388`).

6. **Duplicate detection.**

   ```surrealql
   SELECT filingId, COUNT() AS n FROM exchange_filing
     GROUP BY filingId HAVING n > 1;
   ```

   Expected: an empty result. `filingId` is deterministic by construction
   (`Verified from docs/DESIGN_RATIONALE.md:606-611`), and the write path
   deletes the stale record before upserting
   (`Verified from src/fin_pipeline/pipeline.py:480-481`), so duplicates should
   not occur.

## 10. Environment variables reference

Pipeline-side variables are read by `settings.py` and `logger.py`.
`constants.py` defines no environment variables
(`Verified from src/fin_pipeline/config/constants.py`; the fact is also
recorded at `docs/_FACTS.md:11-12`). The two body-limit variables are read by
the SurrealDB server process, not the pipeline
(`Verified from docs/DESIGN_RATIONALE.md:570-572`).

| name | default | required | purpose | example value |
| --- | --- | --- | --- | --- |
| `SURREAL_ENDPOINT` | `http://127.0.0.1:8000` | no | SurrealDB HTTP endpoint | `http://127.0.0.1:8000` |
| `SURREAL_USER` | `root` | no | SurrealDB username | `root` |
| `SURREAL_PASS` | `secret` | no | SurrealDB password | `secret` |
| `SURREAL_NAMESPACE` | `finance` | no | SurrealDB namespace | `finance` |
| `SURREAL_DATABASE` | `analytics` | no | SurrealDB database | `analytics` |
| `COMPANY_TABLE` | (empty) | no | company table name; enables graph edge tables when non-empty | `company` |
| `EDGAR_IDENTITY` | (empty) | no (required for SEC downloads) | SEC User-Agent identity | `Your Name you@example.com` |
| `FIN_PIPELINE_LOG_LEVEL` | `INFO` | no | Loguru log level | `INFO` |
| `FIN_PIPELINE_LOG_DIR` | `logs` | no | Directory for JSON logs | `logs` |
| `SURREAL_HTTP_MAX_RPC_BODY_SIZE` | (SurrealDB default 4 MiB) | **yes** (server) | Max body size on `/rpc` | `128MiB` |
| `SURREAL_HTTP_MAX_SQL_BODY_SIZE` | (SurrealDB default 1 MiB) | **yes** (server) | Max body size on `/sql` | `16MiB` |

Sources: `SURREAL_*`, `COMPANY_TABLE`, `EDGAR_IDENTITY` at
`Verified from src/fin_pipeline/config/settings.py:53-59`; `FIN_PIPELINE_LOG_*`
at `Verified from src/fin_pipeline/config/logger.py:8-9`; the two server
variables at `Verified from README.md:299-300`. The defaults for the
connection variables are the `get_env` fallbacks in
`Verified from src/fin_pipeline/config/settings.py:53-57`. `EDGAR_IDENTITY` is
required only for SEC download paths
(`Verified from src/fin_pipeline/sec_edgar.py:289-293`). `get_env` gives OS
environment variables precedence over `.env` values
(`Verified from src/fin_pipeline/config/settings.py:48-50`).

## Appendix A — Worked example: one filing

The preferred GOOG trace could not be completed with a real accession: the
accession `sec_GOOG_0001652044-24-000022` does not exist in the repository,
and the XBRL tests contain synthetic fixtures rather than hardcoded GOOG
financials (`Verified from docs/DESIGN_RATIONALE.md:443-452`). GOOG's
verifiable values are limited to its CIK and GICS mapping: CIK `0001652044`
and GICS code `45102010` ("Interactive Media & Services"), from
`data/industry_mapping.csv:3`. The following trace instead uses the AAPL 10-K
whose expected values are asserted in the test suite.

Input CSV row (`tests/test_sec_edgar.py:47`):

```csv
ticker,forms,year
AAPL,8-K,2020
```

CLI flags override the row filters to `forms=["10-K","10-Q"]` and
`year=list(range(2018,2026))` (`tests/test_sec_edgar.py:73-78`). edgartools
returns a filing with `accession_no="0000320193-25-000079"`,
`form="10-K"`, `company="Apple Inc."`, `report_date="2025-09-27"`,
`filing_date="2025-11-01"`, `cik=320193` (`tests/test_sec_edgar.py:48-56`).

Expected metadata built by `_stream_filings_from_csv`
(`tests/test_sec_edgar.py:79-86`, `Verified from src/fin_pipeline/sec_edgar.py:402-425`):

- `filingId = "sec_AAPL_0000320193-25-000079"`
- `sourceType = "structured_filing"` (10-K is in `_STRUCTURED_FORMS`,
  `Verified from src/fin_pipeline/sec_edgar.py:22`)
- `reportingYear = 2025`, `periodEndDate = "2025-09-27"`,
  `fiscalYearEndMonth = 9`
- `isAmendment = False`, `language = "en"`, `pageCount = None`
- `cik = "0000320193"` (zero-padded to 10 by
  `Verified from src/fin_pipeline/models/schemas.py:80-88`; asserted at
  `tests/test_sec_edgar.py:114`)

Expected log lines: a `🚀 Ingestion request initialized for filing ID:
sec_AAPL_0000320193-25-000079` line
(`Verified from src/fin_pipeline/pipeline.py:327-329`) and, on success,
`🏆 Streamed filing exchange_filing:sec_AAPL_0000320193-25-000079 into
SurrealDB in <n>s` (`Verified from src/fin_pipeline/pipeline.py:646-648`).

Expected DB record shape (key fields, all verified against
`Verified from src/fin_pipeline/db/db.py:208-258`):

```text
filingId: "sec_AAPL_0000320193-25-000079"
companyTicker: "AAPL"
stockCode: "UNKNOWN"          (Verified from src/fin_pipeline/sec_edgar.py:405)
exchange: "UNKNOWN"           (Verified from src/fin_pipeline/sec_edgar.py:406)
filingType: "10-K"
sourceType: "structured_filing"
reportingYear: 2025
periodEndDate: "2025-09-27T00:00:00Z"   (Verified from src/fin_pipeline/models/schemas.py:99-106)
fiscalYearEndMonth: 9
isAmendment: false
language: "en"
cik: "0000320193"
documentType: "HTML"          (asserted at tests/test_pipeline.py:584)
```

`documentChunks` decodes to a list whose first element has
`chunkIndex: 0, pageNumber: 1, sectionLabel: "business"`
(`tests/test_pipeline.py:576-588`). With a populated bulk cache, `xbrlFacts`
decodes to a concept-to-value map; the test fixture asserts
`{"assets": 42}` with `xbrlSource="bulk"` and `xbrlCurrency="USD"`
(`tests/test_pipeline.py:557-563, 589`).

## Appendix B — Glossary

- **filing.** A single submitted disclosure document ingested as one
  `exchange_filing` record. Example: an Apple Inc. Form 10-K for fiscal 2025.
- **accession.** The SEC accession number uniquely identifying a filing, in
  the form `CCCCCCCCCC-YY-NNNNNN`. Example: `0000320193-25-000079`
  (`src/fin_pipeline/sec_edgar.py:382`).
- **chunk.** A contiguous text segment, either a PDF page or an HTML
  pseudo-page, stored in `documentChunks`. Example: a `DocumentChunkSchema`
  with `chunkIndex`, `pageNumber`, `sectionLabel`, and `text`
  (`src/fin_pipeline/models/schemas.py:18-24`).
- **section.** One of six canonical region labels applied to a chunk:
  `business`, `risk_factors`, `mdna`, `financial_statements`,
  `corporate_governance`, or `other`
  (`docs/DESIGN_RATIONALE.md:351-361`).
- **exhibit.** An attachment to a filing. Example: a Form 40-F's `EX-99.1`
  exhibit, concatenated into the document body (`src/fin_pipeline/sec_edgar.py:144-184`).
- **provenance.** A record of where a value came from, stored in
  `metadataSources`, `metadataConfidence`, `xbrlProvenance`, and `xbrlSource`.
  Example: `xbrlSource = "bulk"` marks facts sourced from the bulk Parquet
  cache (`src/fin_pipeline/pipeline.py:159`).
- **canonical concept.** One of 17 normalized financial line items in
  `CANONICAL_CONCEPTS` (e.g. `revenues`, `assets`, `netIncomeLoss`)
  (`src/fin_pipeline/utils/xbrl_bulk.py:42-116`).
- **source type.** The filing's classification: `structured_filing`,
  `glossy_report`, or `unknown` (`src/fin_pipeline/models/schemas.py:45`). Example: a 10-K is
  `structured_filing`; an annual report PDF is `glossy_report`.
- **source tag.** The `(namespace, tag)` pair a fact came from. Example:
  `(us-gaap, Revenues)` (`src/fin_pipeline/utils/xbrl_bulk.py:43-50`).
- **dimension.** An XBRL axis, member, or segment that subdivides a fact; the
  pipeline rejects dimensional facts via `_is_consolidated_fact`
  (`src/fin_pipeline/utils/xbrl_bulk.py:183-192`).
- **instant fact.** A point-in-time fact (a balance-sheet item), assigned kind
  `instant` in `CONCEPT_KIND`. Example: `assets` (`src/fin_pipeline/utils/xbrl_bulk.py:117-135`).
- **duration fact.** A period fact (an income-statement item), assigned kind
  `duration` in `CONCEPT_KIND`. Example: `revenues` (`src/fin_pipeline/utils/xbrl_bulk.py:117-135`).
- **IXBRL.** Inline XBRL, XBRL data embedded in HTML. Example:
  `<ix:nonNumeric name="dei:EntityRegistrantName">Apple Inc.</ix:nonNumeric>`
  (`tests/test_pipeline.py:577`).
- **FSDS.** The SEC Financial Statement Data Sets, quarterly numeric archives
  downloaded as `{year}q{quarter}.zip`. Example: `2024q1.zip`
  (`src/fin_pipeline/utils/xbrl_bulk.py:24-27`).
- **GICS.** The Global Industry Classification Standard; an 8-digit industry
  code plus a 2-digit sector. Example: `45202030` (Technology Hardware Storage
  & Peripherals, sector `45`) from `data/industry_mapping.csv:2`.
- **CIK.** Central Index Key, the SEC's 10-digit, zero-padded company
  identifier. Example: `0000320193` for Apple
  (`src/fin_pipeline/models/schemas.py:80-88`).
- **LEI.** Legal Entity Identifier, a 20-character alphanumeric code matched by
  `LEI_PATTERN` (`[0-9A-Z]{20}`) (`src/fin_pipeline/config/constants.py:142`).
- **ISIN.** International Securities Identification Number, a 12-character code
  matched by `ISIN_PATTERN` (`[A-Z]{2}[A-Z0-9]{9}\d`) (`src/fin_pipeline/config/constants.py:143-145`).
  No ISIN value is hardcoded in the repository.

## Appendix C — Verification query library

Field names are cited from the schema DDL
(`src/fin_pipeline/db/db.py:205-271`). JSON-string fields must be decoded
with `encoding::json::decode(...)` before inspection
(`docs/DESIGN_RATIONALE.md:664-666`).

Check a specific filing:

```surrealql
SELECT * FROM exchange_filing WHERE filingId = 'sec_AAPL_0000320193-25-000079';
```

Count records by source type:

```surrealql
SELECT sourceType, COUNT() AS count FROM exchange_filing
  GROUP BY sourceType;
```

(`sourceType` at `db.py:240`.)

Find records missing a field:

```surrealql
SELECT COUNT() AS missing FROM exchange_filing WHERE gicsCode IS NONE;
```

(`gicsCode` at `db.py:248`.)

Inspect a JSON-string field:

```surrealql
SELECT filingId, encoding::json::decode(documentChunks) AS chunks
  FROM exchange_filing WHERE filingId = 'sec_AAPL_0000320193-25-000079'
  LIMIT 1;
```

(`documentChunks` at `db.py:254`.)

Sample random records:

```surrealql
SELECT * FROM exchange_filing ORDER BY rand() LIMIT 5;
```
