import json
import zipfile
from io import BytesIO
from unittest.mock import patch

import pytest

from fin_pipeline import pipeline
from fin_pipeline.utils import xbrl_bulk
from fin_pipeline.utils.xbrl_bulk import parse_companyfacts_zip, parse_fsds_zip


def _write_companyfacts_zip(path, companies):
    with zipfile.ZipFile(path, "w") as archive:
        for cik, company in companies.items():
            archive.writestr(
                f"CIK{cik}.json",
                json.dumps({"cik": int(cik), **company}),
            )


def _companyfacts_company(concepts):
    return {
        "facts": {
            "us-gaap": {
                tag: {
                    "label": tag,
                    "units": {
                        "USD": [
                            {
                                "end": "2023-12-31",
                                "val": value,
                                "accn": "0000000001-24-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-01",
                            }
                        ]
                    },
                }
                for tag, value in concepts.items()
            }
        }
    }


def test_parse_companyfacts_filters_concepts(tmp_path):
    zip_path = tmp_path / "companyfacts.zip"
    _write_companyfacts_zip(
        zip_path,
        {
            "0000000001": _companyfacts_company(
                {"Revenues": 123, "Assets": 456, "SomeOtherConcept": 789}
            )
        },
    )

    parsed = parse_companyfacts_zip(str(zip_path), {"1"})

    assert parsed["0000000001"]["facts"] == {
        "revenues": [
            {
                "end": "2023-12-31",
                "val": 123,
                "accn": "0000000001-24-000001",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-02-01",
                "unit": "USD",
            }
        ],
        "assets": [
            {
                "end": "2023-12-31",
                "val": 456,
                "accn": "0000000001-24-000001",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-02-01",
                "unit": "USD",
            }
        ],
    }


def test_parse_companyfacts_filters_ciks(tmp_path):
    zip_path = tmp_path / "companyfacts.zip"
    _write_companyfacts_zip(
        zip_path,
        {
            "0000000001": _companyfacts_company({"Assets": 1}),
            "0000000002": _companyfacts_company({"Assets": 2}),
        },
    )

    parsed = parse_companyfacts_zip(str(zip_path), {"2"})

    assert list(parsed) == ["0000000002"]
    assert parsed["0000000002"]["facts"]["assets"][0]["val"] == 2


def _write_fsds_zip(path, submissions, numeric_facts=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "sub.txt",
            "adsh\tcik\tname\tform\tperiod\tfiled\n"
            + "".join(
                "\t".join(str(value) for value in row) + "\n"
                for row in submissions
            ),
        )
        archive.writestr(
            "num.txt",
            "adsh\ttag\tvalue\tuom\tddate\tqtrs\n"
            + "".join(
                "\t".join(str(value) for value in row) + "\n"
                for row in numeric_facts
            ),
        )


def test_parse_fsds_sub_filters_form(tmp_path):
    zip_path = tmp_path / "2024q1.zip"
    _write_fsds_zip(
        zip_path,
        [
            ("0000000001-24-000001", "1", "Example Inc", "10-K", "20231231", "20240201"),
            ("0000000001-24-000002", "1", "Example Inc", "10-Q", "20240331", "20240501"),
        ],
    )

    parsed = parse_fsds_zip(str(zip_path), {"1"})

    assert parsed["0000000001-24-000001"]["form"] == "10-K"
    assert parsed["0000000001-24-000002"]["form"] == "10-Q"


def test_parse_fsds_num_maps_canonical(tmp_path):
    zip_path = tmp_path / "2024q1.zip"
    accession = "0000000001-24-000001"
    _write_fsds_zip(
        zip_path,
        [(accession, "1", "Example Inc", "10-K", "20231231", "20240201")],
        [(accession, "Revenues", "123456", "USD", "20231231", "4")],
    )

    parsed = parse_fsds_zip(str(zip_path), {"1"})

    assert parsed[accession]["facts"]["revenues"] == 123456
    assert parsed[accession]["units"]["revenues"] == "USD"


def test_parse_fsds_prefers_annual_fact_and_keeps_quarterly_fallback(tmp_path):
    zip_path = tmp_path / "2024q1.zip"
    accession = "0000000001-24-000001"
    _write_fsds_zip(
        zip_path,
        [(accession, "1", "Example Inc", "10-K", "20231231", "20240201")],
        [
            (accession, "Revenues", "10", "USD", "20231231", "1"),
            (accession, "Revenues", "40", "USD", "20231231", "4"),
            (
                accession,
                "ResearchAndDevelopmentExpense",
                "5",
                "USD",
                "20231231",
                "1",
            ),
            (accession, "Assets", "500", "USD", "20231231", "0"),
        ],
    )

    parsed = parse_fsds_zip(str(zip_path), {"1"})

    assert parsed[accession]["facts"] == {
        "revenues": 40,
        "researchAndDevelopmentExpense": 5,
        "assets": 500,
    }


def test_download_companyfacts_sends_user_agent(tmp_path, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        response = BytesIO(b"archive")
        response.headers = {"Content-Length": "7"}
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)

    downloaded_path = xbrl_bulk.download_companyfacts(str(tmp_path))

    assert captured["request"].get_header("User-agent") == xbrl_bulk.SEC_USER_AGENT
    assert captured["timeout"] == 60
    assert tmp_path.joinpath("companyfacts.zip").read_bytes() == b"archive"
    assert downloaded_path == str(tmp_path / "companyfacts.zip")


def test_download_prints_progress(tmp_path, monkeypatch):
    class FiveMegabyteChunks(BytesIO):
        def read(self, _size=-1):
            return super().read(5_000_000)

    def fake_urlopen(_request, timeout):
        assert timeout == 60
        response = FiveMegabyteChunks(b"x" * (15 * 1_000_000))
        response.headers = {"Content-Length": str(15 * 1_000_000)}
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)

    with patch("fin_pipeline.utils.xbrl_bulk.log.info") as log_info:
        destination = tmp_path / "progress.zip"
        xbrl_bulk._download_with_progress(
            "https://example.invalid/archive.zip", str(destination)
        )

    messages = [str(call.args[0]) for call in log_info.call_args_list]
    progress = [message for message in messages if "%" in message]
    assert progress
    assert any("(" in message and "%)" in message for message in progress)
    assert any("100%" in message for message in progress)
    assert destination.stat().st_size == 15 * 1_000_000


def test_download_fsds_handles_404(tmp_path, monkeypatch):
    def raise_404(*_args, **_kwargs):
        raise xbrl_bulk.urllib.error.HTTPError(
            "https://example.invalid/2026q4.zip", 404, "Not Found", {}, None
        )

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", raise_404)

    with patch("fin_pipeline.utils.xbrl_bulk.log.warning") as log_warning:
        result = xbrl_bulk.download_fsds_quarter(2026, 4, str(tmp_path))

    assert result is None
    log_warning.assert_called_once()
    assert "2026q4 not available yet (404)" in log_warning.call_args.args[0]


def test_download_has_timeout(tmp_path, monkeypatch):
    captured = {}

    def fake_urlopen(_request, timeout):
        captured["timeout"] = timeout
        response = BytesIO(b"archive")
        response.headers = {"Content-Length": "7"}
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)

    xbrl_bulk.download_companyfacts(str(tmp_path))

    assert captured["timeout"] == 60


def test_resumable_download_skips_done_file(tmp_path, monkeypatch):
    archive_path = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "CIK0000000001.json",
            json.dumps(
                {
                    "cik": 1,
                    "facts": {"us-gaap": {"Assets": {"units": {"USD": []}}}},
                }
            ),
        )

    xbrl_bulk.parse_companyfacts_zip(str(archive_path), {"1"})
    monkeypatch.setattr(
        xbrl_bulk.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("completed download should be reused"),
    )

    first_result = xbrl_bulk.download_companyfacts(str(tmp_path))
    second_result = xbrl_bulk.download_companyfacts(str(tmp_path))

    assert first_result == second_result == str(archive_path)
    assert (tmp_path / "companyfacts.zip.done").read_text() == str(
        archive_path.stat().st_size
    )


def test_bulk_cache_loads_from_parquet(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    parquet_path = tmp_path / "xbrl_facts.parquet"
    pd.DataFrame(
        [
            {
                "accession_no": "acc-1",
                "concept": "revenues",
                "value": 100,
            },
            {
                "accession_no": "acc-2",
                "concept": "assets",
                "value": 200,
            },
        ]
    ).to_parquet(parquet_path, index=False)
    monkeypatch.setattr(pipeline, "_xbrl_bulk_cache", None)

    cache = pipeline._load_xbrl_bulk_cache(str(parquet_path))

    assert cache == {("acc-1", "revenues"): 100, ("acc-2", "assets"): 200}


@pytest.mark.asyncio
async def test_pipeline_prefers_bulk_cache_over_filing_extraction(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "_load_xbrl_bulk_cache",
        lambda: {("0000000001-24-000001", "revenues"): 123},
    )
    monkeypatch.setattr(
        pipeline,
        "extract_xbrl_facts",
        lambda _html: pytest.fail("per-filing extraction should not run"),
    )
    monkeypatch.setattr(
        pipeline,
        "enrich_filing_metadata_with_edgartools",
        lambda parsed, _accession: parsed,
    )

    with patch("fin_pipeline.pipeline.ExchangeFilingModel") as model:
        model.return_value.model_dump.return_value = {
            "filingId": "sec_AAPL_0000000001-24-000001",
            "documentStatus": "PROCESSED",
        }
        completed = await pipeline.run_html_content_pipeline(
            {
                "filingId": "sec_AAPL_0000000001-24-000001",
                "companyTicker": "AAPL",
                "stockCode": "UNKNOWN",
                "exchange": "UNKNOWN",
                "filingType": "10-K",
            },
            "<html><body>Filing</body></html>",
            dry_run=True,
        )

    assert completed is True
    facts = json.loads(model.call_args.kwargs["xbrlFacts"])
    assert facts == {"revenues": 123}


@pytest.mark.asyncio
async def test_file_pipeline_prefers_bulk_cache_over_filing_extraction(
    tmp_path, monkeypatch
):
    html_path = tmp_path / "filing.html"
    html_path.write_text("<html><body>Filing</body></html>")
    monkeypatch.setattr(
        pipeline,
        "_load_xbrl_bulk_cache",
        lambda: {("0000000001-24-000001", "assets"): 456},
    )
    monkeypatch.setattr(
        pipeline,
        "parse_document",
        lambda *_args, **_kwargs: {
            "text": "Filing",
            "chunks": [],
            "tables": [],
            "table_cnt": 0,
            "reason": "html",
        },
    )
    monkeypatch.setattr(
        pipeline,
        "enrich_filing_metadata_with_edgartools",
        lambda parsed, _accession: parsed,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_xbrl_facts",
        lambda _html: pytest.fail("per-filing extraction should not run"),
    )

    with patch("fin_pipeline.pipeline.ExchangeFilingModel") as model:
        model.return_value.model_dump.return_value = {
            "filingId": "sec_AAPL_0000000001-24-000001",
            "documentStatus": "PROCESSED",
        }
        completed = await pipeline.run_ingestion_pipeline(
            {
                "filingId": "sec_AAPL_0000000001-24-000001",
                "companyTicker": "AAPL",
                "stockCode": "UNKNOWN",
                "exchange": "UNKNOWN",
                "filingType": "10-K",
            },
            str(html_path),
            source="SEC",
            dry_run=True,
        )

    assert completed is True
    facts = json.loads(model.call_args.kwargs["xbrlFacts"])
    assert facts == {"assets": 456}
