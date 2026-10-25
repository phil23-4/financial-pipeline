import csv
import re
from pathlib import Path

from fin_pipeline.config.logger import pipeline_logger as log

_DEFAULT_MAPPING_PATH = "data/industry_mapping.csv"


def load_industry_mapping(csv_path: str = _DEFAULT_MAPPING_PATH) -> dict:
    """Load the industry mapping CSV and return lookup structures."""
    mapping_file = Path(csv_path)
    if not mapping_file.exists():
        raise FileNotFoundError(
            "run the mapping curation script first — see data/industry_mapping.csv"
        )

    by_cik: dict[str, dict] = {}
    by_company_key: dict[str, dict] = {}
    by_ticker: dict[str, dict] = {}
    warnings: list[str] = []
    valid_rows = 0

    with mapping_file.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        for line_number, row in enumerate(reader, start=2):
            if row is None or not any((value or "").strip() for value in row.values()):
                continue

            company_key = (row.get("companyKey") or "").strip()
            cik = (row.get("cik") or "").strip()
            ticker = (row.get("ticker") or "").strip().upper()
            company_name = (row.get("company_name") or "").strip()
            gics_code = (row.get("gics_code") or "").strip()
            gics_name = (row.get("gics_name") or "").strip()
            gics_sector = (row.get("gics_sector") or "").strip()
            gics_sector_name = (row.get("gics_sector_name") or "").strip()
            confidence_raw = (row.get("confidence") or "").strip()
            source_name = (row.get("source") or "").strip()

            if not re.fullmatch(r"\d{8}", gics_code):
                warnings.append(
                    f"line {line_number}: skipped invalid gics_code '{gics_code}'"
                )
                continue

            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                warnings.append(
                    f"line {line_number}: skipped invalid confidence '{confidence_raw}'"
                )
                continue
            if not 0.0 <= confidence <= 1.0:
                warnings.append(
                    f"line {line_number}: skipped confidence outside 0.0-1.0 range"
                )
                continue

            if not company_key and not cik and not ticker:
                warnings.append(
                    f"line {line_number}: skipped row without companyKey/cik/ticker"
                )
                continue

            cleaned_entry = {
                "companyKey": company_key,
                "cik": cik.zfill(10) if cik else "",
                "ticker": ticker,
                "company_name": company_name,
                "country": (row.get("country") or "").strip(),
                "gics_code": gics_code,
                "gics_name": gics_name,
                "gics_sector": gics_sector or gics_code[:2],
                "gics_sector_name": gics_sector_name,
                "confidence": confidence,
                "source": source_name,
            }

            if cleaned_entry["cik"]:
                by_cik[cleaned_entry["cik"]] = cleaned_entry
            if company_key:
                by_company_key[company_key] = cleaned_entry
            if ticker:
                by_ticker[ticker] = cleaned_entry
            valid_rows += 1

    log.info(
        f"Loaded industry mapping: by_cik={len(by_cik)} by_companyKey={len(by_company_key)} by_ticker={len(by_ticker)}"
        f"valid_rows={valid_rows} skipped={len(warnings)}"
    )
    return {
        "by_cik": by_cik,
        "by_companyKey": by_company_key,
        "by_ticker": by_ticker,
        "row_count": valid_rows,
        "warnings": warnings,
    }


async def enrich_industry_codes(
    db,
    mapping_path: str = _DEFAULT_MAPPING_PATH,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Populate gicsCode, gicsSector, industryCodeRaw, and industryConfidence."""
    mapping = load_industry_mapping(mapping_path)
    query = (
        "SELECT filingId, companyTicker, cik, companyKey, gicsCode, "
        "gicsSector, industryCodeRaw, industryConfidence FROM exchange_filing "
        "WHERE gicsCode IS NONE"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    rows_response = await db.query_raw(query)
    rows = rows_response[0].get("result", []) if isinstance(rows_response, list) else []

    scanned = len(rows)
    matched = 0
    unmatched = 0
    updated = 0
    by_source = {"by_cik": 0, "by_companyKey": 0, "by_ticker": 0}
    sample: list[dict] = []

    for idx, row in enumerate(rows, start=1):
        filing_id = row.get("filingId")
        cik = str(row.get("cik") or "").strip()
        if cik:
            cik = cik.zfill(10)
        company_key = str(row.get("companyKey") or "").strip()
        ticker = str(row.get("companyTicker") or "").strip().upper()

        info = None
        match_source = None
        if cik and cik in mapping["by_cik"]:
            info = mapping["by_cik"][cik]
            match_source = "by_cik"
        elif company_key and company_key in mapping["by_companyKey"]:
            info = mapping["by_companyKey"][company_key]
            match_source = "by_companyKey"
        elif ticker and ticker in mapping["by_ticker"]:
            info = mapping["by_ticker"][ticker]
            match_source = "by_ticker"

        if info is None:
            unmatched += 1
            continue

        matched += 1
        if match_source is not None:
            by_source[match_source] = by_source.get(match_source, 0) + 1

        before = {
            "gicsCode": row.get("gicsCode"),
            "gicsSector": row.get("gicsSector"),
            "industryCodeRaw": row.get("industryCodeRaw"),
            "industryConfidence": row.get("industryConfidence"),
        }
        after = {
            "gicsCode": info["gics_code"],
            "gicsSector": info["gics_sector"],
            "industryConfidence": float(info["confidence"]),
        }

        if idx % 100 == 0:
            log.info("enriched %s/%s (matched=%s)", idx, scanned, matched)

        if not dry_run:
            payload = {
                "gicsCode": info["gics_code"],
                "gicsSector": info["gics_sector"],
                "industryConfidence": float(info["confidence"]),
            }
            if row.get("industryCodeRaw") in (None, "", "UNKNOWN", "NONE"):
                payload["industryCodeRaw"] = info["company_name"]
            await db.merge(f"exchange_filing:{filing_id}", payload)
            updated += 1

        if len(sample) < 5:
            sample.append({"filingId": filing_id, "before": before, "after": after})

    return {
        "scanned": scanned,
        "matched": matched,
        "updated": 0 if dry_run else updated,
        "unmatched": unmatched,
        "by_source": by_source,
        "dry_run": dry_run,
        "sample": sample,
    }


async def backfill_reporting_year(db, dry_run: bool = False) -> dict:
    """Backfill reportingYear from periodEndDate.year when missing."""
    rows_response = await db.query_raw(
        "SELECT filingId, periodEndDate, reportingYear FROM exchange_filing "
        "WHERE reportingYear IS NONE AND periodEndDate IS NOT NONE"
    )
    rows = rows_response[0].get("result", []) if isinstance(rows_response, list) else []

    scanned = len(rows)
    derivable = 0
    updated = 0
    skipped = 0

    for idx, row in enumerate(rows, start=1):
        period_end_date = row.get("periodEndDate")
        if period_end_date is None:
            skipped += 1
            continue
        derivable += 1
        try:
            year = int(str(period_end_date)[:4])
        except (TypeError, ValueError):
            skipped += 1
            continue
        if idx % 500 == 0:
            log.info("reportingYear backfill %s/%s", idx, scanned)
        if not dry_run:
            await db.merge(
                f"exchange_filing:{row['filingId']}", {"reportingYear": year}
            )
            updated += 1

    return {
        "scanned": scanned,
        "derivable": derivable,
        "updated": 0 if dry_run else updated,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def _load_cik_ticker_mapping(csv_path: str) -> dict[str, str]:
    cik_by_ticker: dict[str, str] = {}
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = {
            (fieldname or "").strip().lower() for fieldname in (reader.fieldnames or [])
        }
        if not {"ticker", "cik"}.issubset(fieldnames):
            raise ValueError("CIK CSV must include Ticker and CIK columns")

        for line_number, row in enumerate(reader, start=2):
            normalized_row = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in row.items()
            }
            ticker = normalized_row.get("ticker", "").upper()
            cik_raw = normalized_row.get("cik", "")
            if not ticker or not cik_raw:
                continue
            digits = "".join(character for character in cik_raw if character.isdigit())
            if not digits:
                raise ValueError(f"CIK CSV row {line_number} has no CIK digits")
            cik = digits.zfill(10)
            existing_cik = cik_by_ticker.get(ticker)
            if existing_cik is not None and existing_cik != cik:
                raise ValueError(
                    f"CIK CSV row {line_number} has conflicting CIK for {ticker}"
                )
            cik_by_ticker[ticker] = cik
    return cik_by_ticker


async def backfill_cik_from_csv(
    db, csv_path: str, dry_run: bool = False
) -> dict:
    """Backfill missing filing CIKs from a company ticker/CIK CSV."""
    cik_by_ticker = _load_cik_ticker_mapping(csv_path)

    rows_response = await db.query_raw(
        "SELECT filingId, companyTicker, cik FROM exchange_filing WHERE cik IS NONE"
    )
    rows = rows_response[0].get("result", []) if isinstance(rows_response, list) else []
    scanned = len(rows)
    matched = 0
    updated = 0

    for row in rows:
        if row.get("cik") not in (None, ""):
            continue
        ticker = str(row.get("companyTicker") or "").strip().upper()
        cik = cik_by_ticker.get(ticker)
        if not cik:
            continue
        matched += 1
        if not dry_run:
            await db.merge(
                f"exchange_filing:{row['filingId']}",
                {"cik": cik},
            )
            updated += 1

    return {
        "scanned": scanned,
        "matched": matched,
        "updated": 0 if dry_run else updated,
    }


async def log_low_quality_extractions(db) -> dict:
    """Return counts for suspicious extraction quality issues without modifying rows."""
    rows_response = await db.query_raw(
        "SELECT filingType, sourceType, COUNT() AS count FROM exchange_filing "
        "WHERE documentTextLen = 0 OR (documentTableCnt = 0 AND documentTextLen > 100000) "
        "GROUP BY filingType, sourceType"
    )
    rows = rows_response[0].get("result", []) if isinstance(rows_response, list) else []
    by_source_type: dict[str, int] = {}
    by_filing_type: dict[str, int] = {}
    for row in rows:
        source_type = row.get("sourceType") or "unknown"
        filing_type = row.get("filingType") or "unknown"
        count = int(row.get("count") or 0)
        by_source_type[source_type] = by_source_type.get(source_type, 0) + count
        by_filing_type[filing_type] = by_filing_type.get(filing_type, 0) + count
    return {
        "by_sourceType": by_source_type,
        "by_filingType": by_filing_type,
        "total": sum(by_source_type.values()),
    }
