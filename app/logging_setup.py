from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(*, log_level: str = "INFO", log_file: Path) -> None:
    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers to avoid duplicate logs when reloaded.
    for h in list(root.handlers):
        root.removeHandler(h)

    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(formatter)

    root.addHandler(sh)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            filename=str(log_file),
            maxBytes=20 * 1024 * 1024,  # 20MB
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception:
        # Never crash app on file logging issues (permissions, missing parent, etc.)
        root.warning("Failed to enable file logging at %s; using console only", log_file, exc_info=True)

    # Ensure uvicorn loggers also propagate to root handlers
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(level)


