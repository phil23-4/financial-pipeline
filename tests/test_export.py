import json

import pytest
from click.testing import CliRunner

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from fin_pipeline.cli import main
from fin_pipeline.export import export_filings_to_parquet


def test_export_writes_filing_and_chunk_parquet(tmp_path, monkeypatch):
    response = [
        {
            "status": "OK",
            "result": [
                {
                    "filingId": "sec_ACME_123",
                    "companyTicker": "ACME",
                    "sourceType": "glossy_report",
                    "documentChunks": json.dumps(
                        [
                            {
                                "chunkIndex": 0,
                                "pageNumber": 1,
                                "sectionLabel": "strategy",
                                "text": "Growth plan",
                            }
                        ]
                    ),
                }
            ],
        }
    ]
    queries = []
    monkeypatch.setattr(
        "fin_pipeline.export.surreal_query",
        lambda query, timeout: queries.append(query) or response,
    )

    filings_path = export_filings_to_parquet(
        str(tmp_path),
        {
            "sourceType": "glossy_report",
            "companyTicker": "ACME",
            "filingDate": {"start": "2024-01-01", "end": "2024-12-31"},
        },
    )
    chunks_path = tmp_path / "chunks.parquet"

    assert queries[0].startswith("SELECT * FROM exchange_filing WHERE ")
    assert 'sourceType = "glossy_report"' in queries[0]
    assert 'companyTicker = "ACME"' in queries[0]
    assert "filingDate >= d'2024-01-01'" in queries[0]
    assert "filingDate <= d'2024-12-31'" in queries[0]
    assert pd.read_parquet(filings_path).iloc[0]["filingId"] == "sec_ACME_123"
    chunk = pd.read_parquet(chunks_path).iloc[0]
    assert chunk["filingId"] == "sec_ACME_123"
    assert chunk["chunkIndex"] == 0
    assert chunk["sectionLabel"] == "strategy"
    assert chunk["text"] == "Growth plan"


def test_export_cli_passes_filter_options(tmp_path, monkeypatch):
    called = {}

    def fake_export(output_dir, filters):
        called["output_dir"] = output_dir
        called["filters"] = filters
        return str(tmp_path / "filings.parquet")

    monkeypatch.setattr("fin_pipeline.cli.export_filings_to_parquet", fake_export)

    result = CliRunner().invoke(
        main,
        [
            "export",
            "--output-dir",
            str(tmp_path / "out"),
            "--source-type",
            "glossy_report",
        ],
    )

    assert result.exit_code == 0
    assert called["filters"] == {"sourceType": "glossy_report"}
    assert "Exported filing and chunk Parquet files" in result.output
