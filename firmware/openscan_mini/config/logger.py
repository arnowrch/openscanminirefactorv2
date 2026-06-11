"""
Structured logging configuration for OpenScan Mini firmware.

Supports both JSON and key=value formatted logs, with file and console output.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from pythonjsonlogger import jsonlogger


def setup_logging(
    log_level: str = "INFO",
    json_format: bool = True,
    log_dir: Path | str = "logs",
    console_output: bool = True,
) -> logging.Logger:
    """
    Configure structured logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, use JSON format; otherwise use key=value
        log_dir: Directory for log files
        console_output: If True, also log to console

    Returns:
        Configured root logger instance

    Example:
        >>> logger = setup_logging(log_level="DEBUG")
        >>> logger.info("Application started", extra={"version": "0.2.0"})
    """
    # Create log directory
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatters
    if json_format:
        log_format = "%(timestamp)s %(level)s %(name)s %(message)s"
        formatter = jsonlogger.JsonFormatter(
            log_format,
            timestamp=True,
            rename_fields={"timestamp": "time", "level": "level_name"},
        )
    else:
        log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        formatter = logging.Formatter(
            log_format,
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # File handler (main log)
    log_file = log_dir / f"openscan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(getattr(logging, log_level.upper()))
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper()))
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Log startup message
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging initialized",
        extra={
            "format": "json" if json_format else "key=value",
            "log_file": str(log_file),
            "level": log_level,
        },
    )

    return root_logger


class FastAPILogger:
    """Adapter for FastAPI access logs."""

    @staticmethod
    def get_gunicorn_config():
        """Get gunicorn/uvicorn logging config dict."""
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": jsonlogger.JsonFormatter,
                    "fmt": "%(asctime)s %(message)s",
                    "timestamp": True,
                },
                "access": {
                    "()": jsonlogger.JsonFormatter,
                    "fmt": "%(asctime)s %(message)s %(status_code)s",
                    "timestamp": True,
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO"},
                "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            },
        }
