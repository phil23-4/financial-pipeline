import sys
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from fin_pipeline.cli import main


def test_file_command_passes_stock_name_to_ingestion(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4")

    with patch("fin_pipeline.cli.run_ingestion_pipeline") as run_pipeline:
        result = CliRunner().invoke(
            main,
            [
                "file",
                str(document),
                "--filing-id",
                "local_report",
                "--ticker",
                "TLV",
                "--stock-name",
                "Banca Transilvania S.A.",
                "--stock-code",
                "131662",
                "--exchange",
                "BVB",
                "--type",
                "ANNUAL_REPORT",
                "--source",
                "LOCAL",
                "--no-ocr",
            ],
        )

    assert result.exit_code == 0
    metadata = run_pipeline.call_args.args[0]
    assert metadata["stockName"] == "Banca Transilvania S.A."
    assert metadata["companyTicker"] == "TLV"
    assert run_pipeline.call_args.kwargs["source"] == "LOCAL"
    assert run_pipeline.call_args.kwargs["ocr_enabled"] is False


def test_file_command_requires_filing_type(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4")

    result = CliRunner().invoke(
        main,
        [
            "file",
            str(document),
            "--filing-id",
            "local_report",
            "--ticker",
            "TLV",
        ],
    )

    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_file_command_supports_dry_run(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4")

    with patch(
        "fin_pipeline.cli.run_ingestion_pipeline", return_value=True
    ) as run_pipeline:
        result = CliRunner().invoke(
            main,
            [
                "file",
                str(document),
                "--filing-id",
                "local_report",
                "--ticker",
                "TLV",
                "--type",
                "ANNUAL_REPORT",
                "--dry-run",
            ],
        )

    assert result.exit_code == 0
    assert "Dry run completed" in result.output
    assert run_pipeline.call_args.kwargs["dry_run"] is True
    assert run_pipeline.call_args.kwargs["ocr_enabled"] is True


def test_file_command_reports_success_for_standard_run(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4")

    with patch("fin_pipeline.cli.run_ingestion_pipeline", return_value=True):
        result = CliRunner().invoke(
            main,
            [
                "file",
                str(document),
                "--filing-id",
                "local_report",
                "--ticker",
                "TLV",
                "--type",
                "ANNUAL_REPORT",
            ],
        )

    assert result.exit_code == 0
    assert "Document registration sequence closed out." in result.output


def test_file_command_reports_pipeline_failure(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4")

    with patch("fin_pipeline.cli.run_ingestion_pipeline", return_value=False):
        result = CliRunner().invoke(
            main,
            [
                "file",
                str(document),
                "--filing-id",
                "local_report",
                "--ticker",
                "TLV",
                "--type",
                "ANNUAL_REPORT",
            ],
        )

    assert result.exit_code != 0
    assert "Document processing failed" in result.output


def test_scan_command_passes_dry_run(tmp_path):
    with patch("fin_pipeline.cli.process_entire_directory") as process:
        result = CliRunner().invoke(
            main, ["scan", str(tmp_path), "--dry-run", "--no-ocr"]
        )

    assert result.exit_code == 0
    assert process.call_args.kwargs["dry_run"] is True
    assert process.call_args.kwargs["ocr_enabled"] is False
    assert "Dry run completed" in result.output


def test_scan_mixed_command_passes_dry_run(tmp_path):
    with patch("fin_pipeline.cli.process_mixed_directory") as process:
        result = CliRunner().invoke(
            main, ["scan-mixed", str(tmp_path), "--dry-run", "--no-ocr"]
        )

    assert result.exit_code == 0
    assert process.call_args.kwargs["dry_run"] is True
    assert process.call_args.kwargs["ocr_enabled"] is False
    assert "Dry run completed" in result.output


def test_sec_html_command_passes_dry_run(tmp_path):
    with patch("fin_pipeline.cli.process_sec_edgar_html_directory") as process:
        result = CliRunner().invoke(
            main, ["sec-edgar-html", str(tmp_path), "--dry-run"]
        )

    assert result.exit_code == 0
    assert process.call_args.kwargs["dry_run"] is True


def test_sec_csv_command_passes_dry_run(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,cik\nTLV,123\n")
    with patch("fin_pipeline.cli.process_sec_edgar_csv", return_value=True) as process:
        result = CliRunner().invoke(
            main, ["sec-edgar-csv", str(csv_path), "--dry-run"]
        )

    assert result.exit_code == 0
    assert process.call_args.kwargs["dry_run"] is True


def test_sec_stream_command_passes_dry_run(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker,cik\nTLV,123\n")
    with patch(
        "fin_pipeline.cli.process_sec_edgar_csv_stream", return_value=True
    ) as process:
        result = CliRunner().invoke(
            main, ["sec-edgar-stream", str(csv_path), "--dry-run"]
        )

    assert result.exit_code == 0
    assert process.call_args.kwargs["dry_run"] is True


def test_xbrl_bulk_reports_unavailable_quarters_and_summary(tmp_path, monkeypatch):
    class FakeDataFrame:
        def to_parquet(self, path, index=False):
            with open(path, "wb") as output:
                output.write(b"parquet")

    monkeypatch.setitem(
        sys.modules,
        "pandas",
        SimpleNamespace(
            DataFrame=lambda _rows, columns: FakeDataFrame(),
        ),
    )
    monkeypatch.setattr(
        "fin_pipeline.cli.download_fsds_range",
        lambda *_args, **_kwargs: ["2026q1.zip", None],
    )
    monkeypatch.setattr(
        "fin_pipeline.cli.parse_fsds_zip", lambda *_args: {}
    )

    with patch("fin_pipeline.cli.log.info") as log_info:
        result = CliRunner().invoke(
            main,
            [
                "xbrl-bulk",
                "--download-dir",
                str(tmp_path),
                "--start-year",
                "2026",
                "--end-year",
                "2026",
                "--skip-companyfacts",
            ],
        )

    assert result.exit_code == 0
    assert "✅ xbrl-bulk complete:" in result.output
    assert "Filings processed: 0" in result.output
    assert "Output Parquet:" in result.output
    assert any("1/2 quarters (1 unavailable)" in call.args[0] for call in log_info.call_args_list)
