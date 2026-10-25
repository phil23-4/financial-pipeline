import asyncio
import json
import os
import time
import urllib.error
import zipfile
from typing import Any

import click

from fin_pipeline.config.logger import pipeline_logger as log
from fin_pipeline.export import export_filings_to_parquet
from fin_pipeline.pipeline import (
    process_entire_directory,
    process_mixed_directory,
    process_sec_edgar_csv,
    process_sec_edgar_csv_stream,
    process_sec_edgar_html_directory,
    run_ingestion_pipeline,
)
from fin_pipeline.utils.xbrl_bulk import (
    download_companyfacts,
    download_fsds_range,
    parquet_columns,
    parse_companyfacts_zip,
    parse_fsds_zip,
    read_cik_csv,
)


@click.group()
def main():
    """FinPipeline: Ingest financial statements and PDFs into SurrealDB."""


@main.command()
@click.argument(
    "dir_path", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
@click.option(
    "--source",
    "-s",
    default="LOCAL",
    type=click.Choice(["LOCAL", "SEC"]),
    help="Source metadata label",
)
@click.option(
    "--concurrency", "-c", default=3, type=int, help="Max simultaneous OCR processes"
)
@click.option(
    "--ocr/--no-ocr", default=True, show_default=True, help="Enable or disable PDF OCR."
)
@click.option("--dry-run", is_flag=True, help="Parse and validate without database writes.")
def scan(dir_path: str, source: str, concurrency: int, ocr: bool, dry_run: bool):
    """Recursively scan a local folder for PDFs and pipe them to SurrealDB."""
    click.echo(f"📂 Scanning target directory: {dir_path}")
    asyncio.run(
        process_entire_directory(
            dir_path,
            source_type=source,
            concurrency_limit=concurrency,
            dry_run=dry_run,
            ocr_enabled=ocr,
        )
    )
    click.echo("🧪 Dry run completed; no database changes were made." if dry_run else "✨ Directory processing task successfully closed out.")


@main.command("scan-mixed")
@click.argument(
    "dir_path", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
@click.option(
    "--source",
    "-s",
    default="LOCAL",
    type=click.Choice(["LOCAL", "SEC"]),
    help="Source metadata label",
)
@click.option(
    "--concurrency", "-c", default=3, type=int, help="Max simultaneous parsing processes"
)
@click.option(
    "--ocr/--no-ocr", default=True, show_default=True, help="Enable or disable PDF OCR."
)
@click.option("--dry-run", is_flag=True, help="Parse and validate without database writes.")
def scan_mixed(
    dir_path: str, source: str, concurrency: int, ocr: bool, dry_run: bool
):
    """Recursively scan a local folder for both PDFs and HTML files."""
    click.echo(f"📂 Scanning mixed document directory: {dir_path}")
    asyncio.run(
        process_mixed_directory(
            dir_path,
            source_type=source,
            concurrency_limit=concurrency,
            dry_run=dry_run,
            ocr_enabled=ocr,
        )
    )
    click.echo("🧪 Dry run completed; no database changes were made." if dry_run else "✨ Mixed directory processing task successfully closed out.")


@main.command()
@click.argument(
    "dir_path", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
@click.option(
    "--concurrency",
    "-c",
    default=3,
    type=int,
    help="Max simultaneous HTML processing tasks",
)
@click.option("--dry-run", is_flag=True, help="Parse and validate without database writes.")
def sec_edgar_html(dir_path: str, concurrency: int, dry_run: bool):
    """Process SEC Edgar HTML filings from local directory structure.

    Expected structure: {dir_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html

    Example: fin-pipeline sec-edgar-html /path/to/sec_filings --concurrency 4
    """
    click.echo(f"📂 Scanning SEC Edgar HTML directory: {dir_path}")
    asyncio.run(
        process_sec_edgar_html_directory(
            dir_path, source_type="SEC", concurrency_limit=concurrency, dry_run=dry_run
        )
    )
    click.echo("🧪 Dry run completed; no database changes were made." if dry_run else "✨ SEC Edgar HTML processing task successfully closed out.")


@main.command()
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--download-dir",
    default="sec_downloads",
    show_default=True,
    help="Directory for downloaded primary HTML documents",
)
@click.option("--dry-run", is_flag=True, help="Download, parse, and validate without database writes.")
def sec_edgar_csv(csv_path: str, download_dir: str, dry_run: bool):
    """Fetch SEC filings for ticker/CIK rows in a CSV and ingest them."""
    click.echo(f"📄 Reading SEC company list: {csv_path}")
    completed = asyncio.run(process_sec_edgar_csv(csv_path, download_dir=download_dir, dry_run=dry_run))
    if completed is False:
        raise click.ClickException("SEC CSV processing failed.")
    click.echo("🧪 Dry run completed; no database changes were made." if dry_run else "✨ SEC CSV processing task successfully closed out.")


@main.command()
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--year-range", help="Inclusive filing year range, for example 2018-2025")
@click.option("--forms", help="Comma-separated SEC forms, for example 10-K,10-Q")
@click.option("--dry-run", is_flag=True, help="Fetch, parse, and validate without database writes.")
def sec_edgar_stream(csv_path: str, year_range: str | None, forms: str | None, dry_run: bool):
    """Fetch SEC filings from a CSV and ingest HTML directly from memory."""
    parsed_year_range = None
    if year_range:
        try:
            start_year, end_year = (int(value) for value in year_range.split("-", 1))
            if start_year > end_year:
                raise ValueError
            parsed_year_range = (start_year, end_year)
        except ValueError:
            raise click.BadParameter(
                "must use START-END with START <= END, e.g. 2018-2025",
                param_hint="--year-range",
            )
    parsed_forms = (
        [value.strip().upper() for value in forms.split(",") if value.strip()]
        if forms
        else None
    )
    if forms is not None and not parsed_forms:
        raise click.BadParameter(
            "must contain at least one SEC form", param_hint="--forms"
        )
    click.echo(f"🌐 Streaming SEC company list: {csv_path}")
    completed = asyncio.run(process_sec_edgar_csv_stream(csv_path, parsed_year_range, parsed_forms, dry_run=dry_run))
    if completed is False:
        raise click.ClickException("SEC streaming processing failed.")
    click.echo("🧪 Dry run completed; no database changes were made." if dry_run else "✨ SEC streaming task successfully closed out.")


@main.command()
@click.argument(
    "file_path", type=click.Path(exists=True, file_okay=True, dir_okay=False)
)
@click.option("--filing-id", required=True, help="Unique ID for target document record")
@click.option("--ticker", required=True, help="Company unique trading ticker symbol")
@click.option("--stock-name", default=None, help="Legal company name, when known")
@click.option("--stock-code", default="UNKNOWN", help="Stock identifier exchange code")
@click.option(
    "--exchange", default="UNKNOWN", help="Name of the listing asset exchange platform"
)
@click.option(
    "--type",
    "filing_type",
    required=True,
    help="Filing format label (e.g., 10-K, 20-F)",
)
@click.option("--source", default="SEC", type=click.Choice(["SEC", "LOCAL"]))
@click.option(
    "--ocr/--no-ocr", default=True, show_default=True, help="Enable or disable PDF OCR."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Parse and validate the file without writing to SurrealDB.",
)
def file(
    file_path: str,
    filing_id: str,
    ticker: str,
    stock_name: str | None,
    stock_code: str,
    exchange: str,
    filing_type: str,
    source: str,
    ocr: bool,
    dry_run: bool,
):
    """Ingest a single explicit file (PDF or HTML) with provided structured metadata."""
    metadata = {
        "filingId": filing_id,
        "companyTicker": ticker,
        "stockName": stock_name,
        "stockCode": stock_code,
        "exchange": exchange,
        "filingType": filing_type,
    }
    click.echo(f"📥 Processing individual file: {os.path.basename(file_path)}")
    completed = asyncio.run(
        run_ingestion_pipeline(
            metadata, file_path, source=source, dry_run=dry_run, ocr_enabled=ocr
        )
    )
    if not completed:
        raise click.ClickException("Document processing failed.")
    if dry_run:
        click.echo("🧪 Dry run completed; no database changes were made.")
    else:
        click.echo("✨ Document registration sequence closed out.")


@main.command()
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, dir_okay=True),
    help="Directory for filings.parquet and chunks.parquet",
)
@click.option(
    "--source-type",
    type=click.Choice(
        ["structured_filing", "glossy_report", "unknown"], case_sensitive=False
    ),
    help="Filter by source classification",
)
@click.option("--company-ticker", help="Filter by exact company ticker")
@click.option("--filing-date-from", help="Inclusive filing date start (YYYY-MM-DD)")
@click.option("--filing-date-to", help="Inclusive filing date end (YYYY-MM-DD)")
def export(
    output_dir: str,
    source_type: str | None,
    company_ticker: str | None,
    filing_date_from: str | None,
    filing_date_to: str | None,
):
    """Export filing records and document chunks to Parquet."""
    filters = {}
    if source_type:
        filters["sourceType"] = source_type
    if company_ticker:
        filters["companyTicker"] = company_ticker
    if filing_date_from or filing_date_to:
        filters["filingDate"] = {
            "start": filing_date_from,
            "end": filing_date_to,
        }
    try:
        filings_path = export_filings_to_parquet(output_dir, filters or None)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Exported filing and chunk Parquet files to {os.path.dirname(filings_path)}"
    )


@main.command("xbrl-bulk")
@click.option(
    "--download-dir",
    default="xbrl_bulk",
    show_default=True,
    type=click.Path(file_okay=False, dir_okay=True),
    help="Directory for companyfacts.zip and FSDS ZIPs",
)
@click.option("--start-year", default=2018, show_default=True, type=int)
@click.option("--end-year", default=2025, show_default=True, type=int)
@click.option(
    "--skip-companyfacts",
    is_flag=True,
    help="Skip companyfacts.zip download/parse (use FSDS only)",
)
@click.option(
    "--cik-csv",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="CSV with a 'cik' column. If omitted, parse all CIKs in the bulk data.",
)
@click.option("--force", is_flag=True, help="Re-download archives, ignoring checkpoints.")
def xbrl_bulk(
    download_dir: str,
    start_year: int,
    end_year: int,
    skip_companyfacts: bool,
    cik_csv: str | None,
    force: bool,
):
    """Download SEC bulk XBRL data and write normalized facts to Parquet."""
    started = time.monotonic()
    try:
        cik_list = read_cik_csv(cik_csv) if cik_csv else None
        log.info(
            f"XBRL bulk: processing {len(cik_list) if cik_list is not None else 'all'} "
            "CIKs, USD-only (non-USD filers will produce no facts)"
        )
        os.makedirs(download_dir, exist_ok=True)
        companyfacts_path = None
        if not skip_companyfacts:
            companyfacts_path = download_companyfacts(download_dir, force=force)
        downloaded_fsds = download_fsds_range(
            start_year, end_year, download_dir, force=force
        )
        fsds_paths = [path for path in downloaded_fsds if path is not None]
        unavailable_quarters = len(downloaded_fsds) - len(fsds_paths)
        log.info(
            f"Downloaded {len(fsds_paths)}/{len(downloaded_fsds)} quarters "
            f"({unavailable_quarters} unavailable)."
        )

        rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        if companyfacts_path:
            companyfacts = parse_companyfacts_zip(companyfacts_path, cik_list)
            for accession, filing in companyfacts.items():
                period = filing.get("period", "")
                period_year = int(period[:4]) if period[:4].isdigit() else None
                for concept, fact in filing.get("facts", {}).items():
                    rows_by_key[(accession, concept)] = {
                        "accession_no": accession,
                        "cik": filing["cik"],
                        "concept": concept,
                        "value": fact["value"],
                        "unit": fact["unit"],
                        "period": fact["period"],
                        "tag": fact["tag"],
                        "kind": fact["kind"],
                        "fiscal_year": period_year,
                        "fiscal_period": "FY",
                        "form": filing.get("form"),
                        "filed_date": filing.get("filed"),
                    }

        for fsds_path in fsds_paths:
            filings = parse_fsds_zip(fsds_path, cik_list)
            for accession, filing in filings.items():
                period = filing.get("period", "")
                period_year = int(period[:4]) if period[:4].isdigit() else None
                period_month = int(period[5:7]) if len(period) >= 7 and period[5:7].isdigit() else None
                period_quarter = (
                    f"Q{(period_month - 1) // 3 + 1}" if period_month else None
                )
                fiscal_period = (
                    "FY"
                    if filing.get("form") in {"10-K", "20-F", "40-F", "10-KT"}
                    else period_quarter
                )
                for concept, value in filing.get("facts", {}).items():
                    rows_by_key[(accession, concept)] = {
                        "accession_no": accession,
                        "cik": filing["cik"],
                        "concept": concept,
                        "value": value["value"],
                        "unit": value["unit"],
                        "period": value["period"],
                        "tag": value["tag"],
                        "kind": value["kind"],
                        "fiscal_year": period_year,
                        "fiscal_period": fiscal_period,
                        "form": filing.get("form"),
                        "filed_date": filing.get("filed"),
                    }

        from fin_pipeline.utils.xbrl_bulk import _apply_invariants

        accession_facts: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows_by_key.values():
            accession_facts.setdefault(row["accession_no"], {})[row["concept"]] = {
                "value": row["value"],
                "tag": row.get("tag"),
                "unit": row.get("unit"),
                "period": row.get("period"),
                "kind": row.get("kind"),
            }
        for accession, facts in accession_facts.items():
            _apply_invariants(facts, accession)
        rows_by_key = {
            key: row
            for key, row in rows_by_key.items()
            if key[1] in accession_facts[key[0]]
        }

        import pandas as pd

        dataframe = pd.DataFrame(
            rows_by_key.values(),
            columns=parquet_columns(),
        )
        output_path = os.path.join(download_dir, "xbrl_facts.parquet")
        dataframe.to_parquet(output_path, index=False)
        file_size_mb = os.path.getsize(output_path) / 1_000_000
    except urllib.error.URLError as exc:
        click.echo(f"❌ Network error during download: {exc}", err=True)
        raise click.Abort() from exc
    except TimeoutError as exc:
        click.echo(f"❌ Network error during download: {exc}", err=True)
        raise click.Abort() from exc
    except KeyboardInterrupt as exc:
        click.echo(
            "\n⚠️  Interrupted. Partial downloads remain in "
            f"{download_dir}/; re-run to resume.",
            err=True,
        )
        raise click.Abort() from exc
    except (
        OSError,
        ValueError,
        RuntimeError,
        ImportError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    accession_count = len({key[0] for key in rows_by_key})
    cik_count = len({row["cik"] for row in rows_by_key.values()})
    fact_count = len(rows_by_key)
    elapsed = time.monotonic() - started
    log.info(
        f"XBRL bulk export complete: {accession_count} filings, "
        f"{fact_count} facts, {cik_count} CIKs"
    )
    click.echo(
        f"\n✅ xbrl-bulk complete:\n"
        f"  Filings processed: {accession_count}\n"
        f"  Total facts:       {fact_count}\n"
        f"  Output Parquet:    {output_path} ({file_size_mb:.1f}MB)\n"
        f"  Runtime:           {elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
