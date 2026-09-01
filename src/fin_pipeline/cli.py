import click
import asyncio
import os
from fin_pipeline.pipeline import process_entire_directory, run_ingestion_pipeline, process_sec_edgar_html_directory

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
