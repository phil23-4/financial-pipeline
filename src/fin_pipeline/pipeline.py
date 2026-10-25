import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from typing import Any

from fin_pipeline.config.constants import (
    FILE_TYPE_HTML,
    FILE_TYPE_PDF,
    FILE_TYPE_UNKNOWN,
    FILING_TABLE,
    STATUS_FAILED,
    STATUS_PROCESSED,
)
from fin_pipeline.config.logger import pipeline_logger as log
from fin_pipeline.crawler import scan_directory, scan_documents
from fin_pipeline.db.connection import SurrealConnection
from fin_pipeline.db.relations import establish_graph_relations
from fin_pipeline.models.schemas import ExchangeFilingModel
from fin_pipeline.utils.classify import classify_source_type
from fin_pipeline.utils.crypto import calculate_file_hash
from fin_pipeline.utils.html_parser import (
    enrich_filing_metadata_with_edgartools,
    parse_html_content,
    parse_html_file,
)
from fin_pipeline.utils.pdf_parser import parse_pdf_layout
from fin_pipeline.utils.sections import (
    label_sections_glossy,
    label_sections_structured,
)

_xbrl_bulk_cache: dict[tuple[str, str], Any] | None = None


def _ensure_source_type(payload: dict) -> None:
    source_type = payload.get("sourceType")
    if source_type is None or (
        isinstance(source_type, str) and source_type.lower() == "unknown"
    ):
        payload["sourceType"] = classify_source_type(payload.get("filingType"))


def _fallback_filing_id(file_path: str) -> str:
    """Build a stable filing ID when callers omit one."""
    try:
        return f"local_{calculate_file_hash(file_path)[:32]}"
    except OSError:
        return f"local_{hashlib.sha256(os.path.abspath(file_path).encode()).hexdigest()[:32]}"


def _resolve_filing_id(metadata_input: dict, file_path: str) -> str:
    filing_id = metadata_input.get("filingId")
    if isinstance(filing_id, str) and filing_id.strip():
        return filing_id.strip()
    return _fallback_filing_id(file_path)


def _load_xbrl_bulk_cache(
    parquet_path: str = "xbrl_bulk/xbrl_facts.parquet",
) -> dict[tuple[str, str], Any] | None:
    global _xbrl_bulk_cache
    if _xbrl_bulk_cache is None and os.path.exists(parquet_path):
        import pandas as pd

        from fin_pipeline.utils.xbrl_bulk import CANONICAL_CONCEPTS

        dataframe = pd.read_parquet(parquet_path)
        _xbrl_bulk_cache = {
            (row["accession_no"], row["concept"]): {
                "value": row["value"],
                "tag": row.get("tag"),
                "unit": row.get("unit"),
                "period": row.get("period"),
                "kind": row.get("kind"),
            }
            for _, row in dataframe.iterrows()
            if row["concept"] in CANONICAL_CONCEPTS
        }
    return _xbrl_bulk_cache


def _accession_from_filing_id(payload: dict) -> str | None:
    filing_id = payload.get("filingId")
    if not isinstance(filing_id, str):
        return None
    accession = filing_id.removeprefix("sec_")
    ticker = str(payload.get("companyTicker") or "")
    if ticker and accession.startswith(f"{ticker}_"):
        accession = accession[len(ticker) + 1 :]
    return accession or None


def _populate_xbrl_fields(payload: dict) -> None:
    from fin_pipeline.utils.xbrl_bulk import (
        CONCEPT_KIND,
        _apply_invariants,
    )

    cache = _load_xbrl_bulk_cache()
    accession = _accession_from_filing_id(payload)
    selected = {
        concept: cache[(accession, concept)]
        for concept in CONCEPT_KIND
        if cache and accession and (accession, concept) in cache
    }
    _apply_invariants(selected, accession or str(payload.get("filingId") or "unknown"))
    facts = {concept: entry["value"] for concept, entry in selected.items()}
    provenance = {
        concept: {
            "tag": entry.get("tag"),
            "unit": entry.get("unit"),
            "period": entry.get("period"),
        }
        for concept, entry in selected.items()
    }
    currencies = [entry.get("unit") for entry in selected.values() if entry.get("unit")]
    payload["xbrlFacts"] = facts or None
    payload["xbrlProvenance"] = provenance or None
    payload["xbrlSource"] = "bulk" if facts else "none"
    payload["xbrlCurrency"] = (
        max(set(currencies), key=currencies.count) if currencies else None
    )
    if not facts:
        log.debug(
            f"No XBRL bulk facts for {payload.get('filingId')} "
            "(source=none, not falling back)"
        )


def _read_html_content(file_path: str) -> str:
    with open(file_path, encoding="utf-8", errors="replace") as source:
        return source.read()


def _apply_extracted_metadata(parsed: dict, payload: dict) -> None:
    period_end_date = parsed.get("periodEndDate") or payload.get("periodEndDate")
    metadata_sources = payload.get("metadataSources")
    filename_date = (
        payload.get("documentType") == FILE_TYPE_PDF.upper()
        and isinstance(metadata_sources, dict)
        and metadata_sources.get("filingDate") == "filename_year_inferred"
    )
    if filename_date or (
        not period_end_date
        and payload.get("documentType") == FILE_TYPE_PDF.upper()
        and payload.get("filingDate")
    ):
        filing_date = str(payload["filingDate"])
        if len(filing_date) >= 10:
            period_end_date = filing_date[:10]
    payload["periodEndDate"] = period_end_date

    fiscal_year_end_month = payload.get("fiscalYearEndMonth")
    if not filename_date and fiscal_year_end_month is None:
        fiscal_year_end_month = parsed.get("fiscalYearEndMonth")
    if fiscal_year_end_month is None and period_end_date:
        try:
            fiscal_year_end_month = int(str(period_end_date).split("-")[1])
        except (ValueError, IndexError):
            pass
    payload["fiscalYearEndMonth"] = fiscal_year_end_month

    filing_type = str(payload.get("filingType", "")).upper()
    payload["isAmendment"] = (
        parsed.get("isAmendment", False)
        or payload.get("isAmendment", False)
        or filing_type.endswith("/A")
        or "AMEND" in filing_type
    )
    payload["language"] = (
        parsed.get("language")
        or payload.get("language")
        or ("en" if payload.get("documentType") == FILE_TYPE_HTML.upper() else None)
    )
    payload["pageCount"] = (
        parsed.get("page_count") or parsed.get("pageCount") or payload.get("pageCount")
    )
    payload["industryCodeRaw"] = payload.get("industryCodeRaw") or parsed.get(
        "industryCodeRaw"
    )


def detect_file_type(file_path: str) -> str:
    """Detect file type based on extension.

    Args:
        file_path: Path to file

    Returns:
        File type: 'pdf', 'html', 'htm', or 'unknown'
    """
    _, ext = os.path.splitext(file_path.lower())
    if ext == ".pdf":
        return FILE_TYPE_PDF
    elif ext in (".htm", ".html"):
        return FILE_TYPE_HTML
    else:
        return FILE_TYPE_UNKNOWN


def parse_document(file_path: str, ocr_enabled: bool = True) -> dict:
    """Parse document content and extract text and tables based on file type.

    Supports PDF and HTML files. Automatically detects format and applies
    appropriate parsing strategy.

    Args:
        file_path: Absolute path to document file (PDF or HTML)

    Returns:
        dict with keys:
            - text (str): Extracted text content
            - tables (list): Extracted structured tables
            - table_cnt (int): Number of tables extracted
            - reason (str): Parsing method used or error description
    """
    file_type = detect_file_type(file_path)

    if file_type == FILE_TYPE_PDF:
        return parse_pdf_layout(
            file_path, force_ocr=False, use_ocr=ocr_enabled
        )
    elif file_type == FILE_TYPE_HTML:
        return parse_html_file(file_path)
    else:
        return {
            "text": "",
            "tables": [],
            "table_cnt": 0,
            "reason": f"Unsupported file type: {file_type}",
        }


async def run_ingestion_pipeline(
    metadata_input: dict,
    file_path: str,
    source: str,
    dry_run: bool = False,
    llm_callable: Callable[[str], str] | None = None,
    ocr_enabled: bool = True,
) -> bool:
    """Execute complete document ingestion pipeline with extraction, validation, and database commit.

    Processes a single document through the following stages:
    1. Document parsing (text and table extraction)
    2. Metadata enrichment with SEC Edgar data
    3. Pydantic schema validation
    4. Database upsert and relationship establishment, unless ``dry_run`` is true

    Args:
        metadata_input: Dictionary with filing metadata (filingId, companyTicker, etc.)
        file_path: Absolute path to document file to process
        source: Source identifier ('SEC', 'LOCAL', etc.)
        dry_run: Parse and validate without writing to the database.

    Returns:
        bool: True if processing completed successfully, False otherwise
    """
    filing_id = _resolve_filing_id(metadata_input, file_path)
    start_time = time.time()

    log.info(
        f"🚀 Ingestion request initialized for filing ID: {filing_id} | Path: {file_path}"
    )
    payload = {
        **metadata_input,
        "filingId": filing_id,
        "source": source.upper(),
    }
    _ensure_source_type(payload)
    payload["periodEndDate"] = metadata_input.get("periodEndDate")
    payload["fiscalYearEndMonth"] = metadata_input.get("fiscalYearEndMonth")
    payload["isAmendment"] = metadata_input.get("isAmendment", False)
    payload["language"] = metadata_input.get("language")
    payload["pageCount"] = metadata_input.get("pageCount")
    payload["industryCodeRaw"] = metadata_input.get("industryCodeRaw")
    payload["gicsCode"] = metadata_input.get("gicsCode")
    payload["gicsSector"] = metadata_input.get("gicsSector")
    payload["industryConfidence"] = metadata_input.get("industryConfidence")
    payload["xbrlCurrency"] = None
    payload["xbrlProvenance"] = None
    payload["xbrlSource"] = None

    if not os.path.exists(file_path):
        log.error(f"❌ File missing on target file execution block path: {file_path}")
        payload.update(
            {
                "documentStatus": "FAILED",
                "documentStatusReason": "Binary file missing from local path",
            }
        )
    else:
        try:
            log.debug(
                f"🔍 Executing layout parser vector maps for target: {os.path.basename(file_path)}"
            )
            parsed = parse_document(file_path, ocr_enabled=ocr_enabled)
            if payload.get("sourceType") == "structured_filing":
                form_type = payload.get("filingType") or metadata_input.get(
                    "filingType"
                )
                parsed["chunks"] = label_sections_structured(
                    parsed.get("chunks", []), form_type=form_type
                )
            elif payload.get("sourceType") == "glossy_report" and llm_callable:
                parsed["chunks"] = label_sections_glossy(
                    parsed.get("chunks", []), llm_callable
                )
            if detect_file_type(file_path) == FILE_TYPE_HTML:
                accession_number = str(metadata_input.get("filingId", "")).removeprefix(
                    "sec_"
                )
                parsed = enrich_filing_metadata_with_edgartools(
                    parsed, accession_number
                )

            payload.update(
                {
                    "documentHash": calculate_file_hash(file_path),
                    "documentSize": os.path.getsize(file_path),
                    "documentText": parsed["text"],
                    "documentTextLen": len(parsed["text"]),
                    "documentChunks": parsed.get("chunks", []),
                    "documentTables": parsed["tables"],
                    "documentTableCnt": parsed["table_cnt"],
                    "documentStatus": STATUS_PROCESSED,
                    "documentStatusReason": parsed["reason"]
                    + (
                        f"; {' | '.join(parsed['warnings'])}"
                        if parsed.get("warnings")
                        else ""
                    ),
                    "documentType": detect_file_type(file_path).upper(),
                }
            )
            _apply_extracted_metadata(parsed, payload)
            metadata_sources = parsed.get("metadataSources")
            if metadata_sources:
                payload["metadataSources"] = metadata_sources
            metadata_confidence = parsed.get("metadataConfidence")
            if metadata_confidence:
                payload["metadataConfidence"] = metadata_confidence
            for metadata_key in ("stockName", "filingDate", "filingType", "exchange"):
                existing_value = payload.get(metadata_key)
                if parsed.get(metadata_key) and existing_value in (None, "", "UNKNOWN"):
                    payload[metadata_key] = parsed[metadata_key]

            _ensure_source_type(payload)
            if detect_file_type(file_path) == FILE_TYPE_HTML:
                _populate_xbrl_fields(payload)

            if isinstance(payload.get("metadataSources"), dict):
                payload["metadataSources"] = json.dumps(
                    payload["metadataSources"], ensure_ascii=False
                )
            if isinstance(payload.get("metadataConfidence"), dict):
                payload["metadataConfidence"] = json.dumps(
                    payload["metadataConfidence"], ensure_ascii=False
                )
            log.success(
                f"📝 Text & layout extraction parsed successfully via strategy: {parsed['reason']}"
            )

        except (OSError, ValueError, KeyError) as exc:
            log.exception(
                f"💥 Failed parser engine step processing file layout layers: {file_path}"
            )
            payload.update(
                {
                    "documentStatus": STATUS_FAILED,
                    "documentStatusReason": f"Layout Error: {exc!s}",
                }
            )

    for field in (
        "documentChunks",
        "documentTables",
        "xbrlFacts",
        "xbrlProvenance",
    ):
        if field in payload and payload[field] is not None:
            payload[field] = json.dumps(payload[field], ensure_ascii=False)

    try:
        validated_record = ExchangeFilingModel(**payload).model_dump()
        record_id = f"{FILING_TABLE}:{validated_record['filingId']}"
    except ValueError as validation_err:
        log.critical(
            f"🛡️ Pydantic schema validation blocked record initialization: {filing_id} | Errors: {validation_err}"
        )
        return False

    if dry_run:
        log.info(
            f"🧪 Dry run validated filing record {record_id}; no database changes made"
        )
        return True

    try:
        log.debug(
            f"💾 Opening connection channel block to SurrealDB for record update: {record_id}"
        )
        async with SurrealConnection() as db:
            await db.delete_record(record_id)
            await db.upsert(record_id, validated_record)

            if validated_record["documentStatus"] == "PROCESSED":
                log.debug(
                    f"🕸️ Constructing graph mapping edge references for target: {record_id}"
                )
                await establish_graph_relations(
                    db=db,
                    filing_id=record_id,
                    owning_ticker=validated_record["companyTicker"],
                    owning_company_name=validated_record.get("stockName"),
                    owning_exchange=validated_record.get("exchange"),
                    referenced_tickers=validated_record.get("referencedTickers", []),
                )

        elapsed_time = time.time() - start_time
        log.info(
            f"🏆 Successfully indexed filing record {record_id} into SurrealDB in {elapsed_time:.2f}s"
        )
        return True

    except (OSError, RuntimeError, ValueError, KeyError) as db_err:
        log.error(
            f"📡 Database adapter failed to securely commit transaction records for id: {record_id} | Err: {db_err}"
        )
        return False


async def run_html_content_pipeline(
    metadata_input: dict,
    html_content: str,
    source: str = "SEC",
    dry_run: bool = False,
    llm_callable: Callable[[str], str] | None = None,
) -> bool:
    """Parse and ingest SEC HTML held in memory, without writing it to disk."""
    supplied_filing_id = metadata_input.get("filingId")
    filing_id = (
        supplied_filing_id.strip()
        if isinstance(supplied_filing_id, str) and supplied_filing_id.strip()
        else f"html_{hashlib.sha256(html_content.encode('utf-8')).hexdigest()[:32]}"
    )
    start_time = time.time()
    payload = {
        **metadata_input,
        "filingId": filing_id,
        "source": source.upper(),
    }
    _ensure_source_type(payload)
    payload["periodEndDate"] = metadata_input.get("periodEndDate")
    payload["fiscalYearEndMonth"] = metadata_input.get("fiscalYearEndMonth")
    payload["isAmendment"] = metadata_input.get("isAmendment", False)
    payload["language"] = metadata_input.get("language")
    payload["pageCount"] = metadata_input.get("pageCount")
    payload["industryCodeRaw"] = metadata_input.get("industryCodeRaw")
    payload["gicsCode"] = metadata_input.get("gicsCode")
    payload["gicsSector"] = metadata_input.get("gicsSector")
    payload["industryConfidence"] = metadata_input.get("industryConfidence")
    payload["xbrlCurrency"] = None
    payload["xbrlProvenance"] = None
    payload["xbrlSource"] = None
    try:
        parsed = parse_html_content(html_content)
        if payload.get("sourceType") == "structured_filing":
            form_type = payload.get("filingType") or metadata_input.get("filingType")
            parsed["chunks"] = label_sections_structured(
                parsed.get("chunks", []), form_type=form_type
            )
        elif payload.get("sourceType") == "glossy_report" and llm_callable:
            parsed["chunks"] = label_sections_glossy(
                parsed.get("chunks", []), llm_callable
            )
        accession_number = str(filing_id).removeprefix("sec_")
        parsed = enrich_filing_metadata_with_edgartools(parsed, accession_number)
        content_bytes = html_content.encode("utf-8")
        payload.update(
            {
                "documentHash": hashlib.sha256(content_bytes).hexdigest(),
                "documentSize": len(content_bytes),
                "documentText": parsed["text"],
                "documentTextLen": len(parsed["text"]),
                "documentChunks": parsed.get("chunks", []),
                "documentTables": parsed["tables"],
                "documentTableCnt": parsed["table_cnt"],
                "documentStatus": "PROCESSED",
                "documentStatusReason": parsed["reason"],
                "documentType": "HTML",
            }
        )
        _apply_extracted_metadata(parsed, payload)
        _populate_xbrl_fields(payload)
        if metadata_input.get("exhibitSectionMap"):
            payload["exhibitSectionMap"] = metadata_input["exhibitSectionMap"]
        metadata_sources = parsed.get("metadataSources")
        if metadata_sources:
            payload["metadataSources"] = metadata_sources
        metadata_confidence = parsed.get("metadataConfidence")
        if metadata_confidence:
            payload["metadataConfidence"] = metadata_confidence
        for metadata_key in ("stockName", "filingDate", "filingType", "exchange"):
            if parsed.get(metadata_key) and payload.get(metadata_key) in (
                None,
                "",
                "UNKNOWN",
            ):
                payload[metadata_key] = parsed[metadata_key]
        _ensure_source_type(payload)
        if isinstance(payload.get("metadataSources"), dict):
            payload["metadataSources"] = json.dumps(
                payload["metadataSources"], ensure_ascii=False
            )
        if isinstance(payload.get("metadataConfidence"), dict):
            payload["metadataConfidence"] = json.dumps(
                payload["metadataConfidence"], ensure_ascii=False
            )
    except (OSError, ValueError) as exc:
        log.exception(f"💥 Failed in-memory HTML parser for filing {filing_id}")
        payload.update(
            {
                "documentStatus": STATUS_FAILED,
                "documentStatusReason": f"Layout Error: {exc}",
            }
        )

    for field in (
        "documentChunks",
        "documentTables",
        "xbrlFacts",
        "xbrlProvenance",
    ):
        if field in payload and payload[field] is not None:
            payload[field] = json.dumps(payload[field], ensure_ascii=False)

    try:
        validated_record = ExchangeFilingModel(**payload).model_dump()
        record_id = f"{FILING_TABLE}:{validated_record['filingId']}"
        if dry_run:
            log.info(
                f"🧪 Dry run validated streamed filing {record_id}; no database changes made"
            )
            return True

        async with SurrealConnection() as db:
            await db.delete_record(record_id)
            await db.upsert(record_id, validated_record)
            if validated_record["documentStatus"] == STATUS_PROCESSED:
                await establish_graph_relations(
                    db=db,
                    filing_id=record_id,
                    owning_ticker=validated_record["companyTicker"],
                    owning_company_name=validated_record.get("stockName"),
                    owning_exchange=validated_record.get("exchange"),
                    referenced_tickers=validated_record.get("referencedTickers", []),
                )
        log.info(
            f"🏆 Streamed filing {record_id} into SurrealDB in {time.time() - start_time:.2f}s"
        )
        return True
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        log.error(f"📡 Stream database commit failed for {filing_id}: {exc}")
        return False


async def process_entire_directory(
    dir_path: str,
    source_type: str = "LOCAL",
    concurrency_limit: int = 3,
    dry_run: bool = False,
    ocr_enabled: bool = True,
):
    """Recursively scan and ingest all documents from directory with concurrent processing.

    Uses async semaphore to limit concurrent file processing, preventing resource exhaustion.

    Args:
        dir_path: Root directory path containing documents
        source_type: Source identifier for records ('LOCAL', 'SEC', etc.)
        concurrency_limit: Maximum concurrent tasks
    """
    if not os.path.isdir(dir_path):
        log.error(
            f"❌ Directory validation failed: {dir_path} is not a valid directory"
        )
        return

    semaphore = asyncio.Semaphore(concurrency_limit)
    errors = []

    async def worker(file_path: str, meta: dict):
        async with semaphore:
            try:
                await run_ingestion_pipeline(
                    meta,
                    file_path,
                    source=source_type,
                    dry_run=dry_run,
                    ocr_enabled=ocr_enabled,
                )
            except (OSError, RuntimeError, ValueError) as e:
                error_msg = f"Worker execution failed for {file_path}: {e}"
                log.error(f"❌ {error_msg}")
                errors.append(error_msg)

    tasks = []
    for file_path, generated_meta in scan_directory(dir_path, recursive=True):
        tasks.append(worker(file_path, generated_meta))

    if not tasks:
        log.warning("🔍 No PDF assets detected inside target folder path.")
        return

    await asyncio.gather(*tasks, return_exceptions=True)

    if errors:
        log.warning(f"⚠️ Completed with {len(errors)} errors during batch processing")
        for error in errors:
            log.debug(f"  - {error}")


async def process_mixed_directory(
    dir_path: str,
    source_type: str = "LOCAL",
    concurrency_limit: int = 3,
    dry_run: bool = False,
    llm_callable: Callable[[str], str] | None = None,
    ocr_enabled: bool = True,
):
    """Recursively ingest both PDF and HTML files from a local directory."""
    if not os.path.isdir(dir_path):
        log.error(
            f"❌ Directory validation failed: {dir_path} is not a valid directory"
        )
        return

    semaphore = asyncio.Semaphore(concurrency_limit)
    errors = []

    async def worker(file_path: str, meta: dict):
        async with semaphore:
            try:
                completed = await run_ingestion_pipeline(
                    meta,
                    file_path,
                    source=source_type,
                    dry_run=dry_run,
                    llm_callable=llm_callable,
                    ocr_enabled=ocr_enabled,
                )
                if not completed:
                    errors.append(f"Document processing failed for {file_path}")
            except (OSError, RuntimeError, ValueError) as e:
                error_msg = f"Worker execution failed for {file_path}: {e}"
                log.error(f"❌ {error_msg}")
                errors.append(error_msg)

    tasks = [
        worker(file_path, generated_meta)
        for file_path, generated_meta in scan_documents(dir_path, recursive=True)
    ]
    if not tasks:
        log.warning("🔍 No PDF or HTML assets detected inside target folder path.")
        return

    await asyncio.gather(*tasks, return_exceptions=True)
    if errors:
        log.warning(f"⚠️ Completed with {len(errors)} errors during batch processing")
        for error in errors:
            log.debug(f"  - {error}")


async def process_sec_edgar_html_directory(
    dir_path: str,
    source_type: str = "SEC",
    concurrency_limit: int = 3,
    dry_run: bool = False,
):
    """Process SEC Edgar HTML filings sequentially from directory structure.

    Directory structure: {dir_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html
    """
    from fin_pipeline.crawler import scan_sec_edgar_html_directory

    file_count = 0
    try:
        for file_path, generated_meta in scan_sec_edgar_html_directory(dir_path):
            file_count += 1
            log.info(f"📄 Processing filing {file_count}: {file_path}")
            completed = await run_ingestion_pipeline(
                generated_meta, file_path, source=source_type, dry_run=dry_run
            )
            if not completed:
                log.error(
                    "⏹️ Stopping SEC filing batch because the current record was not fully committed."
                )
                return
    except NotADirectoryError as e:
        log.error(f"❌ Directory error: {e}")
        return

    if file_count == 0:
        log.warning("🔍 No SEC Edgar HTML filings detected inside target folder path.")
        log.info(
            "   Expected structure: {base_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html"
        )
        return

    log.info(f"📋 Processed {file_count} SEC Edgar HTML filings sequentially.")


async def process_sec_edgar_csv(
    csv_path: str, download_dir: str = "sec_downloads", dry_run: bool = False
):
    """Fetch and ingest SEC filings listed in a CSV, sequentially."""
    from fin_pipeline.sec_edgar import fetch_filings_from_csv

    if not os.path.isfile(csv_path):
        log.error(f"❌ CSV file validation failed: {csv_path} is not a valid file")
        return False

    if not os.access(csv_path, os.R_OK):
        log.error(f"❌ CSV file permission error: {csv_path} is not readable")
        return False

    processed = 0
    for file_path, metadata in fetch_filings_from_csv(csv_path, download_dir):
        processed += 1
        log.info(f"📄 Processing downloaded SEC filing {processed}: {file_path}")
        if not await run_ingestion_pipeline(
            metadata, str(file_path), source="SEC", dry_run=dry_run
        ):
            log.error("⏹️ Stopping CSV SEC batch because the current filing failed.")
            return False

    log.info(f"📋 Processed {processed} SEC filings from CSV sequentially.")
    return True


async def process_sec_edgar_csv_stream(
    csv_path: str,
    year_range: tuple[int, int] | None = None,
    forms: list[str] | None = None,
    dry_run: bool = False,
):
    """Fetch and ingest SEC filings from CSV without local files."""
    from fin_pipeline.sec_edgar import SecEdgarFetchError, stream_filings_from_csv

    processed = 0
    skipped = 0
    failed = 0
    try:
        for html_content, metadata in stream_filings_from_csv(
            csv_path, year_range=year_range, forms=forms
        ):
            processed += 1
            if not await run_html_content_pipeline(
                metadata, html_content, dry_run=dry_run
            ):
                failed += 1
                log.error(
                    "⏹️ Stopping streamed SEC batch because the current filing failed."
                )
                return False
    except SecEdgarFetchError as exc:
        failed += 1
        log.error(f"⏹️ SEC stream stopped because a filing could not be downloaded: {exc}")
        return False

    log.info(
        f"📋 SEC stream summary: processed={processed}, skipped={skipped}, failed={failed}."
    )
    return True
