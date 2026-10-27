# Design rationale

## 1. Overview

`fin_pipeline` is a research-grade ingestion pipeline that converts financial
disclosures — SEC EDGAR filings and company-published "glossy" annual reports —
into a single, queryable SurrealDB corpus with page-level text chunks, section
labels, extracted tables, normalized metadata, and XBRL-derived financial
facts. The pipeline targets the Forbes Global 2000 corpus of 2,000 public
companies for fiscal years 2018 through 2025, verified from `README.md:7-13`
and the corpus manifest `FG2K2025.csv` (2,000 data rows after removing one
trailing blank row; computed below in section 2). It serves three audiences:
maintainers who extend the extraction and persistence layers, paper reviewers
who audit the method, and evaluators deciding whether the pipeline fits their
corpus.

The required structure calls for a table of six AI indices. Those indices are
not named anywhere in the repository, so their abbreviations, full names, and
primary data sources could not be verified from source; the table below is a
placeholder.

<!-- TODO: verify the six AI indices (abbreviation, full name, primary data
     source) against the paper's index definitions. No file in the repository
     names them; grep for "AI index", "indices", and the individual index
     abbreviations returns only false positives such as "Wesfarmers" and
     "Qantas Airways". -->

| abbreviation | full name | primary data source |
| --- | --- | --- |
| TODO | TODO | TODO |
| TODO | TODO | TODO |
| TODO | TODO | TODO |
| TODO | TODO | TODO |
| TODO | TODO | TODO |
| TODO | TODO | TODO |

Scope limitations stated up front: the corpus is confined to the largest 2,000
public companies, which over-represents large-cap firms and under-represents
small-cap and private companies; XBRL facts are restricted to USD-denominated
consolidated facts, so non-USD filers have empty `xbrlFacts` (section 10.7);
and the 40-F form is ingested as concatenated exhibit text rather than its
native structured layout (section 10.6).

## 2. Corpus selection and composition

### Why the Forbes Global 2000

The corpus uses the Forbes Global 2000 rather than the Fortune 500, the
S&P Global 1200, or the MSCI ACWI. Forbes ranks public companies on four
equally weighted fundamentals — sales, profits, assets, and market value —
rather than on market capitalization alone; the 2025 methodology and cutoffs
are published at
[Forbes, "2025 Global 2000 Methodology"](https://www.forbes.com/sites/andreamurphy/2025/05/27/2025-global-2000-methodology/)
(sales cutoff $5.9 B, profits $399 M, assets $14.1 B, market value $7.9 B).
The same four columns — `Sales`, `Profit`, `Assets`, `Market Value` — appear
verbatim in `FG2K2025.csv`, corroborating that the repository manifest is the
Forbes 2025 list.

The three alternates were rejected for coverage reasons. The Fortune 500 is
US-only (500 companies), which cannot support a cross-country analysis of
disclosure regimes. The S&P Global 1200 and MSCI ACWI are investable-index
constituent lists driven by float-adjusted market capitalization and index
eligibility rules, which exclude many large companies (for example, closely
held or low-float firms) and do not provide the four fundamental metrics the
Forbes list does. The Forbes Global 2000 gives the widest cross-section of
"large" companies across 63 countries and 31 industries (computed below),
which is the appropriate population for studying how disclosure format
(structured SEC filing versus glossy report) varies with firm size, country,
and sector. These comparisons are reasoned rationale, not claims verified
against the index providers' own documentation.

### Country distribution

`FG2K2025.csv` (repository root, 2,000 companies) carries a `Headquarters`
column of the form `"City, Country"`. Parsing the country as the final
comma-separated token yields the following top-10 distribution.

| rank | country | count | percentage |
| --- | --- | --- | --- |
| 1 | United States | 595 | 29.75% |
| 2 | China | 300 | 15.00% |
| 3 | Japan | 178 | 8.90% |
| 4 | South Korea | 66 | 3.30% |
| 5 | Canada | 64 | 3.20% |
| 6 | India | 64 | 3.20% |
| 7 | United Kingdom | 63 | 3.15% |
| 8 | Germany | 50 | 2.50% |
| 9 | Switzerland | 46 | 2.30% |
| 10 | France | 44 | 2.20% |

France ties with Taiwan (44, 2.20%); both are shown to keep the ranking
faithful. Computed from `FG2K2025.csv` via a country parse of the
`Headquarters` field; 63 distinct countries result.

### Industry distribution

The same file's `Industry` column produces the top-10 industry distribution.

| rank | industry | count | percentage |
| --- | --- | --- | --- |
| 1 | Banking | 314 | 15.70% |
| 2 | Diversified Financials | 136 | 6.80% |
| 3 | Construction | 117 | 5.85% |
| 4 | Insurance | 112 | 5.60% |
| 5 | Materials | 95 | 4.75% |
| 6 | Capital Goods | 94 | 4.70% |
| 7 | Utilities | 93 | 4.65% |
| 8 | Oil & Gas Operations | 91 | 4.55% |
| 9 | IT Software & Services | 84 | 4.20% |
| 10 | Consumer Durables | 83 | 4.15% |

Computed from `FG2K2025.csv`; 31 distinct industries result. Financial
sectors (banking, diversified financials, insurance) together account for
roughly 28% of the corpus.

### Fiscal year end distribution

The proportion of calendar-year versus non-December filers cannot be computed
from the repository. The corpus manifests record no fiscal-year-end field, and
no ingested-corpus snapshot (a populated SurrealDB export or Parquet) is
checked in.

<!-- TODO: verify the calendar-year versus non-December filer proportion
     against an ingested corpus snapshot. `FG2K2025.csv` and `sec.csv` carry
     no fiscal-year-end column. -->

The pipeline records `fiscalYearEndMonth` per filing at ingestion time
(`sec_edgar.py:391-395` from `filing.fiscal_year_end`, falling back to
`periodEndDate`; `schemas.py:49`). The README documents that non-December
fiscal years — Japanese and Indian March filers, Australian June filers, and
UK April filers — are a known source of `periodEndDate` error in the PDF path
(`README.md:623-627`), but this is a qualitative limitation, not a measured
distribution.

### Structured versus glossy split

The corpus splits into SEC filers and non-SEC ("glossy") filers. `sec.csv`
(repository root) is the SEC-filer subset of the Forbes Global 2000 and holds
914 companies. Dividing by the 2,000-company manifest:

- SEC filers: 914 / 2,000 = 45.70%
- Glossy (non-SEC): 1,086 / 2,000 = 54.30%

This is the derivation behind the "~46% SEC, ~54% glossy" figure cited in the
task brief, computed from `FG2K2025.csv` (2,000 rows) and `sec.csv` (914 rows).
The mapping of "SEC filer" to `sourceType = "structured_filing"` and
"non-SEC" to `sourceType = "glossy_report"` follows `classify.py:1-37`, which
classifies SEC forms (10-K, 10-Q, 8-K, 20-F, 40-F, S-1, and so on) as
`structured_filing` and annual/integrated/sustainability/ESG reports as
`glossy_report`.

### Selection bias

The split above is not geographically uniform. The United States is 29.75% of
the full corpus but 64.33% of SEC filers (588 of 914, computed from `sec.csv`).
SEC filers therefore skew larger, more US-centric, and toward firms subject to
standardized, machine-readable disclosure, while glossy filers concentrate in
markets where the annual report is the primary filing. This is a documented
limitation: results drawn from `structured_filing` records generalize to
US-listed and cross-listed large firms, not to the full Global 2000.
This limitation is restated in section 11.

## 3. Architecture

### Data flow

```text
                         ┌────────────────────────────────────────────┐
                         │                entry points                │
                         │  cli.py (click commands, verified below)   │
                         └───────┬───────────────┬────────────────────┘
                                 │               │
        ┌────────────────────────┼───┐      ┌────┴──────────────────────────┐
        │  local / SEC-dir path      │      │  SEC streaming (no disk write) │
        │  crawler.py               │      │  sec_edgar.py                  │
        │  scan_directory /          │      │  _stream_filings_from_csv      │
        │  scan_documents /          │      │  (edgartools Company)          │
        │  scan_sec_edgar_html_dir   │      │                                │
        └────────────┬───────────────┘      └────────────┬───────────────────┘
                     │        (file_path, metadata)      │ (html_content, metadata)
                     └────────────────┬──────────────────┘
                                      ▼
                    run_ingestion_pipeline / run_html_content_pipeline
                    (pipeline.py — orchestration)
                                      │
              ┌───────────────────────┼──────────────────────────┐
              ▼                       ▼                          ▼
      parse_document()        label_sections_*()        _populate_xbrl_fields()
      pdf_parser /             sections.py               pipeline.py (bulk cache)
      html_parser                                        xbrl_bulk.py Parquet
              │                       │                          │
              └───────────────┬───────┴──────────────────────────┘
                              ▼
              ExchangeFilingModel(**payload)  (schemas.py)
                              │
                              ▼
              SurrealConnection.upsert  (connection.py)
              DELETE then UPSERT; graph relations (relations.py)
                              │
                              ▼
                        SurrealDB  (db.py schema DDL)
```

### Module map

| module | responsibility | public entry points | source |
| --- | --- | --- | --- |
| `pipeline.py` | orchestration: parse, validate, persist; resume pre-load; XBRL cache lookup | `run_ingestion_pipeline`, `run_html_content_pipeline`, `process_entire_directory`, `process_mixed_directory`, `process_sec_edgar_html_directory`, `process_sec_edgar_csv`, `process_sec_edgar_csv_stream`, `parse_document` | pipeline.py:241, 274, 509, 655, 718, 776, 821, 858 |
| `sec_edgar.py` | SEC client: CSV reader, edgartools retrieval, 40-F exhibit concatenation | `read_company_csv`, `stream_filings_from_csv`, `fetch_filings_from_csv`, `_stream_filings_from_csv` | sec_edgar.py:187, 281, 451, 471 |
| `crawler.py` | local filesystem scanning and filename metadata inference | `scan_directory`, `scan_documents`, `scan_sec_edgar_html_directory` | crawler.py:212, 305, 403 |
| `utils/pdf_parser.py` | PDF → Markdown, tables, metadata | `parse_pdf_layout`, `extract_filing_metadata` | pdf_parser.py:108, 526 |
| `utils/html_parser.py` | HTML/Inline XBRL → text, tables, metadata | `parse_html_content`, `parse_html_file`, `extract_filing_metadata`, `enrich_filing_metadata_with_edgartools` | html_parser.py:53, 145, 326, 340 |
| `utils/sections.py` | section labeling for structured and glossy documents | `label_sections_structured`, `label_sections_glossy` | sections.py:192, 229 |
| `utils/xbrl_bulk.py` | bulk XBRL download and Parquet fact extraction | `download_companyfacts`, `download_fsds_range`, `parse_companyfacts_zip`, `parse_fsds_zip`, `parquet_columns` | xbrl_bulk.py:460, 495, 517, 677, 778 |
| `utils/xbrl_facts.py` | per-filing Inline XBRL fact extractor (fallback, unused) | `extract_xbrl_facts` | xbrl_facts.py:73 |
| `utils/classify.py` | filing type → `sourceType` | `classify_source_type`, `normalize_filing_type` | classify.py:1, 40 |
| `utils/metadata.py` | regex metadata extraction shared by PDF and HTML paths | `extract_metadata_from_text`, `merge_metadata_candidates` | metadata.py:141, 210 |
| `utils/crypto.py` | SHA-256 file hashing | `calculate_file_hash` | crypto.py:4 |
| `db/db.py` | schema DDL and raw SurrealQL helpers | `_build_schema_sql`, `initialize_schema`, `surreal_query`, `surreal_rpc`, `upsert_batch_with_retry` | db.py:51, 87, 149, 191, 289 |
| `db/connection.py` | HTTP adapter: delete, upsert, merge, JSON serialization | `SurrealConnection`, `_HttpSurrealConnection` | connection.py:84, 165 |
| `db/relations.py` | company/filing graph edges | `ensure_company_exists`, `establish_graph_relations` | relations.py:34, 131 |
| `db/connection_pool.py` | pooled connection (optional) | `SurrealConnectionPool` | connection_pool.py:11 |
| `models/schemas.py` | Pydantic validation and field defaults | `ExchangeFilingModel`, `DocumentChunkSchema`, `DocumentTableSchema` | schemas.py:7, 18, 27 |
| `enrichment.py` | post-ingestion GICS mapping and backfills | `load_industry_mapping`, `enrich_industry_codes`, `backfill_reporting_year`, `backfill_cik_from_csv` | enrichment.py:10, 101, 194, 267 |
| `preferred_filter.py` | preferred-share ticker filtering | `filter_csv`, `delete_ingested_preferred` | preferred_filter.py:74, 117 |
| `export.py` | Parquet export of filings and chunks | `export_filings_to_parquet` | export.py:81 |
| `config/settings.py` | environment configuration | `get_env`, `DB_AUTH` | settings.py:48, 89 |
| `config/logger.py` | loguru stream and JSON file sinks | `configure_pipeline_logger` | logger.py:49 |
| `cli.py` | click command registration | `scan`, `scan-mixed`, `sec-edgar-html`, `sec-edgar-csv`, `sec-edgar-stream`, `file`, `enrich`, `filter-preferred`, `export`, `xbrl-bulk` | cli.py:60, 85, 144, 182, 212, 287, 367, 484, 576, 625 |

### Call graphs

`run_ingestion_pipeline` (pipeline.py:274):

```text
run_ingestion_pipeline
├─ _resolve_filing_id            (pipeline.py:91)
├─ [skip_existing] surreal_query (db.py:51)
├─ _ensure_source_type           (pipeline.py:75) → classify_source_type
├─ parse_document                (pipeline.py:241)
│   ├─ parse_pdf_layout          (pdf_parser.py:526)  [PDF]
│   └─ parse_html_file           (html_parser.py:340) [HTML]
├─ label_sections_structured     (sections.py:192)   [structured]
├─ label_sections_glossy         (sections.py:229)   [glossy + llm_callable]
├─ enrich_filing_metadata_with_edgartools (html_parser.py:145) [HTML]
├─ _apply_extracted_metadata     (pipeline.py:175)
├─ _populate_xbrl_fields         (pipeline.py:133)   [HTML] → _load_xbrl_bulk_cache
├─ ExchangeFilingModel           (schemas.py:27)
└─ SurrealConnection
    ├─ delete_record             (connection.py:96)
    ├─ upsert                    (connection.py:101)
    └─ establish_graph_relations (relations.py:131)
```

`run_html_content_pipeline` (pipeline.py:509):

```text
run_html_content_pipeline
├─ _ensure_source_type           (pipeline.py:75)
├─ parse_html_content            (html_parser.py:326)
│   ├─ extract_text_from_html    (html_parser.py:204)
│   ├─ extract_tables_from_html  (html_parser.py:274)
│   └─ extract_filing_metadata   (html_parser.py:53)
├─ label_sections_structured     (sections.py:192)   [structured]
├─ label_sections_glossy         (sections.py:229)   [glossy]
├─ enrich_filing_metadata_with_edgartools (html_parser.py:145)
├─ _apply_extracted_metadata     (pipeline.py:175)
├─ _populate_xbrl_fields         (pipeline.py:133)
├─ ExchangeFilingModel           (schemas.py:27)
└─ SurrealConnection → delete_record, upsert, establish_graph_relations
```

`process_sec_edgar_csv_stream` (pipeline.py:858):

```text
process_sec_edgar_csv_stream
├─ stream_filings_from_csv       (sec_edgar.py:471)
│   └─ _stream_filings_from_csv  (sec_edgar.py:281)
│       ├─ read_company_csv      (sec_edgar.py:187)
│       ├─ Company(identifier)   (edgartools)
│       ├─ company.get_filings(form, year, amendments=False, trigger_full_load=True)
│       ├─ _get_40f_exhibit_content  (sec_edgar.py:144)  [40-F]
│       ├─ _fetch_filing_html        (sec_edgar.py:75)   [non-40-F]
│       └─ [skip flags] _load_existing_filings (pipeline.py:38)
└─ run_html_content_pipeline     (pipeline.py:509)
```

## 4. Data model

### Table list and cardinalities

| table | kind | cardinality | key | source |
| --- | --- | --- | --- | --- |
| `exchange_filing` | `SCHEMAFULL` | one record per filing | `filingId` (record id `exchange_filing:<filingId>`) | db.py:205, pipeline.py:462 |
| `company` | `SCHEMAFULL` | one per legal entity | `companyKey` = SHA-256 of uppercased ticker + normalized name | db.py:195, relations.py:21-24 |
| `has_filing` | `SCHEMAFULL TYPE RELATION` | company (1) → filing (N) | unique `(in, out)` | db.py:276-278 |
| `references_filing` | `SCHEMAFULL TYPE RELATION` | filing (1) → referenced company (N) | unique `(in, out)` | db.py:281-284 |

The edge tables exist only when `COMPANY_TABLE` is non-empty (`db.py:273`).

### `ExchangeFilingModel` fields grouped by purpose

Grouping follows `schemas.py:27-78`; every field below is declared in that
block unless noted.

**Identity** — who and what the record identifies: `filingId` (schemas.py:28),
`companyTicker` (:29), `stockCode` (:30), `stockName` (:31), `exchange` (:32),
`cik` (:40), `lei` (:41), `isin` (:42), `cusip` (:43), `sedol` (:44).

**Classification** — what kind of document it is: `filingType` (:33),
`filingSubtype` (:34), `filingCategory` (:35), `title` (:36), `sourceType` (:45),
`isAmendment` (:50).

**Temporal** — when the filing was made and what period it covers:
`filingDate` (:37), `reportingYear` (:46), `periodEndDate` (:48),
`fiscalYearEndMonth` (:49).

**Document content** — the extracted document itself: `documentUrl` (:38),
`documentSize` (:65), `documentType` (:66), `documentHash` (:67),
`documentText` (:68), `documentTextLen` (:69), `documentChunks` (:70),
`documentTables` (:71), `documentTableCnt` (:72), `language` (:51),
`pageCount` (:52).

**Extracted data** — structured financial facts: `xbrlFacts` (:75),
`xbrlProvenance` (:59), `xbrlSource` (:60), `xbrlCurrency` (:58).

**Enrichment** — post-ingestion classification: `industryCodeRaw` (:53),
`gicsCode` (:54), `gicsSector` (:55), `industryConfidence` (:56).

**Provenance** — source-of-truth tracking: `source` (:39),
`referencedTickers` (:64), `metadataSources` (:76), `metadataConfidence` (:77),
`exhibitSectionMap` (:78).

**Processing** — pipeline state: `documentStatus` (:73),
`documentStatusReason` (:74), `updatedAt` (:61).

Two Pydantic validators normalize identity and temporal values before write:
`normalize_cik` strips non-digits and zero-pads to 10 (schemas.py:80-88), and
`format_date` / `format_period_end_date` append `T00:00:00Z` to 10-character
dates (schemas.py:90-106).

### Nested schemas

Two Pydantic models describe the objects serialized into the JSON-string
fields. `DocumentChunkSchema` has `chunkIndex` (required), `pageNumber`,
`sectionLabel`, `text` (required), `charStart`, `charEnd`
(schemas.py:18-24). `DocumentTableSchema` has `tableIndex`, `sheetName`,
`pageNumber`, `headers`, `rows`, `rowCount`, `accuracy`, `markdown`, all
optional (schemas.py:7-15).

### Canonical section label set

Structured filings use six labels: `business`, `mdna`,
`corporate_governance`, `other`, `financial_statements`, `risk_factors`.
Defined in `sections.py:18-35` (`_STRUCTURED_PART_LABELS` and
`_STRUCTURED_ITEM_LABELS`), `sections.py:37-99` (`_20F_HEADER_PATTERNS`), and
`sections.py:101-109` (`_40F_MARKER_LABELS`). Glossy reports use a separate
eight-label vocabulary: `ceo_letter`, `strategy`, `business_review`,
`innovation`, `sustainability`, `governance`, `financial_review`, `other`
(sections.py:111-120).

### JSON-string encoding (short version)

Seven fields are stored as JSON-encoded strings rather than native nested
objects: `documentChunks`, `documentTables`, `xbrlFacts`, `xbrlProvenance`,
`metadataSources`, `metadataConfidence`, `exhibitSectionMap`
(`README.md:474-477`; all typed `option<string>` in db.py:236, 252, 254-258
and `str | None` in schemas.py:59, 70-71, 75-78). The rationale is that
SurrealDB coerces strings matching `word:word` into RecordID references during
`CONTENT` writes, which would corrupt valid filing text; the full argument is
in section 10.2.

## 5. Ingestion paths

### 5.1 `sec-edgar-stream`

- **Input format**: a CSV whose rows carry `ticker` or `cik`, optionally
  `forms`/`form`, `year`, `max_filings`, and `industry`
  (`sec_edgar.py:187-204`). CLI flags `--year-range` and `--forms` override
  row-level filters (`cli.py:207-208`, `sec_edgar.py:325-330`).
- **Flow**:
  1. Require `EDGAR_IDENTITY` (sec_edgar.py:289-293).
  2. If skip flags are set, pre-load existing `sec_*` filing ids and hashes
     (`sec_edgar.py:298-306` → `pipeline.py:38`).
  3. For each row, resolve `Company(identifier)`, look up the ticker, and
     call `company.get_filings(form=..., year=..., amendments=False,
     trigger_full_load=True)` (sec_edgar.py:313-339).
  4. For 40-F, concatenate `EX-99.*` exhibits with `<h1>` markers
     (`sec_edgar.py:356-367`); otherwise fetch `filing.html()`
     (`sec_edgar.py:369`).
  5. Build metadata (`sec_edgar.py:402-425`), apply skip filters
     (`sec_edgar.py:426-441`), and yield `(html_content, metadata)`.
  6. `process_sec_edgar_csv_stream` calls `run_html_content_pipeline` per
     yielded filing (pipeline.py:872-882).
- **Failure recovery**: a company whose lookup fails is skipped with
  `Skipping ...; continuing` (sec_edgar.py:340-345); a filing whose HTML
  retrieval fails is skipped (sec_edgar.py:375-380); a filing that fails
  downstream parsing aborts the batch with a non-zero exit
  (pipeline.py:884-887).
- **Entry point**: `process_sec_edgar_csv_stream` (pipeline.py:858) via
  `cli.py:212`.

### 5.2 `scan` / `scan-mixed` / `file`

- **Input format**: local PDFs (`scan`), PDFs and HTML (`scan-mixed`), or a
  single explicit file (`file`). Filenames encode stock code, report type,
  language, and year (`crawler.py:24-209`; `README.md:235-260`).
- **Flow**:
  1. `scan_directory` / `scan_documents` glob the tree and pre-load existing
     `local_*` ids/hashes when skip flags are set (crawler.py:228-235).
  2. Per file, compute SHA-256 and derive `filingId = local_<basename>_<hash[:8]>`
     (crawler.py:247-258).
  3. Infer metadata from filename and directory (`crawler.py:255-281`).
  4. `run_ingestion_pipeline` parses, labels, validates, and persists
     (pipeline.py:274-506).
- **Failure recovery**: a worker failure is logged and counted but does not
  abort the batch (`pipeline.py:682-695`, `738-754`); a missing binary sets
  `documentStatus = "FAILED"` with a reason (pipeline.py:350-357).
- **Entry point**: `process_entire_directory` (pipeline.py:655),
  `process_mixed_directory` (pipeline.py:718); `file` calls
  `run_ingestion_pipeline` directly (cli.py:340-349).

### 5.3 `xbrl-bulk`

- **Input format**: SEC bulk archives — `companyfacts.zip` and quarterly FSDS
  archives — optionally filtered by a `--cik-csv` with a `cik` column
  (`xbrl_bulk.py:760-775`).
- **Flow**:
  1. Download `companyfacts.zip` unless `--skip-companyfacts` (cli.py:643-644;
     xbrl_bulk.py:460).
  2. Download FSDS quarters for `--start-year`..`--end-year` (cli.py:645-647;
     xbrl_bulk.py:495).
  3. Parse each archive to select 17 canonical concepts, then write
     `xbrl_facts.parquet` (cli.py:655-733; xbrl_bulk.py:517, 677).
- **Failure recovery**: invalid ZIPs return empty dicts and log a skip
  (xbrl_bulk.py:648-650, 742-744); corrupt downloads are re-downloaded or
  skipped with "re-run to retry" (xbrl_bulk.py:482-492); `.done` checkpoints
  make the download idempotent (xbrl_bulk.py:422-449).
- **Entry point**: the `xbrl-bulk` command (cli.py:625).

### 5.4 Worked example: an AAPL 10-K through the stream path

The brief requested a GOOG trace using `sec_GOOG_0001652044-24-000022`, on the
grounds that its expected values appear in `tests/test_xbrl_bulk.py`. That
accession does not exist in the repository, and `tests/test_xbrl_bulk.py`
contains only synthetic fixtures (accessions such as `0000000001-24-000001`
with values 123, 456, 40, and so on), not hardcoded GOOG financials.

<!-- CONFLICT: README.md:608-611 states that test_xbrl_bulk.py contains
     "ground-truth tests ... from actual filings (GOOG FY2023, AAPL FY2024,
     MSFT FY2024)". The actual test file contains no such ground-truth values;
     trust the code. -->

The trace below instead uses `sec_AAPL_0000320193-25-000079`, whose expected
values are asserted in `tests/test_sec_edgar.py:45-114` and
`tests/test_pipeline.py:546-590`.

1. **CSV row.** The input row `ticker,forms,year` → `AAPL,8-K,2020`
   (`tests/test_sec_edgar.py:25`). CLI flags override it to
   `forms=["10-K","10-Q"]` and `year=list(range(2018,2026))`
   (`tests/test_sec_edgar.py:73-78`).
2. **edgartools retrieval.** `Company("AAPL").get_filings(...)` returns a
   filing with `accession_no="0000320193-25-000079"`, `form="10-K"`,
   `company="Apple Inc."`, `report_date="2025-09-27"`, `filing_date="2025-11-01"`,
   `cik=320193` (`tests/test_sec_edgar.py:48-56`).
3. **Metadata construction.** `_stream_filings_from_csv` produces
   `filingId="sec_AAPL_0000320193-25-000079"`, `sourceType="structured_filing"`,
   `reportingYear=2025`, `periodEndDate="2025-09-27"`,
   `fiscalYearEndMonth=9`, `isAmendment=False`, `language="en"`,
   `pageCount=None` (`tests/test_sec_edgar.py:79-86`), and `cik="0000320193"`
   (`tests/test_sec_edgar.py:114`; zero-padded by `schemas.py:80-88`).
4. **HTML parsing.** `parse_html_content` (`html_parser.py:326`) strips hidden
   Inline XBRL containers (`html_parser.py:208-221`), splits text into
   3,000-character pseudo-page chunks (`html_parser.py:230`), extracts tables,
   and reads Inline XBRL metadata. In the end-to-end test, the HTML
   `<ix:nonNumeric name="dei:EntityRegistrantName">Apple Inc.` yields
   `stockName="Apple Inc."` (`html_parser.py:58-60`; asserted at
   `tests/test_pipeline.py:585`).
5. **Section labeling.** `label_sections_structured(chunks, form_type="10-K")`
   (`sections.py:192`) matches `ITEM 1` and labels the first chunk
   `"business"` (`tests/test_pipeline.py:588`; `sections.py:25-27`).
6. **XBRL bulk cache lookup.** `_populate_xbrl_fields` (`pipeline.py:133`)
   reads the bulk Parquet cache keyed on `(accession_no, concept)`. The test
   fixture supplies `("0000320193-25-000079", "assets") → {value: 42, tag:
   "Assets", unit: "USD", period: "2025-09-27", kind: "instant"}`
   (`tests/test_pipeline.py:557-563`), yielding `xbrlFacts = {"assets": 42}`
   (`tests/test_pipeline.py:589`), `xbrlSource="bulk"`, `xbrlCurrency="USD"`.
7. **Persistence.** `run_html_content_pipeline` validates with
   `ExchangeFilingModel`, then `SurrealConnection` deletes any stale record
   and upserts the new one (pipeline.py:626-645; connection.py:96-129);
   `documentType="HTML"` is asserted at `tests/test_pipeline.py:584`.

## 6. Extraction layer

### PDF parsing

- **Tool choice**: `pymupdf4llm.to_markdown(..., page_chunks=True)` is the
  primary extractor, preserving headings, lists, and emphasis as Markdown
  (pdf_parser.py:543-555). Camelot (`camelot.read_pdf`) extracts tables with
  lattice flavor first and stream flavor as fallback (pdf_parser.py:351-376).
  Hybrid OCR uses `rapidtess_api.exec_ocr` (RapidOCR region detection with
  Tesseract recognition) through `_exec_ocr_without_tiny_image_noise`
  (pdf_parser.py:448-478).
- **Alternates rejected**: a pure `pymupdf` text dump was rejected because it
  loses heading structure; a pure LLM layout pass was rejected for cost and
  determinism. These are reasoned judgments, not code artifacts.
- **Known limitations**: Camelot returns zero tables on whitespace-aligned
  glossy PDFs with no ruled borders (`README.md:619-622`); non-December
  fiscal-year ends default to `{year}-12-31` in the local path
  (`crawler.py:19-21`, `README.md:623-627`); OCR is page-isolated so one bad
  page does not abort the document (pdf_parser.py:514-522).

### HTML parsing

- **Tool choice**: BeautifulSoup with `html.parser`. Hidden Inline XBRL noise
  is removed by decomposing `ix:header`, `ix:hidden`, `ix:references`, and
  `display:none` elements before text extraction (html_parser.py:208-221).
- **Whitespace preservation**: text extraction uses `"\n"` as the tag
  separator and collapses three or more newlines to two, preserving header
  boundaries for section labeling (html_parser.py:223-227).
- **Known limitation**: `enrich_filing_metadata_with_edgartools` requires
  `EDGAR_IDENTITY` and a CIK and is best-effort (html_parser.py:158-159).

### Section labeling

- **10-K**: `_STRUCTURED_HEADER` matches `PART I–IV` and `ITEM 1–16` with an
  optional letter suffix (sections.py:10-16); Item 1A is special-cased to
  `risk_factors` (sections.py:219-223).
- **20-F**: `_20F_HEADER_PATTERNS` match Items 3.D, 4, 5, 6, 8, and 15
  (sections.py:37-99).
- **40-F**: `label_sections_structured` routes 40-F to `_label_40f_by_markers`
  (sections.py:198-199), which matches injected `<h1>` markers and body
  headings against `_40F_MARKER_LABELS` (sections.py:167-189).

<!-- CONFLICT: README.md:614-618 states "40-F section labels are not
     populated" and "chunks have sectionLabel = None". The code applies
     labels to 40-F via _label_40f_by_markers (sections.py:198-199), and
     tests/test_sections.py:70-81 asserts risk_factors/mdna/financial_statements
     labels. Trust the code. -->

- **Glossy**: `label_sections_glossy` calls an optional `llm_callable` with a
  prompt asking for JSON section ranges, validates the response, and leaves
  chunks unlabeled on failure (sections.py:229-279).

### XBRL fact extraction

The bulk path (section 5.3) is the primary source. The per-filing fallback
`extract_xbrl_facts` (xbrl_facts.py:73) parses Inline XBRL `ix:nonFraction`
tags but is not invoked anywhere in `pipeline.py`; the pipeline logs
`source=none, not falling back` when the bulk cache has no facts
(pipeline.py:163-167).

## 7. Persistence layer

SurrealDB was chosen over Postgres and over a Parquet-only store. Postgres
would require either a fixed JSON column per filing (losing graph
traversal) or a fully normalized fact table (heavy ETL for a schema that is
still changing). A Parquet-only store is cheap for analytics but offers no
upsert, no uniqueness enforcement, and no graph edges. SurrealDB provides
`SCHEMAFULL` enforcement, document upserts, and built-in `RELATION` graph
edges in one store. These are reasoned trade-offs; the schema itself is
verified in `db.py:191-286`.

`SCHEMAFULL` is declared on every table (db.py:195, 205, 276, 281), so a field
not declared in `_build_schema_sql` is rejected on write. The write path
prunes `None` values (`connection.py:25-41`) because SurrealDB rejects nested
`NULL`s, and serializes the seven nested fields to JSON strings
(`connection.py:44-52`, `101-111`).

The request-body limits are configured on the SurrealDB server process, not in
`settings.py`. The server env vars are `SURREAL_HTTP_MAX_RPC_BODY_SIZE`
(`128MiB`) and `SURREAL_HTTP_MAX_SQL_BODY_SIZE` (`16MiB`), documented at
`README.md:46-61`. The pipeline-side settings in `settings.py` are the
connection vars `SURREAL_ENDPOINT`, `SURREAL_USER`, `SURREAL_PASS`,
`SURREAL_NAMESPACE`, `SURREAL_DATABASE` (settings.py:53-57). The SurrealDB
`/sql` endpoint accepts up to 1 MiB and `/rpc` up to 4 MiB by default
(db.py:90). The write-path routing threshold is `_SQL_UPSERT_LIMIT_BYTES =
900_000` (connection.py:10): payloads whose serialized `UPSERT` statement
exceeds 900,000 bytes are sent via the `/rpc` endpoint as parameterized JSON
(connection.py:117-126).

## 8. Enrichment layer

Enrichment runs after ingestion, not during, for three reasons: (1) the GICS
mapping is a curation artifact that changes independently of the extractors,
so a re-runnable pass keeps it idempotent; (2) `reportingYear` can be derived
from `periodEndDate` only after records are present; and (3) CIK backfill
depends on a ticker-to-CIK CSV that may arrive later than the filings. The
pass is safe to re-run because it filters on `WHERE gicsCode IS NONE` and
`WHERE reportingYear IS NONE` (enrichment.py:109-113, 196-199).

GICS mapping: `load_industry_mapping` builds three lookup structures —
`by_cik`, `by_companyKey`, `by_ticker` (enrichment.py:18-20) — and
`enrich_industry_codes` applies a three-tier lookup priority of CIK →
companyKey → ticker (enrichment.py:136-144). Each matched filing receives
`gicsCode`, `gicsSector`, and `industryConfidence` (enrichment.py:170-174).

`reportingYear` is backfilled from the first four characters of
`periodEndDate` (enrichment.py:213-214). CIK is backfilled from a
ticker-to-CIK CSV (`enrichment.py:267-300`). `industryCodeRaw` is preserved:
it is only overwritten with the mapping's `company_name` when its current
value is `None`, `""`, `"UNKNOWN"`, or `"NONE"` (enrichment.py:175-176).

## 9. Resume and idempotency

The `filingId` is deterministic by construction: SEC filings use
`sec_<TICKER>_<accession>` (sec_edgar.py:403), local files use
`local_<basename>_<sha256[:8]>` (crawler.py:258), in-memory HTML uses
`html_<sha256[:32]>` (pipeline.py:521), and the file-path fallback uses
`local_<sha256[:32]>` (pipeline.py:86). Re-running over the same input
therefore reproduces the same ids, and the delete-then-upsert write
(pipeline.py:480-481, 635-636) makes ingestion idempotent.

Resume semantics: `--skip-existing` skips a filing when its `filingId` is
already present; `--skip-by-hash` also skips when the SHA-256 `documentHash`
matches, catching renamed or moved files. Both flags are available on every
batch command (cli.py:58-59, 103-104, 142-143, 180-181, 210-211, 284-285).

The resume pre-load helper is `_load_existing_filings(prefix)` (pipeline.py:38),
which issues a single query
`SELECT filingId, documentHash FROM exchange_filing WHERE string::starts_with(filingId, '<prefix>')`
(pipeline.py:44-47). The time-saved structure is the reduction from one DB
round-trip per file to one round-trip per batch:

| approach | database round-trips for N files |
| --- | --- |
| per-file existence query | N |
| pre-load at batch start | 1 (plus in-memory set lookups) |

No wall-clock latency figures are claimed because no benchmark is checked into
the repository.

<!-- TODO: if a benchmark of resume-vs-cold-start latency is produced, record
     the measured seconds here; none exists in the repo. -->

## 10. Design decisions and justifications

### 10.1 SurrealDB over Postgres or Parquet-only

- **Decision**: persist filings, chunks, and graph edges in a single
  SurrealDB database with `SCHEMAFULL` tables.
- **Alternatives considered**: Postgres with JSONB plus a `RECURSIVE` graph
  model; a Parquet-only analytics store.
- **Reason**: one store provides document upserts, type enforcement, and
  native `RELATION` edges without a separate graph layer, and the write path
  already handles the payload-size asymmetry via `/rpc`.
- **Trade-off accepted**: SurrealDB's record-id coercion of `word:word`
  strings forced the JSON-string encoding in section 10.2, and its `/sql`
  endpoint has a lower body limit than Postgres would.
- **Reference implementation**: `db.py:191-286` (`_build_schema_sql`),
  `connection.py:101-129` (upsert routing).

### 10.2 JSON-string encoding for nested fields

- **Decision**: store the seven nested fields as JSON strings, not native
  SurrealDB objects or arrays.
- **Alternatives considered**: native `object`/`array` fields; normalizing
  chunks and facts into separate relational tables.
- **Reason**: SurrealDB coerces strings matching `word:word` into RecordID
  references during `CONTENT` writes, which rejects valid filing text such as
  `Note:Unless` and `M:68` (mortality tables); JSON strings are never
  interpreted as RecordIDs. Normalized tables were rejected because chunk and
  fact shapes are still evolving.
- **Trade-off accepted**: readers must call `encoding::json::decode(field)` in
  SurrealQL or `json.loads()` in Python; the export path does exactly this
  (export.py:108-109).
- **Reference implementation**: serialization at `pipeline.py:451-458` and
  `connection.py:44-52`; schema types at `db.py:236, 252, 254-258`.

### 10.3 Bulk XBRL download over per-filing Inline XBRL parsing

- **Decision**: extract the 17 canonical facts from SEC bulk archives
  (`companyfacts.zip` + FSDS) into a Parquet cache, and join at ingestion by
  `(accession_no, concept)`.
- **Alternatives considered**: parsing each filing's Inline XBRL at ingestion
  time (the `xbrl_facts.py` path).
- **Reason**: bulk download amortizes one large transfer over thousands of
  filings instead of thousands of per-filing HTTP requests, and the Parquet
  cache is loaded once and reused across the process (pipeline.py:98-119).
- **Trade-off accepted**: the bulk cache must be built by a separate
  `xbrl-bulk` run before ingestion facts are available, and the fallback
  extractor is left unused (pipeline.py:163-167).
- **Reference implementation**: `xbrl_bulk.py:517-653` (companyfacts) and
  `677-757` (FSDS); lookup at `pipeline.py:133-167`.

### 10.4 Regex section labeling for SEC filings, LLM for glossy, skipped for 40-F

- **Decision**: label 10-K and 20-F chunks with deterministic regexes, glossy
  chunks with an optional LLM callable, and 40-F chunks with marker matching.
- **Alternatives considered**: a single LLM classifier for all document types;
  regex for glossy reports.
- **Reason**: SEC Item headers are standardized and regexes are free and
  deterministic; glossy reports have no standard heading vocabulary, so an LLM
  is the only feasible classifier, but it must be opt-in (`llm_callable`
  defaults to `None`) to avoid a hard LLM dependency.
- **Trade-off accepted**: LLM labeling is probabilistic and its failure mode
  is to leave chunks unlabeled (sections.py:272-274); regex labeling depends
  on heading text surviving extraction.
- **Reference implementation**: `sections.py:192-227` (structured) and
  `229-279` (glossy).

Note: the decision title says "skipped for 40-F", which reflects the task
brief and `README.md:614-618`, but the code does label 40-F via
`_label_40f_by_markers`; see the CONFLICT note in section 6.

### 10.5 XBRL fact selection: dimensional filter, instant versus duration, priority order, period match

- **Decision**: select a fact only when four gates pass — consolidated
  (non-dimensional), USD, period match, and instant/duration — then prefer the
  leftmost matching tag in `CANONICAL_CONCEPTS`.
- **Alternatives considered**: taking the first fact without dimensional
  filtering; treating all concepts uniformly.
- **Reason**: `_is_consolidated_fact` rejects segment/member/frame dimensions
  (xbrl_bulk.py:183-192); `_matches_period` requires the fact end date to
  equal the filing period (xbrl_bulk.py:176-180); `CONCEPT_KIND` requires
  `qtrs=4` for duration and `qtrs=0` for instant in FSDS
  (xbrl_bulk.py:200-215), and `fp=FY` for duration in companyfacts
  (xbrl_bulk.py:229-238); priority order is enforced by iterating the tag list
  left-to-right (xbrl_bulk.py:607-629, 724-741).
- **Trade-off accepted**: a dimensional or non-USD fact is dropped entirely
  rather than approximately recovered, which can leave a concept absent.
- **Reference implementation**: `xbrl_bulk.py:195-242`; tests at
  `tests/test_xbrl_bulk.py:191-254`.

### 10.6 40-F content retrieved, section labels skipped

- **Decision**: for 40-F, concatenate `EX-99.*` exhibits into one document
  with injected `<h1>` markers and a per-exhibit section map, rather than
  fetching the cover-page HTML.
- **Alternatives considered**: parsing the 40-F primary HTML; mapping exhibits
  to the six-item taxonomy.
- **Reason**: the 40-F primary document is a cover page and the substance is
  in the exhibits, so `_get_40f_exhibit_content` concatenates exhibits
  (sec_edgar.py:144-184) and records an `exhibitSectionMap`
  (sec_edgar.py:173, 424).
- **Trade-off accepted**: exhibit classification is heuristic
  (`_classify_exhibit_by_content` then `_map_exhibit_to_section`), and the
  title's "section labels skipped" reflects the README but is contradicted by
  the marker-based labeling in `sections.py:167-189`; see the CONFLICT note in
  section 6.
- **Reference implementation**: `sec_edgar.py:144-184`; `sections.py:167-189`.

### 10.7 Non-USD XBRL facts rejected

- **Decision**: discard any fact whose unit is not `USD` (or `US$`).
- **Alternatives considered**: storing all currencies and converting with FX;
  storing the raw unit without conversion.
- **Reason**: `_normalize_value` returns `None` for non-USD units
  (xbrl_bulk.py:166-173), and both selectors require `USD` explicitly
  (xbrl_bulk.py:205, 225), so all stored facts are directly comparable.
- **Trade-off accepted**: non-USD filers (mostly EU and Japanese 20-F filers)
  have `xbrlFacts = None`; `README.md:628-629` estimates this at ~10–15% of
  SEC filings.
- **Reference implementation**: `xbrl_bulk.py:166-173, 205, 225`;
  `tests/test_xbrl_bulk.py:280-295`.

### 10.8 Request body limit raised via env var rather than code split

- **Decision**: raise the SurrealDB server body limits via
  `SURREAL_HTTP_MAX_RPC_BODY_SIZE` and `SURREAL_HTTP_MAX_SQL_BODY_SIZE`
  instead of splitting large payloads in application code.
- **Alternatives considered**: chunking `documentText` and the nested fields
  across multiple records or multiple upserts; compressing text.
- **Reason**: a single-record write preserves transactional integrity and
  avoids reconstructing a filing from fragments on read; the 128 MiB `/rpc`
  limit provides ~20× headroom over the largest observed filing
  (`README.md:46-61`).
- **Trade-off accepted**: deployment must configure the SurrealDB server, not
  just the pipeline; the in-process `_SQL_UPSERT_LIMIT_BYTES` routing still
  exists for payloads over 900,000 bytes (connection.py:117-126).
- **Reference implementation**: `connection.py:10, 117-126`; `db.py:87-93`;
  `README.md:46-61`.

### 10.9 Resume via pre-load rather than per-file DB query

- **Decision**: load existing filing ids and hashes once at batch start, then
  filter in memory, instead of querying the database per file.
- **Alternatives considered**: a `SELECT ... WHERE filingId = ...` per file;
  relying on unique indexes alone.
- **Reason**: one prefix query (`string::starts_with`) returns the full
  resume set in a single round-trip (pipeline.py:44-47), avoiding N queries
  for large batches.
- **Trade-off accepted**: the pre-load is best-effort; on failure the skip
  flags silently have no effect for that run (pipeline.py:55-60).
- **Reference implementation**: `pipeline.py:38-72`; called from
  `crawler.py:230-235` and `sec_edgar.py:298-306`.

### 10.10 OCR off by default

- **Decision**: run selective OCR (`OCRMode.SELECT_REMOVING_OLD`) by default,
  disable it with `--no-ocr`, and never force full-page OCR from the CLI.
- **Alternatives considered**: forcing OCR on every page; OCR always off.
- **Reason**: `pymupdf4llm.to_markdown` is called with
  `use_ocr=OCRMode.SELECT_REMOVING_OLD if use_ocr else OCRMode.NEVER` and
  `force_ocr=False` (pdf_parser.py:546-554), so OCR applies only to pages
  whose native text layer is missing, balancing accuracy against cost.
- **Trade-off accepted**: scanned pages without a text layer depend on
  Tesseract being installed (`README.md:280-281`); full-page `force_ocr` is
  reachable only through the `parse_pdf_layout` parameter, not the CLI.
- **Reference implementation**: `pdf_parser.py:526-555`; `cli.py:55-56`.

<!-- CONFLICT: the decision title says "OCR off by default", but the code
     default is use_ocr=True (selective OCR on). README.md:138-140 describes
     this inconsistently ("opt-in per page" yet "--ocr ... applies it to every
     page"). The accurate statement is: selective OCR on by default,
     --no-ocr disables, full-page force_ocr is off and not CLI-exposed. -->

### 10.11 Metadata confidence scores

- **Decision**: record per-field provenance (`metadataSources`) and confidence
  (`metadataConfidence`) maps, each stored as a JSON string.
- **Alternatives considered**: a single overall confidence scalar; no
  provenance at all.
- **Reason**: extraction uses multiple strategies (PDF properties, regex,
  Inline XBRL, filename), so per-field sources and confidence let downstream
  analyses weigh fields individually (metadata.py:210-224; pdf_parser.py:253-263).
- **Trade-off accepted**: two extra JSON-string columns that readers must
  decode, and confidence values are heuristic weights, not calibrated
  probabilities.
- **Reference implementation**: `metadata.py:141-224`; `pdf_parser.py:120-280`;
  serialization at `pipeline.py:428-435`.

## 11. Known limitations

- **40-F exhibit structure.** Content is assembled from `EX-99.*` exhibits
  whose classification is heuristic, so the reconstructed document is not the
  native layout (section 10.6). Fix: content-based classification for 40-F
  (planned; `README.md:660-661`).
- **Non-USD filers.** USD-only fact selection leaves non-USD 20-F filers with
  empty `xbrlFacts` (section 10.7). Fix: FX conversion (planned;
  `README.md:664`).
- **Camelot table gaps.** Whitespace-aligned glossy tables produce zero rows
  (`README.md:619-622`). Fix: parse `<table>` blocks from `documentText` when
  Camelot returns zero rows (deferred; `README.md:658`).
- **Non-December fiscal year ends.** The local PDF path infers `periodEndDate`
  as the calendar year end, so Japanese, Indian, Australian, and UK filers can
  have incorrect dates (section 2; `README.md:623-627`). Fix: enable the
  cover-page date extractor (planned).
- **Selection bias.** SEC filers skew larger and more US-centric (section 2).
  Fix: none for the Forbes scope; document analyses that use `structured_filing`
  records as US-heavy.
- **OCR dependency.** Selective OCR requires a host Tesseract install
  (`README.md:280-281`). Fix: none; a system prerequisite.
- **RecordID coercion.** The JSON-string encoding (section 10.2) forces
  decode-on-read. Fix: none; a documented read convention.

## 12. Future work

**High priority (blocking for paper or analysis):**

- Confirm the six AI index definitions and map each to a `ticker`/`cik`
  source; the current Overview table is a TODO.
- Measure the calendar-year versus non-December filer proportion from an
  ingested snapshot (currently a TODO).
- Add segment-aware XBRL extraction so `segmentRevenue` can be reintroduced
  (`xbrl_bulk.py:38-41` documents why it is absent; `README.md:663`).

**Medium priority:**

- Add FX conversion for non-USD facts (`README.md:664`).
- Add content-based section classification for 40-F (`README.md:660-661`).
- Enable the regex cover-page date extractor for non-December local PDFs.

**Low priority:**

- Add language-specific keyword lists for non-English glossy reports
  (`README.md:659`).
- Recover tables from `documentText` when Camelot yields none
  (`README.md:658`).

**Downstream (out of scope):**

- Extend graph relations beyond company/filing edges (`README.md:665`).
- Calibrate metadata confidence scores against a labeled validation set.

## Appendix A — Full schema DDL

Copied verbatim from `src/fin_pipeline/db/db.py:191-286`
(`_build_schema_sql`).

```python
def _build_schema_sql() -> str:
    """Return the full schema DDL, dynamically including graph edges if COMPANY_TABLE is set."""
    base = """
-- Core entity table used by graph relations
DEFINE TABLE IF NOT EXISTS company SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS ticker     ON TABLE company TYPE string;
DEFINE FIELD IF NOT EXISTS companyName ON TABLE company TYPE option<string>;
DEFINE FIELD IF NOT EXISTS exchange   ON TABLE company TYPE option<string>;
DEFINE FIELD IF NOT EXISTS updatedAt  ON TABLE company TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS companyKey ON TABLE company TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_company_ticker_lookup ON TABLE company COLUMNS ticker;
DEFINE INDEX IF NOT EXISTS idx_company_key ON TABLE company COLUMNS companyKey UNIQUE;

-- Filing table (SCHEMAFULL for type safety)
DEFINE TABLE IF NOT EXISTS exchange_filing SCHEMAFULL;

-- Metadata fields
DEFINE FIELD IF NOT EXISTS filingId       ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS companyTicker  ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS stockCode      ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS cik            ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS lei            ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS isin           ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS cusip          ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS sedol          ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS stockName      ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS exchange       ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS filingType     ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS filingSubtype  ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS filingCategory ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS title          ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS filingDate     ON TABLE exchange_filing TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS documentUrl    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source         ON TABLE exchange_filing TYPE string;
DEFINE FIELD IF NOT EXISTS updatedAt      ON TABLE exchange_filing TYPE datetime;

-- Cross-reference fields
DEFINE FIELD IF NOT EXISTS referencedTickers ON TABLE exchange_filing TYPE option<array<string>>;

-- Document fields (text + metadata only; raw blobs are NOT stored)
DEFINE FIELD IF NOT EXISTS documentSize         ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS documentType         ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentHash         ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentText         ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentTextLen      ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS documentTables       ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentTableCnt     ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS documentStatus       ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentStatusReason ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS sourceType            ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS reportingYear         ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS periodEndDate      ON TABLE exchange_filing TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS fiscalYearEndMonth ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS isAmendment        ON TABLE exchange_filing TYPE bool;
DEFINE FIELD IF NOT EXISTS language           ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS pageCount          ON TABLE exchange_filing TYPE option<int>;
DEFINE FIELD IF NOT EXISTS industryCodeRaw    ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS gicsCode           ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS gicsSector         ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS industryConfidence ON TABLE exchange_filing TYPE option<float>;
DEFINE FIELD IF NOT EXISTS xbrlCurrency       ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS xbrlProvenance     ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS xbrlSource         ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS documentChunks        ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS xbrlFacts             ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS metadataSources      ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS metadataConfidence   ON TABLE exchange_filing TYPE option<string>;
DEFINE FIELD IF NOT EXISTS exhibitSectionMap   ON TABLE exchange_filing TYPE option<string>;

-- Indexes
DEFINE INDEX IF NOT EXISTS idx_ef_ticker    ON TABLE exchange_filing COLUMNS companyTicker;
DEFINE INDEX IF NOT EXISTS idx_ef_cik       ON TABLE exchange_filing COLUMNS cik;
DEFINE INDEX IF NOT EXISTS idx_ef_stockcode ON TABLE exchange_filing COLUMNS stockCode;
DEFINE INDEX IF NOT EXISTS idx_ef_date      ON TABLE exchange_filing COLUMNS filingDate;
DEFINE INDEX IF NOT EXISTS idx_ef_type      ON TABLE exchange_filing COLUMNS filingType;
DEFINE INDEX IF NOT EXISTS idx_ef_source    ON TABLE exchange_filing COLUMNS source;
DEFINE INDEX IF NOT EXISTS idx_ef_docstatus ON TABLE exchange_filing COLUMNS documentStatus;
DEFINE INDEX IF NOT EXISTS idx_ef_category  ON TABLE exchange_filing COLUMNS filingCategory;
DEFINE INDEX IF NOT EXISTS idx_ef_periodEnd   ON TABLE exchange_filing COLUMNS periodEndDate;
DEFINE INDEX IF NOT EXISTS idx_ef_gicsCode    ON TABLE exchange_filing COLUMNS gicsCode;
DEFINE INDEX IF NOT EXISTS idx_ef_gicsSector  ON TABLE exchange_filing COLUMNS gicsSector;
"""
    if COMPANY_TABLE:
        base += f"""
-- Edge table: company -> filing (graph relation)
DEFINE TABLE IF NOT EXISTS has_filing SCHEMAFULL TYPE RELATION IN {COMPANY_TABLE} OUT exchange_filing;
DEFINE FIELD IF NOT EXISTS createdAt ON TABLE has_filing TYPE option<datetime>;
DEFINE INDEX IF NOT EXISTS idx_hf_unique ON TABLE has_filing COLUMNS in, out UNIQUE;

-- Edge table: filing -> referenced company (graph relation)
DEFINE TABLE IF NOT EXISTS references_filing SCHEMAFULL TYPE RELATION IN exchange_filing OUT {COMPANY_TABLE};
DEFINE FIELD IF NOT EXISTS createdAt ON TABLE references_filing TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS source    ON TABLE references_filing TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_rf_unique ON TABLE references_filing COLUMNS in, out UNIQUE;
"""
    return base
```

## Appendix B — Canonical XBRL concepts with source-tag priority

Copied verbatim from `src/fin_pipeline/utils/xbrl_bulk.py:42-116`
(`CANONICAL_CONCEPTS`). Priority is left-to-right (highest first).

```python
CANONICAL_CONCEPTS = {
    "revenues": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    ],
    "costOfRevenue": [
        ("us-gaap", "CostOfRevenue"),
        ("us-gaap", "CostOfGoodsAndServicesSold"),
        ("us-gaap", "CostOfGoodsSold"),
        ("ifrs-full", "CostOfSales"),
    ],
    "grossProfit": [("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit")],
    "operatingIncomeLoss": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ],
    "netIncomeLoss": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
        ("ifrs-full", "ProfitLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
    ],
    "researchAndDevelopmentExpense": [
        ("us-gaap", "ResearchAndDevelopmentExpense"),
        ("us-gaap", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        ("ifrs-full", "ResearchAndDevelopmentExpense"),
    ],
    "assets": [("us-gaap", "Assets"), ("ifrs-full", "Assets")],
    "liabilities": [("us-gaap", "Liabilities"), ("ifrs-full", "Liabilities")],
    "liabilitiesCurrent": [
        ("us-gaap", "LiabilitiesCurrent"),
        ("ifrs-full", "CurrentLiabilities"),
    ],
    "stockholdersEquity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("ifrs-full", "Equity"),
        ("ifrs-full", "EquityAttributableToOwnersOfParent"),
    ],
    "cashAndCashEquivalents": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        ("ifrs-full", "CashAndCashEquivalents"),
    ],
    "intangibleAssets": [
        ("us-gaap", "IntangibleAssetsNetExcludingGoodwill"),
        ("us-gaap", "FiniteLivedIntangibleAssetsNet"),
        ("ifrs-full", "IntangibleAssetsOtherThanGoodwill"),
    ],
    "goodwill": [("us-gaap", "Goodwill"), ("ifrs-full", "Goodwill")],
    "propertyPlantAndEquipmentNet": [
        ("us-gaap", "PropertyPlantAndEquipmentNet"),
        ("ifrs-full", "PropertyPlantAndEquipment"),
    ],
    "capitalExpenditures": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets"),
        ("us-gaap", "PaymentsForCapitalImprovements"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    ],
    "depreciationDepletionAndAmortization": [
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "DepreciationAmortizationAndAccretionNet"),
        ("ifrs-full", "DepreciationAndAmortisationExpense"),
    ],
    "capitalizedComputerSoftwareNet": [
        ("us-gaap", "CapitalizedComputerSoftwareNet"),
        ("us-gaap", "CapitalizedComputerSoftwareGross"),
        ("ifrs-full", "IntangibleAssetsOtherThanGoodwill"),
    ],
}
```

The companion `CONCEPT_KIND` map (`xbrl_bulk.py:117-135`) assigns `duration`
to revenues, costOfRevenue, grossProfit, operatingIncomeLoss, netIncomeLoss,
researchAndDevelopmentExpense, depreciationDepletionAndAmortization,
capitalExpenditures, and `instant` to the remaining nine concepts.

## Appendix C — JSON structure examples

Seven fields are JSON strings. Decoded examples, using test data where
available.

**`documentChunks`** — a list of `DocumentChunkSchema` objects. From the
end-to-end AAPL test (`tests/test_pipeline.py:576-588`), the decoded first
chunk is `{"chunkIndex": 0, "pageNumber": 1, "sectionLabel": "business",
"text": ..., "charStart": null, "charEnd": null}`; the assertions check
`pageNumber == 1` and `sectionLabel == "business"`.

**`documentTables`** — a list of `DocumentTableSchema` objects. The HTML
extractor produces `{"tableIndex": <i>, "sheetName": null, "pageNumber": null,
"headers": [...], "rowCount": <n>, "markdown": "..."}`
(html_parser.py:310-319). No ground-truth table fixture is asserted in the
tests, so the field order above is cited from the code.

**`xbrlFacts`** — a map from canonical concept to numeric value. From the
bulk-cache test (`tests/test_pipeline.py:557-563, 589`), the decoded value is
`{"assets": 42}`.

**`xbrlProvenance`** — a map from concept to `{tag, unit, period}`. From
`tests/test_xbrl_bulk.py:736-742`, the decoded value is
`{"revenues": {"tag": "Revenues", "unit": "USD", "period": "2023-12-31"}}`.

**`metadataSources`** — a map from field name to source label. Example from
the crawler path (`crawler.py:74-84`):
`{"stockName": "filename_company_name", "companyTicker":
"parent_directory", "stockCode": "filename", "filingType": "filename",
"filingDate": "filename_year_inferred"}`.

**`metadataConfidence`** — a map from field name to float confidence. Example
from the same path (`crawler.py:85-93`): `{"stockName": 0.9, "companyTicker":
0.95, "stockCode": 0.9, "filingType": 0.9, "filingDate": 0.7}`.

**`exhibitSectionMap`** — a map from exhibit document type to section label.
From `tests/test_sec_edgar.py:466`, the decoded value is
`{"EX-99.1": "business"}`.

The seven fields and their `str | None` declarations are at
`schemas.py:59, 70-71, 75-78`; the JSON serialization that produces them is
at `pipeline.py:451-458` (nested) and `pipeline.py:428-435`
(metadata maps), with `exhibitSectionMap` serialized in
`sec_edgar.py:424`.
