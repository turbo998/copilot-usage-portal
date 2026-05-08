"""Quick smoke test: parse local Copilot CLI logs without sending.

Also runs the production normaliser + cost estimator over the parsed events to
sanity-check the cost table and surface unknown models.
"""
import json
import os
import sys
import types
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cursor import Cursor
from adapters.copilot_cli import CopilotCliAdapter

# Stub azure-cosmos so we can import the production normaliser/family helpers.
fake_repo = types.ModuleType("shared.cosmos_repo")
fake_repo.cost_container = lambda: None  # type: ignore[attr-defined]
sys.modules["shared"] = types.ModuleType("shared")
sys.modules["shared.cosmos_repo"] = fake_repo
src_path = Path(__file__).resolve().parent.parent / "api" / "shared" / "cost_table.py"
src = src_path.read_text(encoding="utf-8").replace(
    "from .cosmos_repo import cost_container",
    "from shared.cosmos_repo import cost_container",
)
ns: dict = {}
exec(compile(src, "cost_table.py", "exec"), ns)
normalise = ns["normalise_model_id"]
family = ns["model_family"]

# Load cost table from seed file (no Cosmos)
seed_path = Path(__file__).resolve().parent.parent / "infra" / "cost_table_seed.json"
seed_rows = json.loads(seed_path.read_text(encoding="utf-8"))
cost_by_id = {r["id"]: r for r in seed_rows}


def estimate_credits(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int) -> float:
    rate = cost_by_id.get(normalise(model))
    if not rate:
        return 0.0
    uncached = max(0, prompt_tokens - cached_tokens)
    return round(
        (uncached / 1_000_000.0) * rate.get("credits_per_million_input", 0.0)
        + (cached_tokens / 1_000_000.0) * rate.get("credits_per_million_cached_input", 0.0)
        + (completion_tokens / 1_000_000.0) * rate.get("credits_per_million_output", 0.0),
        6,
    )


tmp = Path(os.path.expanduser("~")) / ".copilot-usage-collector" / "smoke-cursor.json"
if tmp.exists():
    tmp.unlink()
cur = Cursor(tmp)

adapter = CopilotCliAdapter(cur)
events = list(adapter.collect())
print(f"parsed {len(events)} events from {adapter._dir}")

if not events:
    sys.exit(0)

# Aggregate
by_raw_model: dict[str, int] = defaultdict(int)
by_norm_model: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0, 0.0])
unknown: dict[str, int] = defaultdict(int)
total_in = total_out = total_cached = 0
total_credits = 0.0

for e in events:
    raw = e["model"]
    norm = normalise(raw)
    by_raw_model[raw] += 1
    by_norm_model[norm][0] += 1
    by_norm_model[norm][1] += e["prompt_tokens"]
    by_norm_model[norm][2] += e["completion_tokens"]
    by_norm_model[norm][3] += e["cached_tokens"]
    cred = estimate_credits(raw, e["prompt_tokens"], e["completion_tokens"], e["cached_tokens"])
    by_norm_model[norm][4] += cred
    total_in += e["prompt_tokens"]
    total_out += e["completion_tokens"]
    total_cached += e["cached_tokens"]
    total_credits += cred
    if norm not in cost_by_id:
        unknown[norm] += 1

print(f"\n--- totals ---  prompt={total_in:,}  completion={total_out:,}  cached={total_cached:,}")
print(f"--- est credits ---  total={total_credits:.2f}  cache_hit={total_cached/total_in*100:.1f}%")

print("\n--- per normalised model (count, prompt, completion, cached, credits) ---")
for m, (n, p, c, ca, cr) in sorted(by_norm_model.items(), key=lambda x: -x[1][1]):
    flag = " (no rate)" if m not in cost_by_id else ""
    print(f"  {m:32s}  {n:>5}  {p:>12,}  {c:>9,}  {ca:>12,}  {cr:>8.2f}{flag}")

if unknown:
    print(f"\n--- unknown models (no cost rate) ---")
    for m, n in sorted(unknown.items(), key=lambda x: -x[1]):
        print(f"  {m}: {n}")

