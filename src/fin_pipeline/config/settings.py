import os
import tempfile
import warnings
from pathlib import Path
from types import MappingProxyType

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None
    warnings.warn(
        "python-dotenv is not installed; skipping .env file loading.",
        category=ImportWarning,
        stacklevel=1,
    )

def find_project_root(starting_path: Path, markers=("pyproject.toml", ".git", "setup.py")) -> Path:
    """Find the project root by searching upward for marker files, falling back to package root depth."""
    for parent in starting_path.parents:
        if any((parent / marker).exists() for marker in markers):
            return parent
    
    # Fallback to repository root relative to src/fin_pipeline/config/ settings location
    if len(starting_path.parents) >= 3:
        return starting_path.parents[3]
    return starting_path.parent

project_root = find_project_root(Path(__file__).resolve())

env_path = project_root / ".env"
env_values = {}

if dotenv_values:
    if env_path.exists():
        env_values = dotenv_values(env_path)
    else:
        warnings.warn(
            f".env file not found at {env_path}; falling back to system environment variables.",
            category=UserWarning,
            stacklevel=1,
        )

def get_env(key: str, default: str = "") -> str:
    """Retrieve configuration, giving system OS variables precedence over .env file values."""
    return os.getenv(key, env_values.get(key, default))

DB_ENDPOINT = get_env("SURREAL_ENDPOINT")
DB_USER = get_env("SURREAL_USER", "root")
DB_PASS = get_env("SURREAL_PASS")
DB_NS = get_env("SURREAL_NAMESPACE")
DB_DB = get_env("SURREAL_DATABASE")
COMPANY_TABLE = get_env("COMPANY_TABLE")
EDGAR_IDENTITY = get_env("EDGAR_IDENTITY")

# Backwards-compatible aliases
SURREAL_ENDPOINT = DB_ENDPOINT
SURREAL_USER = DB_USER
SURREAL_PASS = DB_PASS
SURREAL_NS = DB_NS
SURREAL_DB = DB_DB

# Points to existing log/ folder in project root
LOG_DIR = project_root / "log"

def get_log_dir() -> Path:
    """Ensure log directory exists, falling back to a temp directory if primary path is unwriteable."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        return LOG_DIR
    except OSError as e:
        fallback_dir = Path(tempfile.gettempdir()) / "fin_pipeline_log"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        warnings.warn(
            f"Could not create log directory at {LOG_DIR} ({e}). Falling back to {fallback_dir}.",
            RuntimeWarning,
            stacklevel=1,
        )
        return fallback_dir

# Immutable configuration dictionary
DB_AUTH = MappingProxyType({
    "user": DB_USER,
    "pass": DB_PASS,
    "namespace": DB_NS,
    "database": DB_DB,
})