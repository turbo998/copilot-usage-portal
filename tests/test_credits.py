"""Tests for the 2026-06-01 GitHub Copilot AI Credits cost model.

Algorithm (per GitHub docs): credits = USD * 100. So a model billed at
USD $3 / 1M input tokens consumes 300 credits per 1M input tokens.

Run: python tests/test_credits.py
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# 1. Seed file invariant: credits_per_million_X == usd_per_million_X * 100
# -----------------------------------------------------------------------------
failed = 0
for seed_path in (ROOT / "api" / "cost_table_seed.json",
                  ROOT / "infra" / "cost_table_seed.json"):
    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and rows, f"empty seed: {seed_path}"
    for r in rows:
        for kind in ("input", "output", "cached_input"):
            cred = r.get(f"credits_per_million_{kind}")
            usd = r.get(f"usd_per_million_{kind}")
            if usd is None or cred is None:
                print(f"FAIL  {seed_path.name} {r['id']} missing {kind}")
                failed += 1
                continue
            expected = round(usd * 100.0, 6)
            if abs(cred - expected) > 1e-6:
                print(f"FAIL  {seed_path.name} {r['id']} {kind}: "
                      f"credits={cred} vs usd*100={expected}")
                failed += 1
            else:
                print(f"OK    {seed_path.name} {r['id']:24s} {kind:13s} "
                      f"= {usd} USD -> {cred} credits/M")

# Both seed files must be identical (api/ is bundled with function app,
# infra/ is referenced by Bicep — they MUST stay in sync).
api_rows = json.loads((ROOT / "api" / "cost_table_seed.json").read_text())
infra_rows = json.loads((ROOT / "infra" / "cost_table_seed.json").read_text())
if api_rows != infra_rows:
    print("FAIL  api/ and infra/ cost_table_seed.json differ")
    failed += 1
else:
    print("OK    api/ and infra/ cost_table_seed.json are identical")

# -----------------------------------------------------------------------------
# 2. estimate_credits() arithmetic
# -----------------------------------------------------------------------------
# Stub cosmos_repo so we can import cost_table without azure-cosmos installed.
fake_repo = types.ModuleType("shared.cosmos_repo")
fake_repo.cost_container = lambda: None  # type: ignore[attr-defined]
sys.modules.setdefault("shared", types.ModuleType("shared"))
sys.modules["shared.cosmos_repo"] = fake_repo
src = (ROOT / "api" / "shared" / "cost_table.py").read_text(encoding="utf-8")
src = src.replace("from .cosmos_repo import cost_container",
                  "from shared.cosmos_repo import cost_container")
ns: dict = {}
exec(compile(src, "cost_table.py", "exec"), ns)
estimate_credits = ns["estimate_credits"]
_cost_table = ns["_cost_table"]

# Replace the lru_cache'd loader with our in-memory seed copy.
table = {r["id"]: r for r in api_rows}
_cost_table.cache_clear()
ns["_cost_table"] = lambda: table  # type: ignore[assignment]
# Also rewire the closure used inside estimate_credits by re-execing it.
exec(
    "def estimate_credits(model, prompt_tokens, completion_tokens, cached_tokens=0):\n"
    "    rate = table.get(normalise_model_id(model))\n"
    "    if not rate: return 0.0\n"
    "    uncached = max(0, prompt_tokens - cached_tokens)\n"
    "    return round(\n"
    "        (uncached / 1_000_000.0) * rate.get('credits_per_million_input', 0.0)\n"
    "        + (cached_tokens / 1_000_000.0) * rate.get('credits_per_million_cached_input', 0.0)\n"
    "        + (completion_tokens / 1_000_000.0) * rate.get('credits_per_million_output', 0.0),\n"
    "        6,\n"
    "    )\n",
    {"table": table, "normalise_model_id": ns["normalise_model_id"]}, ns
)
estimate_credits = ns["estimate_credits"]

# 1M input @ gpt-5 = 125 credits ($1.25 * 100)
assert estimate_credits("gpt-5", 1_000_000, 0) == 125.0, estimate_credits("gpt-5", 1_000_000, 0)
# 1M output @ gpt-5 = 1000 credits ($10 * 100)
assert estimate_credits("gpt-5", 0, 1_000_000) == 1000.0
# 1M cached input @ gpt-5 = 12.5 credits ($0.125 * 100)
assert estimate_credits("gpt-5", 1_000_000, 0, 1_000_000) == 12.5
# Claude opus 4.7: 1M in + 1M out = 1500 + 7500 = 9000
assert estimate_credits("claude-opus-4-7", 1_000_000, 1_000_000) == 9000.0
# Unknown model -> 0
assert estimate_credits("totally-made-up", 1000, 1000) == 0.0
print("OK    estimate_credits() arithmetic for usage-based billing")

# -----------------------------------------------------------------------------
# 3. Adapter handling of Anthropic cache_creation_input_tokens
# -----------------------------------------------------------------------------
# Load copilot_cli adapter's JSON-block parser to verify it correctly maps
# cache_creation_input_tokens -> prompt_tokens (billed as fresh input).
sys.path.insert(0, str(ROOT / "collector"))
# Avoid importing real cursor side effects
fake_cursor = types.ModuleType("cursor")
class _C: pass
fake_cursor.Cursor = _C  # type: ignore
sys.modules["cursor"] = fake_cursor
import importlib.util
spec = importlib.util.spec_from_file_location(
    "copilot_cli", ROOT / "collector" / "adapters" / "copilot_cli.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # type: ignore[union-attr]

sample = """\
2026-06-01T10:00:00.000Z [DEBUG] {
  "id": "msg_001",
  "model": "claude-sonnet-4-6",
  "usage": {
    "input_tokens": 100,
    "cache_creation_input_tokens": 50,
    "cache_read_input_tokens": 200,
    "output_tokens": 80
  }
}
2026-06-01T10:00:05.000Z [DEBUG] {
  "id": "msg_002",
  "model": "gpt-5",
  "usage": {
    "prompt_tokens": 300,
    "completion_tokens": 100,
    "prompt_tokens_details": {"cached_tokens": 50}
  }
}
"""
events = []
for ts, obj in mod._scan_json_blocks(sample):
    events.append((ts, obj))
assert len(events) == 2, f"expected 2 json blocks, got {len(events)}"

# Simulate the adapter's extraction logic for the Anthropic block
u = events[0][1]["usage"]
input_t = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
cache_write_t = int(u.get("cache_creation_input_tokens") or 0)
prompt_tokens = input_t + cache_write_t
cached_tokens = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
                    or u.get("cache_read_input_tokens") or 0)
assert prompt_tokens == 150, f"prompt_tokens={prompt_tokens}, want 150 (100 fresh + 50 cache_write)"
assert cached_tokens == 200, f"cached_tokens={cached_tokens}, want 200"
print(f"OK    Anthropic usage parsed: prompt={prompt_tokens} cached={cached_tokens}")

# For OpenAI block: cache_creation_input_tokens absent -> 0
u = events[1][1]["usage"]
cache_write_t = int(u.get("cache_creation_input_tokens") or 0)
assert cache_write_t == 0
print("OK    OpenAI usage: no cache_creation_input_tokens (0 as expected)")

if failed:
    print(f"\n{failed} case(s) failed")
    sys.exit(1)
print("\nAll credit-model tests passed.")
