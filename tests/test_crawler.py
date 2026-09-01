import pytest
from fin_pipeline.crawler import scan_directory

def test_scan_directory_missing():
    with pytest.raises(NotADirectoryError):
        list(scan_directory("/invalid/target/null/path"))

def test_scan_directory_with_mock_files(tmp_path):
    (tmp_path / "report_alpha.pdf").write_bytes(b"%PDF-1.4 data payload block")
    results = list(scan_directory(str(tmp_path), recursive=False))
    assert len(results) == 1
    assert results[0][1]["exchange"] == "LOCAL_FS"
