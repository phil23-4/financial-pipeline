import os
import sys
import json
from datetime import datetime
from loguru import logger

LOG_LEVEL = os.getenv("FIN_PIPELINE_LOG_LEVEL", "INFO").upper()
LOG_DIR = os.getenv("FIN_PIPELINE_LOG_DIR", "logs")


def serialize_json_log(record):
    """Formats Python logs into unified JSON configurations for metric parsers."""
    timestamp = record.get("time") or record.get("date") or datetime.utcnow()
    if hasattr(timestamp, "strftime"):
        timestamp_value = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        timestamp_value = str(timestamp)

    level = record.get("level")
    level_name = getattr(level, "name", str(level))

    exception = record.get("exception")
    if exception is not None:
        exc_type = type(exception).__name__
        exc_value = str(exception)
        exception_payload = {"type": exc_type, "value": exc_value}
    else:
        exception_payload = None

    return {
        "timestamp": timestamp_value,
        "level": level_name,
        "message": record.get("message"),
        "module": record.get("module"),
        "function": record.get("function"),
        "line": record.get("line"),
        "exception": exception_payload,
    }


def _json_formatter(record):
    record["extra"]["serialized"] = json.dumps(serialize_json_log(record))
    return "{extra[serialized]}\n"


def configure_pipeline_logger():
    """Builds two separate stream sinks: colorized terminal output and a daily rotating file."""
    logger.remove()

    console_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
    logger.add(sys.stderr, level=LOG_LEVEL, format=console_format, colorize=True)

    os.makedirs(LOG_DIR, exist_ok=True)
    logger.add(
        os.path.join(LOG_DIR, "pipeline_metrics.json"),
        level=LOG_LEVEL,
        format=_json_formatter,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
    )


pipeline_logger = logger
