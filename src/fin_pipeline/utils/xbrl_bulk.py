"""Download and parse SEC bulk XBRL datasets."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger as log

COMPANYFACTS_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
)
FSDS_URL_TEMPLATE = (
    "https://www.sec.gov/files/dera/data/financial-statement-data-sets/"
    "{year}q{quarter}.zip"
)
SEC_USER_AGENT = "Phillip francis@phillipchapita.com"

_CONCEPT_TAGS = {
    "revenues": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "capitalExpenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "researchAndDevelopmentExpense": ("ResearchAndDevelopmentExpense",),
    "intangibleAssets": (
        "IntangibleAssetsNetExcludingGoodwill",
        "FiniteLivedIntangibleAssetsNet",
    ),
    "assets": ("Assets",),
}
_TAG_TO_CONCEPT = {
    tag: concept for concept, tags in _CONCEPT_TAGS.items() for tag in tags
}
_PARQUET_COLUMNS = [
    "accession_no",
    "cik",
    "concept",
    "value",
    "unit",
    "fiscal_year",
    "fiscal_period",
    "form",
    "filed_date",
]


def _normalize_cik(cik: Any) -> str:
    value = str(cik).strip()
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError(f"Invalid CIK: {cik!r}")
    return digits.zfill(10)


def _download_with_progress(
    url: str,
    dest_path: str,
    chunk_size: int = 1_048_576,
) -> str:
    """Download a URL with periodic byte progress and a 60-second timeout."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    destination = Path(dest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = Path(f"{dest_path}.tmp")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": SEC_USER_AGENT},
    )
    log.info(f"Downloading {url}")
    log.info(f"  -> {dest_path}")

    with urllib.request.urlopen(request, timeout=60) as response:
        try:
            total = int(response.headers.get("Content-Length", 0))
        except (AttributeError, TypeError, ValueError):
            total = 0
        downloaded = 0
        last_report = 0
        report_interval = min(total / 10, 10_000_000) if total else 10_000_000
        with temporary_path.open("wb") as output:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded - last_report >= report_interval or (
                    total and downloaded == total
                ):
                    progress = f"{downloaded / 1_000_000:.1f}MB"
                    if total:
                        percentage = 100 * downloaded / total
                        progress += f" / {total / 1_000_000:.1f}MB ({percentage:.0f}%)"
                    log.info(f"  {destination.name}: {progress}")
                    last_report = downloaded

    os.replace(temporary_path, destination)
    return str(destination)


def _download(url: str, destination: Path, force: bool) -> str:
    marker = Path(f"{destination}.done")
    temporary_path = Path(f"{destination}.tmp")

    if temporary_path.exists():
        temporary_path.unlink()

    if force:
        marker.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
    elif marker.is_file() and destination.is_file():
        try:
            marker_size = marker.read_text(encoding="ascii").strip()
        except OSError:
            marker_size = ""
        if marker_size == str(destination.stat().st_size) and zipfile.is_zipfile(
            destination
        ):
            log.info(f"Using completed download {destination}")
            return str(destination)

    marker.unlink(missing_ok=True)
    destination.unlink(missing_ok=True)
    return _download_with_progress(url, str(destination))


def _mark_download_done(zip_path: str) -> None:
    path = Path(zip_path)
    Path(f"{zip_path}.done").write_text(
        str(path.stat().st_size),
        encoding="ascii",
    )


def download_companyfacts(dest_dir: str, force: bool = False) -> str:
    """Download companyfacts.zip to ``dest_dir`` unless already present."""
    return _download(
        COMPANYFACTS_URL,
        Path(dest_dir) / "companyfacts.zip",
        force,
    )


def download_fsds_quarter(
    year: int,
    quarter: int,
    dest_dir: str,
    force: bool = False,
) -> str | None:
    """Download one quarterly Financial Statement Data Set archive."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be between 1 and 4")
    if year < 2009:
        raise ValueError("FSDS data is available from 2009 onward")
    url = FSDS_URL_TEMPLATE.format(year=year, quarter=quarter)
    destination = Path(dest_dir) / f"{year}q{quarter}.zip"
    try:
        return _download(url, destination, force)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        log.warning(f"  FSDS {year}q{quarter} not available yet (404); skipping")
        return None


def download_fsds_range(
    start_year: int,
    end_year: int,
    dest_dir: str,
    force: bool = False,
) -> list[str | None]:
    """Download all FSDS quarters from ``start_year`` Q1 through ``end_year`` Q4."""
    if start_year > end_year:
        raise ValueError("start_year must be less than or equal to end_year")
    if start_year < 2009:
        raise ValueError("FSDS data is available from 2009 onward")
    return [
        download_fsds_quarter(year, quarter, dest_dir, force=force)
        for year in range(start_year, end_year + 1)
        for quarter in range(1, 5)
    ]


def _allowed_ciks(cik_list: set[str] | None) -> set[str] | None:
    return {_normalize_cik(cik) for cik in cik_list} if cik_list is not None else None


def parse_companyfacts_zip(
    zip_path: str,
    cik_list: set[str] | None,
) -> dict[str, dict[str, Any]]:
    """Read selected filing-level facts from companyfacts.zip."""
    allowed = _allowed_ciks(cik_list)
    result: dict[str, dict[str, Any]] = {}
    requested = (
        f"{len(cik_list)} CIKs requested" if cik_list is not None else "all CIKs"
    )
    log.info(f"Parsing {Path(zip_path).name} ({requested})...")
    started = time.monotonic()
    last_report = started
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        total = len(members)
        for index, member in enumerate(members):
            now = time.monotonic()
            if index == 0 or index % 200 == 0 or now - last_report >= 30:
                log.info(f"  parsed {index}/{total} CIK files")
                last_report = now
            if not member.filename.lower().endswith(".json"):
                continue
            basename = Path(member.filename).name
            cik_match = re.search(r"(?:CIK)?(\d+)\.json$", basename, re.IGNORECASE)
            if cik_match is None:
                continue
            filename_cik = cik_match.group(1).zfill(10)
            if allowed is not None and filename_cik not in allowed:
                continue

            with archive.open(member) as source:
                company_data = json.load(source)
            cik = _normalize_cik(company_data.get("cik", filename_cik))
            if allowed is not None and cik not in allowed:
                continue

            canonical_facts: dict[str, list[dict[str, Any]]] = {}
            taxonomies = company_data.get("facts", {})
            if not isinstance(taxonomies, dict):
                continue
            for taxonomy_facts in taxonomies.values():
                if not isinstance(taxonomy_facts, dict):
                    continue
                for tag, concept_data in taxonomy_facts.items():
                    canonical = _TAG_TO_CONCEPT.get(tag)
                    if canonical is None or not isinstance(concept_data, dict):
                        continue
                    units = concept_data.get("units", {})
                    if not isinstance(units, dict):
                        continue
                    for unit, observations in units.items():
                        if not isinstance(observations, list):
                            continue
                        for observation in observations:
                            if not isinstance(observation, dict) or not observation.get(
                                "accn"
                            ):
                                continue
                            fact = {
                                key: observation[key]
                                for key in (
                                    "val",
                                    "fy",
                                    "fp",
                                    "form",
                                    "accn",
                                    "end",
                                    "filed",
                                )
                                if key in observation
                            }
                            fact["unit"] = unit
                            canonical_facts.setdefault(canonical, []).append(fact)
            result[cik] = {"facts": canonical_facts}
    log.info(f"  parsed {total}/{total} CIK files")
    _mark_download_done(zip_path)
    return result


def _zip_text_file(archive: zipfile.ZipFile, basename: str) -> zipfile.ZipExtFile:
    for member in archive.namelist():
        if Path(member).name.lower() == basename.lower():
            return archive.open(member)
    raise ValueError(f"FSDS archive is missing {basename}")


def _iter_tsv_rows(archive: zipfile.ZipFile, basename: str) -> Iterator[dict[str, str]]:
    with _zip_text_file(archive, basename) as source:
        import io

        text_source = io.TextIOWrapper(source, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text_source, delimiter="\t")


def _date_from_fsds(value: str) -> str:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _numeric_value(value: str) -> int | float | None:
    value = value.strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def parse_fsds_zip(
    zip_path: str,
    cik_list: set[str] | None,
) -> dict[str, dict[str, Any]]:
    """Parse submission metadata and selected facts from one FSDS archive."""
    allowed = _allowed_ciks(cik_list)
    filings: dict[str, dict[str, Any]] = {}
    log.info(f"Parsing {Path(zip_path).name}...")
    started = time.monotonic()
    with zipfile.ZipFile(zip_path) as archive:
        submission_rows = 0
        for row in _iter_tsv_rows(archive, "sub.txt"):
            submission_rows += 1
            if submission_rows % 200_000 == 0 or time.monotonic() - started >= 30:
                log.info(f"  parsed {submission_rows} submission rows")
                started = time.monotonic()
            accession = row.get("adsh", "").strip()
            cik = _normalize_cik(row.get("cik", ""))
            if not accession or (allowed is not None and cik not in allowed):
                continue
            period = _date_from_fsds(row.get("period", ""))
            filings[accession] = {
                "cik": cik,
                "form": row.get("form", "").strip(),
                "period": period,
                "filed": _date_from_fsds(row.get("filed", "")),
                "facts": {},
                "units": {},
                "_fact_ranks": {},
            }

        numeric_rows = 0
        for row in _iter_tsv_rows(archive, "num.txt"):
            numeric_rows += 1
            if numeric_rows % 200_000 == 0 or time.monotonic() - started >= 30:
                log.info(f"  parsed {numeric_rows} numeric fact rows")
                started = time.monotonic()
            accession = row.get("adsh", "").strip()
            filing = filings.get(accession)
            canonical = _TAG_TO_CONCEPT.get(row.get("tag", "").strip())
            if filing is None or canonical is None:
                continue
            ddate = _date_from_fsds(row.get("ddate", ""))
            if ddate != filing["period"]:
                continue
            try:
                quarters = int(row.get("qtrs", ""))
            except ValueError:
                continue
            if quarters == 4:
                rank = 3
            elif quarters == 0:
                rank = 2
            elif quarters == 1:
                rank = 1
            else:
                continue
            value = _numeric_value(row.get("value", ""))
            if value is None:
                continue
            previous_rank = filing["_fact_ranks"].get(canonical, 0)
            if rank > previous_rank:
                filing["facts"][canonical] = value
                filing["units"][canonical] = row.get("uom", "").strip() or None
                filing["_fact_ranks"][canonical] = rank

    for filing in filings.values():
        filing.pop("_fact_ranks", None)
    log.info(f"  {submission_rows} filings, {numeric_rows} facts")
    _mark_download_done(zip_path)
    return filings


def read_cik_csv(cik_csv: str) -> set[str]:
    """Read and normalize CIKs from a CSV with a ``cik`` column."""
    with open(cik_csv, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "cik" not in {
            name.strip().lower() for name in reader.fieldnames if name
        }:
            raise ValueError("CIK CSV must include a 'cik' column")
        cik_column = next(
            name for name in reader.fieldnames if name.strip().lower() == "cik"
        )
        return {
            _normalize_cik(row.get(cik_column) or "")
            for row in reader
            if (row.get(cik_column) or "").strip()
        }


def parquet_columns() -> list[str]:
    """Return the stable column order for the bulk fact Parquet output."""
    return list(_PARQUET_COLUMNS)
