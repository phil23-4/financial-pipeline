import click
import asyncio
import os
from fin_pipeline.pipeline import process_entire_directory, run_ingestion_pipeline, process_sec_edgar_html_directory, process_sec_edgar_csv, process_sec_edgar_csv_stream

@click.group()
def main():
    """FinPipeline: Ingest financial statements and PDFs into SurrealDB."""
    pass

@main.command()
@click.argument('dir_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option('--source', '-s', default='LOCAL', type=click.Choice(['LOCAL', 'SEC']), help='Source metadata label')
@click.option('--concurrency', '-c', default=3, type=int, help='Max simultaneous OCR processes')
def scan(dir_path: str, source: str, concurrency: int):
    """Recursively scan a local folder for PDFs and pipe them to SurrealDB."""
    click.echo(f"📂 Scanning target directory: {dir_path}")
    asyncio.run(process_entire_directory(dir_path, source_type=source, concurrency_limit=concurrency))
    click.echo("✨ Directory processing task successfully closed out.")

@main.command()
@click.argument('dir_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option('--concurrency', '-c', default=3, type=int, help='Max simultaneous HTML processing tasks')
def sec_edgar_html(dir_path: str, concurrency: int):
    """Process SEC Edgar HTML filings from local directory structure.
    
    Expected structure: {dir_path}/sec-edgar-filings/{TICKER}/{FILING_TYPE}/{ACCESSION_NUMBER}/primary-document.html
    
    Example: fin-pipeline sec-edgar-html /path/to/sec_filings --concurrency 4
    """
    click.echo(f"📂 Scanning SEC Edgar HTML directory: {dir_path}")
    asyncio.run(process_sec_edgar_html_directory(dir_path, source_type="SEC", concurrency_limit=concurrency))
    click.echo("✨ SEC Edgar HTML processing task successfully closed out.")

@main.command()
@click.argument('csv_path', type=click.Path(exists=True, dir_okay=False))
@click.option('--download-dir', default='sec_downloads', show_default=True, help='Directory for downloaded primary HTML documents')
def sec_edgar_csv(csv_path: str, download_dir: str):
    """Fetch SEC filings for ticker/CIK rows in a CSV and ingest them."""
    click.echo(f"📄 Reading SEC company list: {csv_path}")
    asyncio.run(process_sec_edgar_csv(csv_path, download_dir=download_dir))
    click.echo("✨ SEC CSV processing task successfully closed out.")

@main.command()
@click.argument('csv_path', type=click.Path(exists=True, dir_okay=False))
@click.option('--year-range', help='Inclusive filing year range, for example 2018-2025')
@click.option('--forms', help='Comma-separated SEC forms, for example 10-K,10-Q')
def sec_edgar_stream(csv_path: str, year_range: str | None, forms: str | None):
    """Fetch SEC filings from a CSV and ingest HTML directly from memory."""
    parsed_year_range = None
    if year_range:
        try:
            start_year, end_year = (int(value) for value in year_range.split('-', 1))
            if start_year > end_year:
                raise ValueError
            parsed_year_range = (start_year, end_year)
        except ValueError:
            raise click.BadParameter('must use START-END with START <= END, e.g. 2018-2025', param_hint='--year-range')
    parsed_forms = [value.strip().upper() for value in forms.split(',') if value.strip()] if forms else None
    if forms is not None and not parsed_forms:
        raise click.BadParameter('must contain at least one SEC form', param_hint='--forms')
    click.echo(f"🌐 Streaming SEC company list: {csv_path}")
    asyncio.run(process_sec_edgar_csv_stream(csv_path, parsed_year_range, parsed_forms))
    click.echo("✨ SEC streaming task successfully closed out.")

@main.command()
@click.argument('file_path', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option('--filing-id', required=True, help='Unique ID for target document record')
@click.option('--ticker', required=True, help='Company unique trading ticker symbol')
@click.option('--stock-code', default='UNKNOWN', help='Stock identifier exchange code')
@click.option('--exchange', default='UNKNOWN', help='Name of the listing asset exchange platform')
@click.option('--type', 'filing_type', required=True, help='Filing format label (e.g., 10-K, 20-F)')
@click.option('--source', default='SEC', type=click.Choice(['SEC', 'LOCAL']))
def file(file_path: str, filing_id: str, ticker: str, stock_code: str, exchange: str, filing_type: str, source: str):
    """Ingest a single explicit file (PDF or HTML) with provided structured metadata."""
    metadata = {
        "filingId": filing_id,
        "companyTicker": ticker,
        "stockCode": stock_code,
        "exchange": exchange,
        "filingType": filing_type
    }
    click.echo(f"📥 Processing individual file: {os.path.basename(file_path)}")
    asyncio.run(run_ingestion_pipeline(metadata, file_path, source=source))
    click.echo("✨ Document registration sequence closed out.")

if __name__ == '__main__':
    main()
