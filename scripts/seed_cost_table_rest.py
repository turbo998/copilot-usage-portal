"""
Seed Cosmos DB `cost_table` container using only the stdlib + the account key.

Why not azure-cosmos? Because on Windows ARM64 the indirect `cryptography`
dependency requires a Rust toolchain to build a wheel. The Cosmos REST API only
needs HMAC-SHA256, which is in stdlib `hmac`/`hashlib`.

Usage:
  python scripts\\seed_cost_table_rest.py --account cosmos-... --database copilot_usage \
        --container cost_table --key <primary-master-key> --seed-file infra\\cost_table_seed.json
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import hmac
import http.client
import json
import sys
import urllib.parse
from pathlib import Path


def _sign(verb: str, resource_type: str, resource_link: str, date: str, master_key: str) -> str:
    payload = f"{verb.lower()}\n{resource_type.lower()}\n{resource_link}\n{date.lower()}\n\n"
    key = base64.b64decode(master_key)
    sig = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.b64encode(sig).decode()
    return urllib.parse.quote(f"type=master&ver=1.0&sig={sig_b64}", safe="")


def upsert(account: str, database: str, container: str, master_key: str, doc: dict) -> tuple[int, str]:
    host = f"{account}.documents.azure.com"
    path = f"/dbs/{database}/colls/{container}/docs"
    resource_link = f"dbs/{database}/colls/{container}"
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    auth = _sign("POST", "docs", resource_link, date, master_key)

    headers = {
        "Authorization": auth,
        "x-ms-date": date,
        "x-ms-version": "2018-12-31",
        "x-ms-documentdb-is-upsert": "true",
        "x-ms-documentdb-partitionkey": json.dumps([doc["id"]]),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(doc).encode("utf-8")
    conn = http.client.HTTPSConnection(host, timeout=30)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", errors="replace")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, help="Cosmos account name (without .documents.azure.com)")
    parser.add_argument("--database", default="copilot_usage")
    parser.add_argument("--container", default="cost_table")
    parser.add_argument("--key", required=True, help="Cosmos primary master key")
    parser.add_argument("--seed-file", default="infra/cost_table_seed.json")
    args = parser.parse_args()

    seed_path = Path(args.seed_file)
    if not seed_path.exists():
        print(f"seed file not found: {seed_path}", file=sys.stderr)
        return 2
    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("seed file must contain a JSON array", file=sys.stderr)
        return 2

    ok = 0
    failed = 0
    for row in rows:
        status, body = upsert(args.account, args.database, args.container, args.key, row)
        if status in (200, 201):
            ok += 1
            print(f"  upsert {row.get('id'):<28} -> {status}")
        else:
            failed += 1
            print(f"  upsert {row.get('id'):<28} -> {status}  {body}", file=sys.stderr)
    print(f"\nDone: {ok} upserted, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
