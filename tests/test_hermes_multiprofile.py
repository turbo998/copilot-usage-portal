"""Tests for HermesAdapter multi-profile state.db discovery.

Root cause being fixed: Hermes runs as multiple *profiles*, each with its own
~/.hermes/profiles/<name>/state.db. Newer models (e.g. claude-opus-4.8) are only
recorded in the per-profile DBs, while the legacy global ~/.hermes/state.db holds
older history. The original adapter read ONLY the global DB, so the portal never
saw the newer models. The adapter must discover and read the global DB *and* every
profile DB, merging events with independent per-DB watermarks.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# adapters/ lives under collector/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collector"))

from adapters.hermes import HermesAdapter  # noqa: E402
from cursor import Cursor  # noqa: E402


SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT,
    model TEXT,
    billing_provider TEXT,
    started_at REAL,
    ended_at REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens INTEGER,
    estimated_cost_usd REAL
);
"""


def _make_db(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        for r in rows:
            conn.execute(
                """INSERT INTO sessions
                   (id, source, model, billing_provider, started_at, ended_at,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, reasoning_tokens, estimated_cost_usd)
                   VALUES (:id,:source,:model,:billing_provider,:started_at,:ended_at,
                           :input_tokens,:output_tokens,:cache_read_tokens,
                           :cache_write_tokens,:reasoning_tokens,:estimated_cost_usd)""",
                {
                    "id": r["id"],
                    "source": r.get("source", "feishu"),
                    "model": r["model"],
                    "billing_provider": r.get("billing_provider", "copilot"),
                    "started_at": r.get("started_at", 1_000_000.0),
                    "ended_at": r.get("ended_at", 1_000_000.0),
                    "input_tokens": r.get("input_tokens", 100),
                    "output_tokens": r.get("output_tokens", 50),
                    "cache_read_tokens": r.get("cache_read_tokens", 0),
                    "cache_write_tokens": r.get("cache_write_tokens", 0),
                    "reasoning_tokens": r.get("reasoning_tokens", 0),
                    "estimated_cost_usd": r.get("estimated_cost_usd", 0.0),
                },
            )
        conn.commit()
    finally:
        conn.close()


# A timestamp safely in the past relative to the adapter's `now - 300` cutoff.
PAST = 1_700_000_000.0


@pytest.fixture()
def hermes_home(tmp_path: Path) -> Path:
    """Build a fake ~/.hermes tree: global DB (old model) + 2 profile DBs (new model)."""
    home = tmp_path / ".hermes"
    _make_db(home / "state.db", [
        {"id": "g1", "model": "claude-opus-4-7", "ended_at": PAST},
    ])
    _make_db(home / "profiles" / "fde" / "state.db", [
        {"id": "fde1", "model": "claude-opus-4-8", "ended_at": PAST + 10},
    ])
    _make_db(home / "profiles" / "researcher" / "state.db", [
        {"id": "res1", "model": "claude-opus-4-8", "ended_at": PAST + 20},
    ])
    return home


def _collect(home: Path, cursor_path: Path) -> list[dict]:
    cursor = Cursor(cursor_path)
    adapter = HermesAdapter(cursor, host="testhost", hermes_home=home)
    events = list(adapter.collect())
    cursor.save()
    return events


def test_discovers_global_and_profile_dbs(hermes_home: Path, tmp_path: Path):
    events = _collect(hermes_home, tmp_path / "cursor.json")
    models = {e["model"] for e in events}
    assert "claude-opus-4-8" in models, "must surface model that exists only in profile DBs"
    assert "claude-opus-4-7" in models, "must still include the legacy global DB model"
    ids = {e["id"] for e in events}
    assert ids == {"g1", "fde1", "res1"}


def test_all_events_tagged_hermes_client(hermes_home: Path, tmp_path: Path):
    events = _collect(hermes_home, tmp_path / "cursor.json")
    assert all(e["client"] == "hermes" for e in events)
    assert all(e["total_tokens"] > 0 for e in events)


def test_per_db_watermark_prevents_redelivery(hermes_home: Path, tmp_path: Path):
    cur = tmp_path / "cursor.json"
    first = _collect(hermes_home, cur)
    assert len(first) == 3
    # Second run with no new rows -> nothing re-emitted (watermarks advanced per DB).
    second = _collect(hermes_home, cur)
    assert second == []


def test_new_profile_row_emitted_on_next_run(hermes_home: Path, tmp_path: Path):
    cur = tmp_path / "cursor.json"
    _collect(hermes_home, cur)
    # Append a newer 4.8 session to the fde profile DB.
    _append = sqlite3.connect(str(hermes_home / "profiles" / "fde" / "state.db"))
    try:
        _append.execute(
            """INSERT INTO sessions (id, source, model, billing_provider, started_at,
               ended_at, input_tokens, output_tokens, cache_read_tokens,
               cache_write_tokens, reasoning_tokens, estimated_cost_usd)
               VALUES ('fde2','feishu','claude-opus-4-8','copilot',?,?,200,80,0,0,0,0)""",
            (PAST + 100, PAST + 100),
        )
        _append.commit()
    finally:
        _append.close()
    second = _collect(hermes_home, cur)
    assert {e["id"] for e in second} == {"fde2"}


def test_missing_home_returns_empty(tmp_path: Path):
    events = _collect(tmp_path / "does-not-exist", tmp_path / "cursor.json")
    assert events == []


def test_backwards_compat_usage_file_single_db(tmp_path: Path):
    """Explicit usage_file= still reads exactly that one DB (legacy callers)."""
    db = tmp_path / "solo.db"
    _make_db(db, [{"id": "s1", "model": "claude-opus-4-8", "ended_at": PAST}])
    cursor = Cursor(tmp_path / "cursor.json")
    adapter = HermesAdapter(cursor, host="h", usage_file=str(db))
    events = list(adapter.collect())
    assert {e["id"] for e in events} == {"s1"}


def test_hermes_home_env_var_overrides_discovery(hermes_home: Path, tmp_path: Path, monkeypatch):
    """HERMES_HOME env var drives discovery when no explicit home is passed."""
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    cursor = Cursor(tmp_path / "cursor.json")
    adapter = HermesAdapter(cursor, host="h")  # no hermes_home kwarg
    events = list(adapter.collect())
    assert "claude-opus-4-8" in {e["model"] for e in events}
    assert {e["id"] for e in events} == {"g1", "fde1", "res1"}
