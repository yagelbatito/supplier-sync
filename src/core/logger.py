"""
Centralized logging configuration for supplier-sync.
Creates rotating file logs + console output with structured formatting.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

# Windows consoles default to the system OEM codepage (e.g. cp1255 in Hebrew
# locales), which can't encode characters like → ─. Force UTF-8 on stdout/stderr
# so logger messages with these characters don't trigger UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """
    Returns a named logger with console + file handlers.
    Safe to call multiple times — handlers are not duplicated.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler — one file per run date
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"sync_{today}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(numeric_level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.propagate = False
    return logger


# Default application logger
logger = get_logger("supplier_sync")
