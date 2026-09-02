"""Structured logging utilities with prefixes for better log aggregation."""

from enum import Enum
from loguru import logger as log


class LogLevel(str, Enum):
    """Standard log level prefixes for structured logging."""
    
    INIT = "[INIT]"
    PROCESSING = "[PROCESSING]"
    SUCCESS = "[SUCCESS]"
    ERROR = "[ERROR]"
    WARNING = "[WARNING]"
    DEBUG = "[DEBUG]"
    VALIDATION = "[VALIDATION]"
    DATABASE = "[DATABASE]"
    NETWORK = "[NETWORK]"


class StructuredLogger:
    """Wrapper around loguru logger with structured prefix logging."""
    
    @staticmethod
    def init(message: str, **kwargs) -> None:
        """Log initialization event."""
        log.info(f"{LogLevel.INIT} {message}", **kwargs)
    
    @staticmethod
    def processing(message: str, **kwargs) -> None:
        """Log processing event."""
        log.info(f"{LogLevel.PROCESSING} {message}", **kwargs)
    
    @staticmethod
    def success(message: str, **kwargs) -> None:
        """Log success event."""
        log.info(f"{LogLevel.SUCCESS} {message}", **kwargs)
    
    @staticmethod
    def error(message: str, **kwargs) -> None:
        """Log error event."""
        log.error(f"{LogLevel.ERROR} {message}", **kwargs)
    
    @staticmethod
    def warning(message: str, **kwargs) -> None:
        """Log warning event."""
        log.warning(f"{LogLevel.WARNING} {message}", **kwargs)
    
    @staticmethod
    def debug(message: str, **kwargs) -> None:
        """Log debug event."""
        log.debug(f"{LogLevel.DEBUG} {message}", **kwargs)
    
    @staticmethod
    def validation(message: str, **kwargs) -> None:
        """Log validation event."""
        log.warning(f"{LogLevel.VALIDATION} {message}", **kwargs)
    
    @staticmethod
    def database(message: str, **kwargs) -> None:
        """Log database event."""
        log.info(f"{LogLevel.DATABASE} {message}", **kwargs)
    
    @staticmethod
    def network(message: str, **kwargs) -> None:
        """Log network event."""
        log.info(f"{LogLevel.NETWORK} {message}", **kwargs)
