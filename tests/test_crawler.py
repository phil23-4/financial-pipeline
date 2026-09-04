import pytest

from fin_pipeline.crawler import scan_directory, scan_sec_edgar_html_directory


def test_scan_directory_missing():
    with pytest.raises(NotADirectoryError):
        list(scan_directory("/invalid/target/null/path"))


def test_scan_directory_with_mock_files(tmp_path):
    (tmp_path / "report_alpha.pdf").write_bytes(b"%PDF-1.4 data payload block")
    results = list(scan_directory(str(tmp_path), recursive=False))
    assert len(results) == 1
    assert results[0][1]["exchange"] == "LOCAL_FS"


def test_scan_directory_parses_local_filing_path_and_filename(tmp_path):
    report_dir = tmp_path / "romania" / "Banca Transilvania"
    report_dir.mkdir(parents=True)
    report = report_dir / "131662.ar.en.2018.pdf"
    report.write_bytes(b"%PDF-1.4 data payload block")

    result = next(iter(scan_directory(str(tmp_path))))[1]

    assert result["stockName"] == "Banca Transilvania"
    assert result["stockCode"] == "131662"
    assert result["filingType"] == "ANNUAL_REPORT"
    assert result["filingDate"] == "2018-12-31"
    assert result["metadataSources"]["stockName"] == "parent_directory"


def test_scan_sec_edgar_html_directory_keeps_each_accession(tmp_path):
    first = tmp_path / "sec-edgar-filings" / "BAC" / "10-K" / "0000000001-22-000001"
    second = tmp_path / "sec-edgar-filings" / "BAC" / "10-K" / "0000000001-23-000001"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "primary-document.html").write_text("<html></html>")
    (second / "primary-document.html").write_text("<html></html>")

    results = list(scan_sec_edgar_html_directory(str(tmp_path)))

    assert len(results) == 2
    assert {item[1]["filingId"] for item in results} == {
        "sec_BAC_0000000001-22-000001",
        "sec_BAC_0000000001-23-000001",
    }
