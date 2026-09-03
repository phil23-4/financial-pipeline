import os
import time
import asyncio
import hashlib
from fin_pipeline.config.logger import pipeline_logger as log
from fin_pipeline.config.constants import (
    FILING_TABLE, STATUS_PROCESSED, STATUS_FAILED, SOURCE_LOCAL,
    FILE_TYPE_PDF, FILE_TYPE_HTML, FILE_TYPE_UNKNOWN
)
from fin_pipeline.models.schemas import ExchangeFilingModel
from fin_pipeline.utils.crypto import calculate_file_hash
from fin_pipeline.utils.pdf_parser import parse_pdf_layout
from fin_pipeline.utils.html_parser import parse_html_file, parse_html_content, enrich_filing_metadata_with_edgartools
from fin_pipeline.db.connection import SurrealConnection
from fin_pipeline.db.relations import establish_graph_relations
from fin_pipeline.crawler import scan_directory

def detect_file_type(file_path: str) -> str:
    """Detect file type based on extension.
    
    Args:
        file_path: Path to file
        
    Returns:
        File type: 'pdf', 'html', or 'unknown'
    """
    _, ext = os.path.splitext(file_path.lower())
    if ext == '.pdf':
        return FILE_TYPE_PDF
    elif ext == '.html':
        return FILE_TYPE_HTML
    else:
        return FILE_TYPE_UNKNOWN

def parse_document(file_path: str) -> dict:
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
        return parse_pdf_layout(file_path)
    elif file_type == FILE_TYPE_HTML:
        return parse_html_file(file_path)
    else:
        return {
            "text": "",
            "tables": [],
            "table_cnt": 0,
            "reason": f"Unsupported file type: {file_type}"
        }

async def run_ingestion_pipeline(metadata_input: dict, file_path: str, source: str) -> bool:
    """Execute complete document ingestion pipeline with extraction, validation, and database commit.
    
    Processes a single document through the following stages:
    1. Document parsing (text and table extraction)
    2. Metadata enrichment with SEC Edgar data
    3. Pydantic schema validation
    4. Database upsert and relationship establishment
    
    Args:
        metadata_input: Dictionary with filing metadata (filingId, companyTicker, etc.)
        file_path: Absolute path to document file to process
        source: Source identifier ('SEC', 'LOCAL', etc.)
        
    Returns:
        bool: True if processing completed successfully, False otherwise
    """
    filing_id = metadata_input.get("filingId", "UNKNOWN_ID")
    start_time = time.time()
    
    log.info(f"🚀 Ingestion request initialized for filing ID: {filing_id} | Path: {file_path}")
    payload = {**metadata_input, "source": source.upper()}

    if not os.path.exists(file_path):
        log.error(f"❌ File missing on target file execution block path: {file_path}")
        payload.update({"documentStatus": "FAILED", "documentStatusReason": "Binary file missing from local path"})
    else:
        try:
            log.debug(f"🔍 Executing layout parser vector maps for target: {os.path.basename(file_path)}")
            parsed = parse_document(file_path)
            if detect_file_type(file_path) == FILE_TYPE_HTML:
                accession_number = str(metadata_input.get("filingId", "")).removeprefix("sec_")
                parsed = enrich_filing_metadata_with_edgartools(parsed, accession_number)
            
            payload.update({
                "documentHash": calculate_file_hash(file_path),
                "documentSize": os.path.getsize(file_path),
                "documentText": parsed["text"],
                "documentTextLen": len(parsed["text"]),
                "documentTables": parsed["tables"],
                "documentTableCnt": parsed["table_cnt"],
                "documentStatus": STATUS_PROCESSED,
                "documentStatusReason": parsed["reason"],
                "documentType": detect_file_type(file_path).upper()
            })
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
            log.success(f"📝 Text & layout extraction parsed successfully via strategy: {parsed['reason']}")
            
        except (IOError, OSError, ValueError, KeyError) as exc:
            log.exception(f"💥 Failed parser engine step processing file layout layers: {file_path}")
            payload.update({"documentStatus": STATUS_FAILED, "documentStatusReason": f"Layout Error: {str(exc)}"})

    try:
        validated_record = ExchangeFilingModel(**payload).model_dump()
        record_id = f"{FILING_TABLE}:{validated_record['filingId']}"
    except ValueError as validation_err:
        log.critical(f"🛡️ Pydantic schema validation blocked record initialization: {filing_id} | Errors: {validation_err}")
        return False


    try:
        log.debug(f"💾 Opening connection channel block to SurrealDB for record update: {record_id}")
        async with SurrealConnection() as db:
            await db.delete_record(record_id)
            await db.upsert(record_id, validated_record)
            
            if validated_record["documentStatus"] == "PROCESSED":
                log.debug(f"🕸️ Constructing graph mapping edge references for target: {record_id}")
                await establish_graph_relations(
                    db=db,
                    filing_id=record_id,
                    owning_ticker=validated_record["companyTicker"],
                    owning_company_name=validated_record.get("stockName"),
                    owning_exchange=validated_record.get("exchange"),
                    referenced_tickers=validated_record.get("referencedTickers", [])
                )
        
        elapsed_time = time.time() - start_time
        log.info(f"🏆 Successfully indexed filing record {record_id} into SurrealDB in {elapsed_time:.2f}s")
        return True
        
    except Exception as db_err:
        log.error(f"📡 Database adapter failed to securely commit transaction records for id: {record_id} | Err: {db_err}")
        return False


async def run_html_content_pipeline(metadata_input: dict, html_content: str, source: str = "SEC") -> bool:
    """Parse and ingest SEC HTML held in memory, without writing it to disk."""
    filing_id = metadata_input.get("filingId", "UNKNOWN_ID")
    start_time = time.time()
    payload = {**metadata_input, "source": source.upper()}
    try:
        parsed = parse_html_content(html_content)
        accession_number = str(filing_id).removeprefix("sec_")
        parsed = enrich_filing_metadata_with_edgartools(parsed, accession_number)
        content_bytes = html_content.encode("utf-8")
        payload.update({
            "documentHash": hashlib.sha256(content_bytes).hexdigest(),
            "documentSize": len(content_bytes),
            "documentText": parsed["text"],
            "documentTextLen": len(parsed["text"]),
            "documentTables": parsed["tables"],
            "documentTableCnt": parsed["table_cnt"],
            "documentStatus": "PROCESSED",
            "documentStatusReason": parsed["reason"],
            "documentType": "HTML",
        })
        metadata_sources = parsed.get("metadataSources")
        if metadata_sources:
            payload["metadataSources"] = metadata_sources
        metadata_confidence = parsed.get("metadataConfidence")
        if metadata_confidence:
            payload["metadataConfidence"] = metadata_confidence
        for metadata_key in ("stockName", "filingDate", "filingType", "exchange"):
            if parsed.get(metadata_key) and payload.get(metadata_key) in (None, "", "UNKNOWN"):
                payload[metadata_key] = parsed[metadata_key]
    except (IOError, OSError, ValueError) as exc:
        log.exception(f"💥 Failed in-memory HTML parser for filing {filing_id}")
        payload.update({"documentStatus": STATUS_FAILED, "documentStatusReason": f"Layout Error: {exc}"})

    try:
        validated_record = ExchangeFilingModel(**payload).model_dump()
        record_id = f"{FILING_TABLE}:{validated_record['filingId']}"
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
        log.info(f"🏆 Streamed filing {record_id} into SurrealDB in {time.time() - start_time:.2f}s")
        return True
    except (IOError, RuntimeError, ValueError, KeyError) as exc:
        log.error(f"📡 Stream database commit failed for {filing_id}: {exc}")
        return False


async def process_entire_directory(dir_path: str, source_type: str = "LOCAL", concurrency_limit: int = 3):
    """Recursively scan and ingest all documents from directory with concurrent processing.
    
    Uses async semaphore to limit concurrent file processing, preventing resource exhaustion.
    
    Args:
        dir_path: Root directory path containing documents
        source_type: Source identifier for records ('LOCAL', 'SEC', etc.)
        concurrency_limit: Maximum concurrent tasks
    """
    if not os.path.isdir(dir_path):
        log.error(f"❌ Directory validation failed: {dir_path} is not a valid directory")
        return
    
    semaphore = asyncio.Semaphore(concurrency_limit)
    errors = []

    async def worker(file_path: str, meta: dict):
        async with semaphore:
            try:
                await run_ingestion_pipeline(meta, file_path, source=source_type)
            except (IOError, RuntimeError, ValueError) as e:
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

async def process_sec_edgar_html_directory(dir_path: str, source_type: str = "SEC", concurrency_limit: int = 3):
    """Process SEC Edgar HTML filings sequentially from directory structure.
    
    Directory structure: {dir_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html
    """
    from fin_pipeline.crawler import scan_sec_edgar_html_directory
    
    file_count = 0
    try:
        for file_path, generated_meta in scan_sec_edgar_html_directory(dir_path):
            file_count += 1
            log.info(f"📄 Processing filing {file_count}: {file_path}")
            completed = await run_ingestion_pipeline(generated_meta, file_path, source=source_type)
            if not completed:
                log.error("⏹️ Stopping SEC filing batch because the current record was not fully committed.")
                return
    except NotADirectoryError as e:
        log.error(f"❌ Directory error: {e}")
        return

    if file_count == 0:
        log.warning("🔍 No SEC Edgar HTML filings detected inside target folder path.")
        log.info("   Expected structure: {base_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html")
        return

    log.info(f"📋 Processed {file_count} SEC Edgar HTML filings sequentially.")


async def process_sec_edgar_csv(csv_path: str, download_dir: str = "sec_downloads"):
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
        if not await run_ingestion_pipeline(metadata, str(file_path), source="SEC"):
            log.error("⏹️ Stopping CSV SEC batch because the current filing failed.")
            return False

    log.info(f"📋 Processed {processed} SEC filings from CSV sequentially.")
    return True


async def process_sec_edgar_csv_stream(
    csv_path: str,
    year_range: tuple[int, int] | None = None,
    forms: list[str] | None = None,
):
    """Fetch and ingest SEC filings from CSV without local files."""
    from fin_pipeline.sec_edgar import stream_filings_from_csv

    processed = 0
    for html_content, metadata in stream_filings_from_csv(
        csv_path, year_range=year_range, forms=forms
    ):
        processed += 1
        if not await run_html_content_pipeline(metadata, html_content):
            log.error("⏹️ Stopping streamed SEC batch because the current filing failed.")
            return False
    log.info(f"📋 Streamed {processed} SEC filings from CSV sequentially.")
    return True
