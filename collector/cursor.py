"""Cursor persistence: per-source byte/line position, plus retry spool."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class Cursor:
    """JSON file mapping source-key -> {pos: int, mtime: float}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def get(self, key: str) -> dict:
        return self._data.get(key, {"pos": 0, "mtime": 0})

    def set(self, key: str, pos: int, mtime: float) -> None:
        self._data[key] = {"pos": pos, "mtime": mtime}

    def save(self) -> None:
        # atomic write
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(self.path.parent),
                                         encoding="utf-8") as tmp:
            json.dump(self._data, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, self.path)
