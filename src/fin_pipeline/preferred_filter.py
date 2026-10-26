import csv
import os
import re
from collections import defaultdict

from fin_pipeline.config.logger import pipeline_logger as log

_PREFERRED_PATTERN = re.compile(r"^[A-Z0-9]+-[A-Z]{2,}[A-Z0-9]*$")


def is_preferred_ticker(ticker: str | None) -> bool:
    """Return whether a ticker has a multi-letter preferred-share suffix."""
    if not ticker:
        return False
    return bool(_PREFERRED_PATTERN.match(ticker.strip().upper()))


def _row_value(row: dict, *names: str) -> str:
    normalized = {
        str(key).strip().lower(): value
        for key, value in row.items()
        if key is not None
    }
    for name in names:
        value = normalized.get(name.lower())
        if value is not None:
            return str(value).strip()
    return ""


def _normalized_cik(cik: str) -> str:
    digits = "".join(character for character in cik if character.isdigit())
    return digits.zfill(10) if digits else ""


def find_preferred_rows(rows: list[dict]) -> dict:
    """Separate preferred tickers with plain siblings from rows to preserve."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cik = _normalized_cik(_row_value(row, "cik"))
        if cik:
            groups[cik].append(row)
        else:
            groups[f"\0empty-cik-{len(groups)}"].append(row)

    drop = []
    keep = []
    flagged = []
    for group_rows in groups.values():
        has_plain_ticker = any(
            not is_preferred_ticker(_row_value(row, "ticker"))
            for row in group_rows
        )
        for row in group_rows:
            ticker = _row_value(row, "ticker")
            if not is_preferred_ticker(ticker):
                keep.append(row)
            elif has_plain_ticker and _normalized_cik(_row_value(row, "cik")):
                drop.append(row)
            else:
                keep.append(row)
                flagged.append(ticker)
    return {"drop": drop, "keep": keep, "flagged": flagged}


def _summary_row(row: dict) -> dict:
    return {
        "ticker": _row_value(row, "ticker"),
        "cik": _row_value(row, "cik"),
        "company": _row_value(row, "company", "companyname", "company_name", "name"),
    }


def filter_csv(
    input_path: str,
    output_path: str,
    dry_run: bool = False,
) -> dict:
    """Filter preferred-share rows while preserving input CSV headers and order."""
    if not dry_run and os.path.realpath(input_path) == os.path.realpath(output_path):
        raise ValueError("Output CSV must differ from the input CSV")

    with open(input_path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise ValueError("Input CSV must include a header row")
        headers = {field.strip().lower() for field in fieldnames if field}
        if not {"ticker", "cik"}.issubset(headers):
            raise ValueError("Input CSV must include Ticker and CIK columns")
        rows = list(reader)

    result = find_preferred_rows(rows)
    dropped_ids = {id(row) for row in result["drop"]}
    output_rows = [row for row in rows if id(row) not in dropped_ids]

    if not dry_run:
        with open(output_path, "w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_rows)

    return {
        "input_rows": len(rows),
        "output_rows": len(output_rows),
        "dropped": [_summary_row(row) for row in result["drop"]],
        "kept_flagged": [
            _summary_row(row)
            for row in result["keep"]
            if is_preferred_ticker(_row_value(row, "ticker"))
        ],
        "output_path": output_path,
        "dry_run": dry_run,
    }


async def delete_ingested_preferred(
    db,
    filtered_csv_path: str,
    dry_run: bool = False,
) -> dict:
    """Delete preferred-ticker filing records only when a plain sibling remains."""
    plain_siblings = _load_plain_siblings(filtered_csv_path)

    response = await db.query_raw(
        "SELECT filingId, companyTicker, cik FROM exchange_filing "
        "WHERE string::contains(companyTicker, '-')"
    )
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(response["error"])
    if isinstance(response, list) and any(
        isinstance(item, dict) and item.get("status") == "ERR"
        for item in response
    ):
        raise RuntimeError("SurrealDB preferred-record query failed")
    records = response[0].get("result", []) if isinstance(response, list) else []
    scanned = 0
    deleted = 0
    kept = 0
    deleted_filing_ids = []

    for record in records:
        ticker = str(record.get("companyTicker") or "").strip()
        if not is_preferred_ticker(ticker):
            continue
        scanned += 1
        cik = _normalized_cik(str(record.get("cik") or "").strip())
        plain_ticker = ticker.split("-", 1)[0].upper()
        if cik and (cik, plain_ticker) in plain_siblings:
            filing_id = record.get("filingId")
            deleted += 1
            deleted_filing_ids.append(filing_id)
            if not dry_run:
                await db.delete_record(f"exchange_filing:{filing_id}")
                log.info(f"Deleted preferred-share filing {filing_id} ({ticker})")
        else:
            kept += 1

    return {
        "scanned": scanned,
        "deleted": deleted,
        "kept": kept,
        "deleted_filing_ids": deleted_filing_ids,
    }


def _load_plain_siblings(filtered_csv_path: str) -> set[tuple[str, str]]:
    with open(filtered_csv_path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("Filtered CSV must include a header row")
        headers = {field.strip().lower() for field in reader.fieldnames if field}
        if not {"ticker", "cik"}.issubset(headers):
            raise ValueError("Filtered CSV must include Ticker and CIK columns")
        plain_siblings = {
            (
                _normalized_cik(_row_value(row, "cik")),
                _row_value(row, "ticker").upper(),
            )
            for row in reader
            if _normalized_cik(_row_value(row, "cik"))
            and _row_value(row, "ticker")
            and not is_preferred_ticker(_row_value(row, "ticker"))
        }
    return plain_siblings
