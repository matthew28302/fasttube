from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _norm_query(q: str) -> str:
    q = (q or "").strip().lower()
    q = re.sub(r"[\s\-_]+", " ", q)
    q = re.sub(r"[^\w\s]", "", q)
    return " ".join(q.split())


class SearchCache:
    """Tiny disk cache: normalized query -> youtube url."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, str] = {}
        self._loaded = False

    def _load_once(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("search cache load failed: %s", self.path, exc_info=True)
            self._data = {}

    def get(self, query: str) -> Optional[str]:
        self._load_once()
        k = _norm_query(query)
        if not k:
            return None
        return self._data.get(k)

    def set(self, query: str, url: str) -> None:
        self._load_once()
        k = _norm_query(query)
        if not k or not url:
            return
        self._data[k] = url
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            logger.warning("search cache save failed: %s", self.path, exc_info=True)


