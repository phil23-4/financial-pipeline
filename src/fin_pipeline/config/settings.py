import os
from pathlib import Path

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None

project_root = Path(__file__).resolve().parents[3]
env_values = {}
if dotenv_values is not None:
    env_path = project_root / ".env"
    if env_path.exists():
        env_values = dotenv_values(env_path)

DB_ENDPOINT = os.getenv("SURREAL_ENDPOINT", env_values.get("SURREAL_ENDPOINT", ""))
DB_USER = os.getenv("SURREAL_USER", env_values.get("SURREAL_USER", "root"))
DB_PASS = os.getenv("SURREAL_PASS", env_values.get("SURREAL_PASS", ""))
DB_NS = os.getenv("SURREAL_NAMESPACE", env_values.get("SURREAL_NAMESPACE", ""))
DB_DB = os.getenv("SURREAL_DATABASE", env_values.get("SURREAL_DATABASE", ""))
COMPANY_TABLE = os.getenv("COMPANY_TABLE", env_values.get("COMPANY_TABLE", ""))
EDGAR_IDENTITY = os.getenv("EDGAR_IDENTITY", env_values.get("EDGAR_IDENTITY", ""))

# Backwards-compatible aliases expected by older helper code.
SURREAL_ENDPOINT = DB_ENDPOINT
SURREAL_USER = DB_USER
SURREAL_PASS = DB_PASS
SURREAL_NS = DB_NS
SURREAL_DB = DB_DB
LOG_DIR = Path(project_root / "logs")

DB_AUTH = {
    "user": DB_USER,
    "pass": DB_PASS,
    "namespace": DB_NS,
    "database": DB_DB,
}
