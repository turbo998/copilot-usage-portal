"""Copilot CLI adapter — parses ~/.copilot/logs/process-*.log on Windows/Linux."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from cursor import Cursor

log = logging.getLogger(__name__)

# Lines that introduce a multi-line JSON response body look like:
#   2026-05-06T15:02:50.301Z [DEBUG] {
#   2026-05-06T15:02:50.301Z [DEBUG] data: {
# We match the timestamp + DEBUG marker + optional `data:` + `{` at end of line.
_BLOCK_START = re.compile(
    r"^(?P<ts>20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+"
    r"\[(?:DEBUG|INFO|TRACE)\]\s+(?:data:\s+)?\{",
    re.MULTILINE,
)


def _logs_root() -> Path:
    home = Path(os.path.expanduser("~"))
    return home / ".copilot" / "logs"


def _scan_json_blocks(text: str) -> Iterator[tuple[str | None, dict]]:
    """Yield (timestamp, json_obj) for every JSON object opened by a marker line.

    The Copilot CLI process log writes pretty-printed JSON response bodies
    inline after a marker like:

        2026-05-06T15:02:50.301Z [DEBUG] {
          "id": "...",
          ...
        }

    We anchor on each marker, locate the `{` it ends with, and balance-parse
    until the matching `}`.
    """
    for m in _BLOCK_START.finditer(text):
        ts = m.group("ts")
        # The opening `{` is the last character matched
        start = m.end() - 1
        n = len(text)
        depth = 0
        i = start
        in_str = False
        esc = False
        while i < n:
            ch = text[i]
            if esc:
                esc = False
            elif ch == "\\" and in_str:
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        chunk = text[start:i + 1]
                        try:
                            obj = json.loads(chunk)
                            yield ts, obj
                        except Exception:
                            pass
                        break
            i += 1


def _collect_from_file(path: Path, cursor: Cursor, host: str) -> list[dict]:
    key = str(path.resolve())
    state = cursor.get(key)
    pos = int(state.get("pos") or 0)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return []
    if size < pos:
        # File was rotated/truncated — restart from beginning
        pos = 0
    if size == pos:
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        text = f.read()
        new_pos = f.tell()

    out: list[dict] = []
    seen_ids: set[str] = set()
    for ts, obj in _scan_json_blocks(text):
        usage = obj.get("usage") if isinstance(obj, dict) else None
        if not isinstance(usage, dict):
            continue
        msg_id = obj.get("id") or f"{path.name}:{ts}:{len(out)}"
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)

        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or 0) or (prompt_tokens + completion_tokens)
        cached_tokens = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
                            or usage.get("cache_read_input_tokens") or 0)
        reasoning_tokens = int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        model = obj.get("model") or "unknown"
        ts_iso = ts or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        # Session id is not in CLI logs reliably; derive from file name
        session_id = path.stem  # e.g. "process-1234"
        out.append({
            "id": msg_id,
            "client": "copilot-cli",
            "host": host,
            "ts": ts_iso,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
            "session_id": session_id,
        })

    cursor.set(key, new_pos, path.stat().st_mtime)
    return out


class CopilotCliAdapter:
    name = "copilot-cli"

    def __init__(self, cursor: Cursor, host: str | None = None,
                 logs_dir: str | None = None) -> None:
        self._cursor = cursor
        self._host = host or socket.gethostname()
        self._dir = Path(logs_dir) if logs_dir else _logs_root()

    def collect(self) -> Iterable[dict]:
        if not self._dir.exists():
            log.info("Copilot CLI logs dir not found: %s", self._dir)
            return []
        events: list[dict] = []
        for p in sorted(self._dir.glob("process-*.log")):
            events.extend(_collect_from_file(p, self._cursor, self._host))
        return events

