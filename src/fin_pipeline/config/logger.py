# pip install loguru
import os
import sys
from loguru import logger

# Configuration constants sourced from environment configs
LOG_LEVEL = os.getenv("FIN_PIPELINE_LOG_LEVEL", "INFO").upper()
LOG_DIR = os.getenv("FIN_PIPELINE_LOG_DIR", "logs")

def serialize_json_log(record):
    """
    Custom serializer to output uniform JSON lines for automated data metric ingestors.
    """
    subset = {
        "timestamp": record["date"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "exception": None
    }
    if record["exception"]:
        subset["exception"] = {
            "type": str(record["exception"].type),
            "value": str(record["exception"].value),
        }
    return subset

def _json_formatter(record):
    """Appends structural system contexts into log outputs."""
    import json
    record["extra"]["serialized"] = json.dumps(serialize_json_log(record))
    return "{extra[serialized]}\n"

def configure_pipeline_logger():
    """
    Initializes systemic logging infrastructure. Must be triggered at package boot up.
    """
    # 1. Purge default unconfigured logging handlers
    logger.remove()

    # 2. Add high-visibility colorized console output handler
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, level=LOG_LEVEL, format=console_format, colorize=True)

    # 3. Add production-grade structured JSON log rotators
    os.makedirs(LOG_DIR, exist_ok=True)
    json_log_path = os.path.join(LOG_DIR, "pipeline_metrics.json")
    
    logger.add(
        json_log_path,
        level=LOG_LEVEL,
        format=_json_formatter,
        rotation="10 MB",       # Rotate to a new file when the log hits 10 megabytes
        retention="30 days",    # Auto-cleanup files older than 30 days
        compression="zip",      # Compress old files to save server space
        enqueue=True            # Multi-threading and async safety queue barrier enabled
    )
    
    logger.debug(f"Structured logging system initialized. Base path: {json_log_path}")

# Export configured reference handle instance globally
pipeline_logger = logger
