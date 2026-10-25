import json
import ssl
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
                                "form": "10-K",
                                "filed": "2024-02-01",
                                **({"fp": "FY"} if tag != "Assets" else {}),
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

    filing = parsed["0000000001-24-000001"]
    assert filing["period"] == "2023-12-31"
    assert filing["facts"]["revenues"] == {
        "value": 123.0,
        "tag": "Revenues",
        "unit": "USD",
        "period": "2023-12-31",
        "kind": "duration",
    }
    assert filing["facts"]["assets"] == {
        "value": 456.0,
        "tag": "Assets",
        "unit": "USD",
        "period": "2023-12-31",
        "kind": "instant",
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

    assert list(parsed) == ["0000000001-24-000001"]
    assert parsed["0000000001-24-000001"]["cik"] == "0000000002"
    assert parsed["0000000001-24-000001"]["facts"]["assets"]["value"] == 2.0


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

    assert parsed[accession]["facts"]["revenues"] == {
        "value": 123456.0,
        "tag": "Revenues",
        "unit": "USD",
        "period": "2023-12-31",
        "kind": "duration",
    }
    assert parsed[accession]["currency"] == "USD"


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
                "4",
            ),
            (accession, "Assets", "500", "USD", "20231231", "0"),
        ],
    )

    parsed = parse_fsds_zip(str(zip_path), {"1"})

    assert {
        concept: fact["value"] for concept, fact in parsed[accession]["facts"].items()
    } == {
        "revenues": 40,
        "researchAndDevelopmentExpense": 5,
        "assets": 500,
    }


def test_segment_revenue_concept_is_not_exported():
    assert "segmentRevenue" not in xbrl_bulk.CANONICAL_CONCEPTS
    assert "segmentRevenue" not in xbrl_bulk.CONCEPT_KIND


def test_dimensional_fact_rejected():
    assert not xbrl_bulk._is_consolidated_fact({"segments": "SegmentA"})
    assert xbrl_bulk._is_consolidated_fact({"segments": ""})
    assert not xbrl_bulk._is_consolidated_fact({"frame": "C-CY2023"})


def test_instant_uses_qtrs_zero():
    candidates = [
        {"uom": "USD", "ddate": "20231231", "qtrs": "4", "value": "4"},
        {"uom": "USD", "ddate": "20231231", "qtrs": "0", "value": "0"},
    ]

    selected = xbrl_bulk._select_fsds_fact(candidates, "assets", "20231231")

    assert selected["value"] == 0
    assert selected["qtrs"] == "0"


def test_duration_uses_qtrs_four_only():
    candidates = [
        {"uom": "USD", "ddate": "20231231", "qtrs": "1", "value": "1"},
        {"uom": "USD", "ddate": "20231231", "qtrs": "4", "value": "4"},
    ]
    assert xbrl_bulk._select_fsds_fact(
        candidates, "revenues", "20231231"
    )["value"] == 4
    assert (
        xbrl_bulk._select_fsds_fact(candidates[:1], "revenues", "20231231")
        is None
    )


def test_period_match_required():
    candidates = [
        {"uom": "USD", "ddate": "20221231", "qtrs": "4", "value": "2022"},
        {"uom": "USD", "ddate": "20231231", "qtrs": "4", "value": "2023"},
    ]
    selected = xbrl_bulk._select_fsds_fact(
        candidates, "revenues", "20231231"
    )
    assert selected["value"] == 2023


def test_priority_order_enforced(tmp_path):
    accession = "0000000001-24-000001"
    zip_path = tmp_path / "priority.zip"
    _write_fsds_zip(
        zip_path,
        [(accession, "1", "Example", "10-K", "20231231", "20240201")],
        [
            (
                accession,
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "100",
                "USD",
                "20231231",
                "4",
            ),
            (accession, "Revenues", "200", "USD", "20231231", "4"),
        ],
    )

    parsed = parse_fsds_zip(str(zip_path), {"1"})
    assert parsed[accession]["facts"]["revenues"]["value"] == 100


def test_priority_falls_back_when_first_tag_has_no_valid_period(tmp_path):
    accession = "0000000001-24-000001"
    zip_path = tmp_path / "priority-fallback.zip"
    _write_fsds_zip(
        zip_path,
        [(accession, "1", "Example", "10-K", "20231231", "20240201")],
        [
            (
                accession,
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "100",
                "USD",
                "20221231",
                "4",
            ),
            (accession, "Revenues", "200", "USD", "20231231", "4"),
        ],
    )

    parsed = parse_fsds_zip(str(zip_path), {"1"})
    assert parsed[accession]["facts"]["revenues"]["value"] == 200


def test_non_usd_rejected():
    assert (
        xbrl_bulk._select_fsds_fact(
            [
                {
                    "uom": "EUR",
                    "ddate": "20231231",
                    "qtrs": "4",
                    "value": "100",
                }
            ],
            "revenues",
            "20231231",
        )
        is None
    )


def test_invariant_operating_income_greater_than_revenue():
    facts = {
        "revenues": {"value": 1_000_000},
        "operatingIncomeLoss": {"value": 2_000_000},
    }
    xbrl_bulk._apply_invariants(facts, "accession")
    assert facts == {}


def test_invariant_cash_greater_than_assets():
    facts = {
        "assets": {"value": 3_000_000_000},
        "cashAndCashEquivalents": {"value": 5_000_000_000},
    }
    xbrl_bulk._apply_invariants(facts, "accession")
    assert "cashAndCashEquivalents" not in facts


def test_companyfacts_fy_filter():
    candidates = [
        {"unit": "USD", "end": "2023-12-31", "fp": "Q4", "val": 40},
        {"unit": "USD", "end": "2023-12-31", "fp": "FY", "val": 50},
    ]
    selected = xbrl_bulk._select_companyfacts_fact(
        candidates, "revenues", "2023-12-31"
    )
    assert selected["value"] == 50
    assert (
        xbrl_bulk._select_companyfacts_fact(
            candidates[:1], "revenues", "2023-12-31"
        )
        is None
    )


def test_companyfacts_instant_fact_requires_no_fp():
    candidates = [
        {"unit": "USD", "end": "2023-12-31", "fp": "FY", "val": 40},
        {"unit": "USD", "end": "2023-12-31", "val": 50},
    ]
    selected = xbrl_bulk._select_companyfacts_fact(
        candidates, "assets", "2023-12-31"
    )
    assert selected["value"] == 50


def test_download_companyfacts_sends_user_agent(tmp_path, monkeypatch):
    captured = {}
    zip_bytes = b"PK\x03\x04" + (b"x" * (1_100_000))

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        response = BytesIO(zip_bytes)
        response.headers = {"Content-Length": str(len(zip_bytes))}
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)

    downloaded_path = xbrl_bulk.download_companyfacts(str(tmp_path))

    assert captured["request"].get_header("User-agent") == xbrl_bulk.SEC_USER_AGENT
    assert captured["timeout"] == 60
    assert tmp_path.joinpath("companyfacts.zip").read_bytes() == zip_bytes
    assert downloaded_path == str(tmp_path / "companyfacts.zip")


def test_download_prints_progress(tmp_path, monkeypatch):
    valid_zip = b"PK\x03\x04" + (b"x" * (15 * 1_000_000))

    class FiveMegabyteChunks(BytesIO):
        def read(self, _size=-1):
            chunk = super().read(5_000_000)
            return chunk if chunk else b""

    def fake_urlopen(_request, timeout):
        assert timeout == 60
        response = FiveMegabyteChunks(valid_zip)
        response.headers = {"Content-Length": str(len(valid_zip))}
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
    assert any("(" in message and "%" in message for message in progress)
    assert any("100%" in message for message in progress)
    assert destination.stat().st_size == len(valid_zip)


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
    zip_bytes = b"PK\x03\x04" + (b"x" * (1_100_000))

    def fake_urlopen(_request, timeout):
        captured["timeout"] = timeout
        response = BytesIO(zip_bytes)
        response.headers = {"Content-Length": str(len(zip_bytes))}
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)

    xbrl_bulk.download_companyfacts(str(tmp_path))

    assert captured["timeout"] == xbrl_bulk.DOWNLOAD_TIMEOUT


def test_download_retries_on_ssl_error(tmp_path):
    destination = str(tmp_path / "retry.zip")
    with (
        patch(
            "fin_pipeline.utils.xbrl_bulk._download_once",
            side_effect=[ssl.SSLError("connection dropped")] * 2 + [destination],
        ) as download_once,
        patch("fin_pipeline.utils.xbrl_bulk.log.warning") as log_warning,
        patch("fin_pipeline.utils.xbrl_bulk.time.sleep"),
    ):
        result = xbrl_bulk._download_with_progress(
            "https://example.invalid/retry.zip", destination
        )

    assert result == destination
    assert download_once.call_count == 3
    assert log_warning.call_count == 2


def test_download_gives_up_after_max_retries(tmp_path):
    destination = str(tmp_path / "fail.zip")
    with (
        patch(
            "fin_pipeline.utils.xbrl_bulk._download_once",
            side_effect=xbrl_bulk.urllib.error.URLError("connection failed"),
        ) as download_once,
        patch("fin_pipeline.utils.xbrl_bulk.time.sleep"),
        pytest.raises(RuntimeError, match="after 3 attempts"),
    ):
        xbrl_bulk._download_with_progress(
            "https://example.invalid/fail.zip", destination
        )

    assert download_once.call_count == 3


def test_download_cleans_part_file_between_retries(tmp_path):
    destination = str(tmp_path / "partial.zip")
    partial_path = tmp_path / "partial.zip.part"

    def fail_after_writing_part(url, dest_path, *_args):
        assert not partial_path.exists()
        partial_path.write_bytes(b"partial")
        raise xbrl_bulk.urllib.error.URLError("connection failed")

    with (
        patch(
            "fin_pipeline.utils.xbrl_bulk._download_once",
            side_effect=fail_after_writing_part,
        ) as download_once,
        patch("fin_pipeline.utils.xbrl_bulk.time.sleep"),
        pytest.raises(RuntimeError, match="after 3 attempts"),
    ):
        xbrl_bulk._download_with_progress(
            "https://example.invalid/partial.zip", destination
        )

    assert download_once.call_count == 3
    assert not partial_path.exists()
    assert not (tmp_path / "partial.zip.tmp").exists()


def test_fsds_quarter_returns_none_on_retry_exhaustion(tmp_path):
    with patch(
        "fin_pipeline.utils.xbrl_bulk._download_with_progress",
        side_effect=RuntimeError("Download failed after 3 attempts"),
    ):
        result = xbrl_bulk.download_fsds_quarter(2020, 3, str(tmp_path))

    assert result is None


def test_download_rejects_html_error_page(tmp_path, monkeypatch):
    content = b"<html>SEC error page</html>"

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        response = FakeResponse(content)
        response.headers = {
            "Content-Type": "text/html",
            "Content-Length": str(len(content)),
        }
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "bad.zip"
    with pytest.raises(RuntimeError, match="not a valid ZIP"):
        xbrl_bulk._download_with_progress("https://example.invalid/bad.zip", str(dest))
    assert not dest.exists()
    assert not (tmp_path / "bad.zip.tmp").exists()


def test_download_rejects_tiny_zip(tmp_path, monkeypatch):
    zip_bytes = b"PK\x03\x04" + (b"x" * 1_024)

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        response = FakeResponse(zip_bytes)
        response.headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(len(zip_bytes)),
        }
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "tiny.zip"
    with pytest.raises(RuntimeError, match="not a valid ZIP"):
        xbrl_bulk._download_with_progress("https://example.invalid/tiny.zip", str(dest))


def test_download_accepts_valid_zip(tmp_path, monkeypatch):
    zip_bytes = b"PK\x03\x04" + (b"x" * (15 * 1_000_000))

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        response = FakeResponse(zip_bytes)
        response.headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(len(zip_bytes)),
        }
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "good.zip"
    result = xbrl_bulk._download_with_progress("https://example.invalid/good.zip", str(dest))
    assert result == str(dest)
    assert dest.exists()


def test_existing_corrupt_zip_redownloaded(tmp_path, monkeypatch):
    corrupted = tmp_path / "existing.zip"
    corrupted.write_bytes(b"not-a-zip")
    zip_bytes = b"PK\x03\x04" + (b"x" * (15 * 1_000_000))
    calls = {"count": 0}

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(_request, timeout):
        calls["count"] += 1
        response = FakeResponse(zip_bytes)
        response.headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(len(zip_bytes)),
        }
        return response

    monkeypatch.setattr(xbrl_bulk.urllib.request, "urlopen", fake_urlopen)
    result = xbrl_bulk._download_with_progress("https://example.invalid/existing.zip", str(corrupted))
    assert calls["count"] == 1
    assert result == str(corrupted)
    assert corrupted.exists()


def test_existing_valid_zip_skipped(tmp_path, monkeypatch):
    valid = tmp_path / "existing-valid.zip"
    valid.write_bytes(b"PK\x03\x04" + (b"x" * (15 * 1_000_000)))
    monkeypatch.setattr(
        xbrl_bulk.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("urlopen should not run when valid zip exists"),
    )
    result = xbrl_bulk._download_with_progress("https://example.invalid/existing-valid.zip", str(valid))
    assert result == str(valid)


def test_parse_rejects_bad_zip(tmp_path):
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not-a-zip")
    with patch("fin_pipeline.utils.xbrl_bulk.zipfile.ZipFile", side_effect=zipfile.BadZipFile("bad")):
        assert xbrl_bulk.parse_fsds_zip(str(bad_zip), None) == {}
        assert xbrl_bulk.parse_companyfacts_zip(str(bad_zip), None) == {}


def test_resumable_download_skips_done_file(tmp_path, monkeypatch):
    archive_path = tmp_path / "companyfacts.zip"
    valid_zip = b"PK\x03\x04" + (b"x" * (1_500_000))
    archive_path.write_bytes(valid_zip)

    xbrl_bulk._mark_download_done(str(archive_path))
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
                "unit": "USD",
                "period": "2023-12-31",
                "tag": "Revenues",
                "kind": "duration",
            },
            {
                "accession_no": "acc-2",
                "concept": "assets",
                "value": 200,
                "unit": "USD",
                "period": "2023-12-31",
                "tag": "Assets",
                "kind": "instant",
            },
        ]
    ).to_parquet(parquet_path, index=False)
    monkeypatch.setattr(pipeline, "_xbrl_bulk_cache", None)

    cache = pipeline._load_xbrl_bulk_cache(str(parquet_path))

    assert cache == {
        ("acc-1", "revenues"): {
            "value": 100,
            "tag": "Revenues",
            "unit": "USD",
            "period": "2023-12-31",
            "kind": "duration",
        },
        ("acc-2", "assets"): {
            "value": 200,
            "tag": "Assets",
            "unit": "USD",
            "period": "2023-12-31",
            "kind": "instant",
        },
    }


@pytest.mark.asyncio
async def test_pipeline_prefers_bulk_cache_over_filing_extraction(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "_load_xbrl_bulk_cache",
        lambda: {
            ("0000000001-24-000001", "revenues"): {
                "value": 123,
                "tag": "Revenues",
                "unit": "USD",
                "period": "2023-12-31",
                "kind": "duration",
            }
        },
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
    assert model.call_args.kwargs["xbrlSource"] == "bulk"
    assert model.call_args.kwargs["xbrlCurrency"] == "USD"
    assert json.loads(model.call_args.kwargs["xbrlProvenance"]) == {
        "revenues": {
            "tag": "Revenues",
            "unit": "USD",
            "period": "2023-12-31",
        }
    }


@pytest.mark.asyncio
async def test_file_pipeline_prefers_bulk_cache_over_filing_extraction(
    tmp_path, monkeypatch
):
    html_path = tmp_path / "filing.html"
    html_path.write_text("<html><body>Filing</body></html>")
    monkeypatch.setattr(
        pipeline,
        "_load_xbrl_bulk_cache",
        lambda: {
            ("0000000001-24-000001", "assets"): {
                "value": 456,
                "tag": "Assets",
                "unit": "USD",
                "period": "2023-12-31",
                "kind": "instant",
            }
        },
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
