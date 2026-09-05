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
            ],
        )

    assert result.exit_code == 0
    metadata = run_pipeline.call_args.args[0]
    assert metadata["stockName"] == "Banca Transilvania S.A."
    assert metadata["companyTicker"] == "TLV"
    assert run_pipeline.call_args.kwargs["source"] == "LOCAL"


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
