from surrealdb import Surreal
from fin_pipeline.config.settings import COMPANY_TABLE

async def establish_graph_relations(db: Surreal, filing_id: str, owning_ticker: str, referenced_tickers: list):
    """Safely runs SurrealQL RELATE transactions, adhering to type safety rules."""
    owner_res = await db.query(f"SELECT id FROM {COMPANY_TABLE} WHERE ticker = $ticker LIMIT 1;", {"ticker": owning_ticker})
    owner_data = owner_res[0].get("result") if owner_res else None
    
    if owner_data and len(owner_data) > 0:
        await db.query("RELATE $company_id->has_filing->$filing_id SET createdAt = time::now();", {"company_id": owner_data[0]["id"], "filing_id": filing_id})

    if referenced_tickers:
        for ref_ticker in referenced_tickers:
            ref_res = await db.query(f"SELECT id FROM {COMPANY_TABLE} WHERE ticker = $ticker LIMIT 1;", {"ticker": ref_ticker})
            ref_data = ref_res[0].get("result") if ref_res else None
            if ref_data and len(ref_data) > 0:
                await db.query(
                    "RELATE $filing_id->references_filing->$ref_id SET createdAt = time::now(), source = 'extraction_pipeline';", 
                    {"filing_id": filing_id, "ref_id": ref_data[0]["id"]}
                )
