import hashlib
import re
import unicodedata
from datetime import UTC, datetime

from loguru import logger as log
from surrealdb import Surreal

from fin_pipeline.config.settings import COMPANY_TABLE
from fin_pipeline.db.connection import _surrealql_literal
from fin_pipeline.db.db import surreal_query
from fin_pipeline.utils.db_utils import quote_record_id


def _normalized_company_name(company_name: str) -> str:
    """Normalize a company name for use in a stable company identity."""
    normalized = unicodedata.normalize("NFKC", company_name).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _company_key(ticker: str, company_name: str) -> str:
    """Build a stable key from a ticker and normalized company name."""
    identity = f"{ticker.strip().upper()}\0{_normalized_company_name(company_name)}"
    return f"co_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _query_rows(response: list | dict | None) -> list[dict]:
    if isinstance(response, list) and response and isinstance(response[0], dict):
        rows = response[0].get("result")
        return rows if isinstance(rows, list) else []
    return []


async def ensure_company_exists(
    db: Surreal,
    ticker: str,
    company_name: str | None = None,
    exchange: str | None = None,
):
    """Find or create a company record identified by ticker and company name."""
    if not COMPANY_TABLE:
        log.debug("Skipping company graph relation because COMPANY_TABLE is unset")
        return None

    if (
        not ticker.strip()
        or not company_name
        or not _normalized_company_name(company_name)
    ):
        log.warning(
            "Cannot create company graph relation without both a ticker and company name"
        )
        return None

    normalized_ticker = ticker.strip().upper()
    normalized_name = _normalized_company_name(company_name)
    company_key = _company_key(normalized_ticker, company_name)

    check_res = await db.query(
        f"SELECT id FROM {COMPANY_TABLE} "
        f"WHERE companyKey = {_surrealql_literal(company_key)} LIMIT 1;"
    )
    check_data = _query_rows(check_res)
    if check_data:
        return check_data[0]["id"]

    legacy_res = await db.query(
        f"SELECT id, companyName FROM {COMPANY_TABLE} "
        f"WHERE ticker = {_surrealql_literal(normalized_ticker)};"
    )
    for legacy_company in _query_rows(legacy_res):
        legacy_name = _normalized_company_name(
            str(legacy_company.get("companyName") or "")
        )
        if legacy_name != normalized_name and legacy_name != normalized_ticker.casefold():
            continue
        legacy_id = str(legacy_company["id"])
        migration_sql = (
            f"UPDATE {quote_record_id(legacy_id)} MERGE "
            f"{{ companyKey: {_surrealql_literal(company_key)}, "
            f"companyName: {_surrealql_literal(company_name.strip())} }};"
        )
        migration_result = surreal_query(migration_sql, timeout=30)
        if isinstance(migration_result, dict) and migration_result.get("error"):
            log.warning(
                f"Could not assign company key to existing company {legacy_id}: "
                f"{migration_result['error'][:200]}"
            )
            return None
        if isinstance(migration_result, list) and any(
            item.get("status") == "ERR"
            for item in migration_result
            if isinstance(item, dict)
        ):
            log.warning(
                f"Could not assign company key to existing company {legacy_id}: "
                f"{migration_result}"
            )
            return None
        return legacy_id

    company_id = f"{COMPANY_TABLE}:{company_key}"
    company_payload = {
        "companyKey": company_key,
        "ticker": normalized_ticker,
        "companyName": company_name.strip(),
        "updatedAt": datetime.now(UTC),
    }
    if exchange:
        company_payload["exchange"] = exchange

    sql = (
        f"UPSERT {quote_record_id(company_id)} "
        f"CONTENT {_surrealql_literal(company_payload)};"
    )
    result = surreal_query(sql, timeout=30)

    if isinstance(result, dict) and result.get("error"):
        log.warning(f"Could not auto-create company {ticker}: {result['error'][:200]}")
        return None
    if isinstance(result, list) and any(
        item.get("status") == "ERR" for item in result if isinstance(item, dict)
    ):
        log.warning(f"Could not auto-create company {ticker}: {result}")
        return None

    log.debug(f"Auto-created company record: {company_id}")
    return company_id


async def establish_graph_relations(
    db: Surreal,
    filing_id: str,
    owning_ticker: str,
    owning_company_name: str | None = None,
    owning_exchange: str | None = None,
    referenced_tickers: list[str] | None = None,
):
    """Safely runs SurrealQL RELATE transactions, adhering to type safety rules.

    Auto-creates company records if they don't exist.
    """
    if referenced_tickers is None:
        referenced_tickers = []

    # Ensure owning company exists
    owner_id = await ensure_company_exists(
        db, owning_ticker, owning_company_name, owning_exchange
    )
    if owner_id:
        owner_ref = quote_record_id(owner_id)
        filing_ref = quote_record_id(filing_id)
        sql = f"DELETE has_filing WHERE in = {owner_ref} AND out = {filing_ref}; RELATE {owner_ref}->has_filing->{filing_ref};"
        await db.query(sql)

    # Ensure referenced companies exist
    if referenced_tickers:
        for ref_ticker in referenced_tickers:
            ref_id = await ensure_company_exists(db, ref_ticker)
            if ref_id:
                filing_ref = quote_record_id(filing_id)
                ref_ref = quote_record_id(ref_id)
                sql = f"DELETE references_filing WHERE in = {filing_ref} AND out = {ref_ref}; RELATE {filing_ref}->references_filing->{ref_ref};"
                await db.query(sql)
