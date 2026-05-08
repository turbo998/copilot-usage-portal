"""Standalone tests for normalise_model_id (no Cosmos required).

Run: python tests/test_normalise.py
"""
from __future__ import annotations
import sys
import types
from pathlib import Path

src_path = Path(__file__).resolve().parent.parent / "api" / "shared" / "cost_table.py"
src = src_path.read_text(encoding="utf-8")

# Stub the relative `from .cosmos_repo import cost_container` so tests don't
# need azure-cosmos installed.
fake_repo = types.ModuleType("shared.cosmos_repo")
fake_repo.cost_container = lambda: None  # type: ignore[attr-defined]
sys.modules["shared"] = types.ModuleType("shared")
sys.modules["shared.cosmos_repo"] = fake_repo
src = src.replace("from .cosmos_repo import cost_container",
                  "from shared.cosmos_repo import cost_container")

ns: dict = {}
exec(compile(src, "cost_table.py", "exec"), ns)
normalise = ns["normalise_model_id"]
family = ns["model_family"]

CASES = [
    ("gpt-5-mini-2025-08-07",                "gpt-5-mini"),
    ("gpt-5.4-2026-03-05",                   "gpt-5"),
    ("gpt-5.4-mini-2026-03-05",              "gpt-5-mini"),
    ("claude-opus-4-7",                      "claude-opus-4-7"),
    ("claude-opus-4.6",                      "claude-opus-4-6"),
    ("claude-opus-4.6-1m",                   "claude-opus-4-6-1m"),
    ("claude-haiku-4-5-20251001",            "claude-haiku-4-5"),
    ("claude-sonnet-4.6",                    "claude-sonnet-4-6"),
    ("github-copilot/gpt-5-mini",            "gpt-5-mini"),
    ("capi-noe-ptuc-h200-ib-gpt-5-mini-2025-08-07", "gpt-5-mini"),
    ("",                                     "unknown"),
    ("o3-mini",                              "o3-mini"),
    ("gpt-4.1",                              "gpt-4-1"),
]

FAMILY_CASES = [
    ("gpt-5-mini-2025-08-07", "gpt"),
    ("claude-opus-4-7",       "claude"),
    ("o3-mini",               "gpt"),
    ("",                      "other"),
]

failed = 0
for raw, want in CASES:
    got = normalise(raw)
    ok = got == want
    print(f"{'OK ' if ok else 'FAIL'}  {raw!r:60s} -> {got!r:30s} (want {want!r})")
    if not ok:
        failed += 1

for raw, want in FAMILY_CASES:
    got = family(raw)
    ok = got == want
    print(f"{'OK ' if ok else 'FAIL'}  family({raw!r:30s}) -> {got!r:8s} (want {want!r})")
    if not ok:
        failed += 1

if failed:
    print(f"\n{failed} case(s) failed")
    sys.exit(1)
print(f"\nAll {len(CASES) + len(FAMILY_CASES)} cases passed.")
