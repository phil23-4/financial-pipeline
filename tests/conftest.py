import pytest

@pytest.fixture(autouse=True)
def mock_settings_env(monkeypatch):
    """Enforces mock tracking environment variables so unit tests never hit live production setups."""
    monkeypatch.setenv("SURREAL_ENDPOINT", "ws://localhost:9999/rpc")
    monkeypatch.setenv("SURREAL_USER", "test_root")
    monkeypatch.setenv("SURREAL_PASS", "test_pass")
    monkeypatch.setenv("SURREAL_NAMESPACE", "test_ns")
    monkeypatch.setenv("SURREAL_DATABASE", "test_db")

@pytest.fixture
def sample_pdf_path(tmp_path):
    """Builds a temporary baseline binary layout file signature."""
    pdf_file = tmp_path / "mock_report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock contents layout buffer parameters data info")
    return str(pdf_file)
