import pytest

from fin_pipeline.crawler import (
    scan_directory,
    scan_documents,
    scan_sec_edgar_html_directory,
)


def test_scan_directory_missing():
    with pytest.raises(NotADirectoryError):
        list(scan_directory("/invalid/target/null/path"))


def test_scan_directory_with_mock_files(tmp_path):
    (tmp_path / "report_alpha.pdf").write_bytes(b"%PDF-1.4 data payload block")
    results = list(scan_directory(str(tmp_path), recursive=False))
    assert len(results) == 1
    assert results[0][1]["exchange"] == "LOCAL_FS"


def test_scan_documents_includes_pdf_and_html_files(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 data payload block")
    (tmp_path / "report.html").write_text("<html>report</html>")
    (tmp_path / "notes.txt").write_text("ignored")

    results = list(scan_documents(str(tmp_path)))

    assert {path.rsplit(".", 1)[-1] for path, _ in results} == {"pdf", "html"}


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


def test_scan_directory_parses_nested_ticker_and_structured_filename(tmp_path):
    report_dir = tmp_path / "romania" / "TLV"
    report_dir.mkdir(parents=True)
    report = report_dir / "banca_transilvania_2020_131662_ar_en_5.pdf"
    report.write_bytes(b"%PDF-1.4 data payload block")

    result = next(iter(scan_directory(str(tmp_path))))[1]

    assert result["companyTicker"] == "TLV"
    assert result["stockName"] == "banca transilvania"
    assert result["stockCode"] == "131662"
    assert result["filingType"] == "ANNUAL_REPORT"
    assert result["filingDate"] == "2020-12-31"
    assert result["metadataSources"]["companyTicker"] == "parent_directory"
    assert result["metadataSources"]["stockName"] == "filename_company_name"


def test_scan_directory_parses_structured_sustainability_filename(tmp_path):
    report_dir = tmp_path / "romania" / "TLV"
    report_dir.mkdir(parents=True)
    report = report_dir / "banca_transilvania_2022_sustainability_report_13.pdf"
    report.write_bytes(b"%PDF-1.4 data payload block")

    result = next(iter(scan_directory(str(tmp_path))))[1]

    assert result["companyTicker"] == "TLV"
    assert result["stockName"] == "banca transilvania"
    assert result["stockCode"] == "UNKNOWN"
    assert result["filingType"] == "SUSTAINABILITY_REPORT"
    assert result["filingDate"] == "2022-12-31"


@pytest.mark.parametrize("report_code", ["annual_report", "annual-report"])
def test_scan_directory_accepts_annual_report_filename_variants(tmp_path, report_code):
    report_dir = tmp_path / "romania" / "Banca Transilvania"
    report_dir.mkdir(parents=True)
    (report_dir / f"131662.{report_code}.en.2018.pdf").write_bytes(b"%PDF-1.4")

    result = next(iter(scan_directory(str(tmp_path))))[1]

    assert result["stockCode"] == "131662"
    assert result["filingType"] == "ANNUAL_REPORT"
    assert result["filingDate"] == "2018-12-31"


def test_scan_directory_retains_defaults_for_unstructured_filename(tmp_path):
    report_dir = tmp_path / "romania" / "Banca Transilvania"
    report_dir.mkdir(parents=True)
    (report_dir / "annual-report.pdf").write_bytes(b"%PDF-1.4")

    result = next(iter(scan_directory(str(tmp_path))))[1]

    assert result["stockName"] == "Banca Transilvania"
    assert result["stockCode"] == "UNKNOWN"
    assert result["filingType"] == "ANNUAL_REPORT"
    assert result["filingDate"] is None


def test_scan_directory_parses_company_website_annual_report_filename(tmp_path):
    report = tmp_path / "Magontec_Limited_Annual_Report_2021.pdf"
    report.write_bytes(b"%PDF-1.4")

    result = next(iter(scan_directory(str(tmp_path), recursive=False)))[1]

    assert result["stockName"] == "Magontec Limited"
    assert result["stockCode"] == "UNKNOWN"
    assert result["filingType"] == "ANNUAL_REPORT"
    assert result["filingDate"] == "2021-12-31"
    assert result["metadataSources"]["stockName"] == "filename_company_name"
    assert result["metadataSources"]["filingDate"] == "filename_year_inferred"


@pytest.mark.parametrize(
    ("filename", "filing_type"),
    [
        ("ACME_Integrated_Report_FY2023.pdf", "INTEGRATED_REPORT"),
        ("ACME_Sustainability_Report_2022.pdf", "SUSTAINABILITY_REPORT"),
        ("ACME_ESG_Report_2024.pdf", "ESG_REPORT"),
    ],
)
def test_scan_directory_parses_non_sec_report_filename_types(
    tmp_path, filename, filing_type
):
    (tmp_path / filename).write_bytes(b"%PDF-1.4")

    result = next(iter(scan_directory(str(tmp_path), recursive=False)))[1]

    assert result["filingType"] == filing_type
    assert result["filingDate"].endswith("-12-31")


def test_scan_directory_parses_code_prefix_in_website_filename(tmp_path):
    report = tmp_path / "131662_Annual_Report_en_2018.pdf"
    report.write_bytes(b"%PDF-1.4")

    result = next(iter(scan_directory(str(tmp_path), recursive=False)))[1]

    assert result["stockCode"] == "131662"
    assert result["filingDate"] == "2018-12-31"


def test_scan_directory_ignores_non_pdf_files(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "notes.txt").write_text("not a filing")

    results = list(scan_directory(str(tmp_path)))

    assert len(results) == 1
    assert results[0][0].endswith("report.pdf")


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
