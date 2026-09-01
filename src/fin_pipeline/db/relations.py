from surrealdb import Surreal
from fin_pipeline.config.settings import COMPANY_TABLE
from fin_pipeline.db.db import surreal_query
from fin_pipeline.db.connection import _surrealql_literal
from loguru import logger as log


def _quote_record_id(record_id: str) -> str:
    table, _, key = record_id.partition(":")
    return f"{table}:⟨{key}⟩" if key else record_id

async def ensure_company_exists(db: Surreal, ticker: str, company_name: str = None, exchange: str = None):
    """Auto-create a company record if it doesn't exist."""
    # Check if company exists
    check_res = await db.query(f"SELECT id FROM {COMPANY_TABLE} WHERE ticker = $ticker LIMIT 1;", {"ticker": ticker})
    check_data = check_res[0].get("result") if check_res else None
    
    if check_data and len(check_data) > 0:
        return check_data[0]["id"]  # Already exists
    
    # Create new company record using UPSERT
    from datetime import datetime, timezone
    company_id = f"{COMPANY_TABLE}:{ticker}"
    company_payload = {
        "ticker": ticker,
        "companyName": company_name or ticker,
        "updatedAt": datetime.now(timezone.utc)
    }
    if exchange:
        company_payload["exchange"] = exchange
    
    # Use UPSERT with SurrealQL literal
    sql = f"UPSERT {company_id} CONTENT {_surrealql_literal(company_payload)};"
    result = surreal_query(sql, timeout=30)
    
    if isinstance(result, dict) and result.get("error"):
        log.warning(f"Could not auto-create company {ticker}: {result['error'][:200]}")
        return None
    if isinstance(result, list) and any(item.get("status") == "ERR" for item in result if isinstance(item, dict)):
        log.warning(f"Could not auto-create company {ticker}: {result}")
        return None
    
    log.debug(f"Auto-created company record: {company_id}")
    return company_id

async def establish_graph_relations(db: Surreal, filing_id: str, owning_ticker: str, owning_company_name: str = None, owning_exchange: str = None, referenced_tickers: list = None):
    """Safely runs SurrealQL RELATE transactions, adhering to type safety rules.
    
    Auto-creates company records if they don't exist.
    """
    if referenced_tickers is None:
        referenced_tickers = []
    
    # Ensure owning company exists
    owner_id = await ensure_company_exists(db, owning_ticker, owning_company_name, owning_exchange)
    if owner_id:
        owner_ref = _quote_record_id(owner_id)
        filing_ref = _quote_record_id(filing_id)
        sql = f"DELETE has_filing WHERE in = {owner_ref} AND out = {filing_ref}; RELATE {owner_ref}->has_filing->{filing_ref};"
        await db.query(sql)

    # Ensure referenced companies exist
    if referenced_tickers:
        for ref_ticker in referenced_tickers:
            ref_id = await ensure_company_exists(db, ref_ticker)
            if ref_id:
                filing_ref = _quote_record_id(filing_id)
                ref_ref = _quote_record_id(ref_id)
                sql = f"DELETE references_filing WHERE in = {filing_ref} AND out = {ref_ref}; RELATE {filing_ref}->references_filing->{ref_ref};"
                await db.query(sql)
