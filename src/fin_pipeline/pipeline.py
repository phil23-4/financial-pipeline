import os
import time
import asyncio
from fin_pipeline.config.logger import pipeline_logger as log
from fin_pipeline.models.schemas import ExchangeFilingModel
from fin_pipeline.utils.crypto import calculate_file_hash
from fin_pipeline.utils.pdf_parser import parse_pdf_layout
from fin_pipeline.utils.html_parser import parse_html_file, enrich_filing_metadata_with_edgartools
from fin_pipeline.db.connection import SurrealConnection
from fin_pipeline.db.relations import establish_graph_relations
from fin_pipeline.crawler import scan_directory

def detect_file_type(file_path: str) -> str:
    """Detect file type based on extension."""
    _, ext = os.path.splitext(file_path.lower())
    if ext == '.pdf':
        return 'pdf'
    elif ext == '.html':
        return 'html'
    else:
        return 'unknown'

def parse_document(file_path: str) -> dict:
    """Parse document based on file type (PDF or HTML).
    
    Returns:
        dict with keys: text, tables, table_cnt, reason
    """
    file_type = detect_file_type(file_path)
    
    if file_type == 'pdf':
        return parse_pdf_layout(file_path)
    elif file_type == 'html':
        return parse_html_file(file_path)
    else:
        return {
            "text": "",
            "tables": [],
            "table_cnt": 0,
            "reason": f"Unsupported file type: {file_type}"
        }

async def run_ingestion_pipeline(metadata_input: dict, file_path: str, source: str) -> bool:
    """Executes single file pipeline extraction layers, schema checks, and database commits."""
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
            if detect_file_type(file_path) == "html":
                accession_number = str(metadata_input.get("filingId", "")).removeprefix("sec_")
                parsed = enrich_filing_metadata_with_edgartools(parsed, accession_number)
            
            payload.update({
                "documentHash": calculate_file_hash(file_path),
                "documentSize": os.path.getsize(file_path),
                "documentText": parsed["text"],
                "documentTextLen": len(parsed["text"]),
                "documentTables": parsed["tables"],
                "documentTableCnt": parsed["table_cnt"],
                "documentStatus": "PROCESSED",
                "documentStatusReason": parsed["reason"],
                "documentType": detect_file_type(file_path).upper()
            })
            for metadata_key in ("stockName", "filingDate", "filingType", "exchange"):
                existing_value = payload.get(metadata_key)
                if parsed.get(metadata_key) and existing_value in (None, "", "UNKNOWN"):
                    payload[metadata_key] = parsed[metadata_key]
            log.success(f"📝 Text & layout extraction parsed successfully via strategy: {parsed['reason']}")
            
        except Exception as exc:
            log.exception(f"💥 Failed parser engine step processing file layout layers: {file_path}")
            payload.update({"documentStatus": "FAILED", "documentStatusReason": f"Layout Error: {str(exc)}"})

    try:
        validated_record = ExchangeFilingModel(**payload).model_dump()
        record_id = f"exchange_filing:{validated_record['filingId']}"
    except Exception as validation_err:
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

async def process_entire_directory(dir_path: str, source_type: str = "LOCAL", concurrency_limit: int = 3):
    """Executes full concurrent folder scans regulated via thread token boundaries (semaphores)."""
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def worker(file_path: str, meta: dict):
        async with semaphore:
            try:
                await run_ingestion_pipeline(meta, file_path, source=source_type)
            except Exception as e:
                log.error(f"❌ Worker thread execution failed for file {file_path}: {e}")

    tasks = []
    for file_path, generated_meta in scan_directory(dir_path, recursive=True):
        tasks.append(worker(file_path, generated_meta))

    if not tasks:
        log.warning("🔍 No PDF assets detected inside target folder path.")
        return

    await asyncio.gather(*tasks)

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
