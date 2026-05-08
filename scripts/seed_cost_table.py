"""Seed the cost_table container with per-model AI Credits rates.

Usage:
    python scripts/seed_cost_table.py --endpoint https://xxx.documents.azure.com:443/ \
        --database copilot_usage --seed-file infra/cost_table_seed.json

Uses DefaultAzureCredential — make sure you ran `az login` and your account has
Cosmos DB Built-in Data Contributor role on the account.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from azure.cosmos import CosmosClient, PartitionKey, exceptions  # type: ignore
from azure.identity import DefaultAzureCredential  # type: ignore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--endpoint', required=True)
    ap.add_argument('--database', default='copilot_usage')
    ap.add_argument('--container', default='cost_table')
    ap.add_argument('--seed-file', required=True)
    args = ap.parse_args()

    seed_path = Path(args.seed_file)
    if not seed_path.exists():
        print(f'seed file not found: {seed_path}', file=sys.stderr)
        return 2
    seed = json.loads(seed_path.read_text(encoding='utf-8'))
    rows = seed.get('rates') if isinstance(seed, dict) else seed
    if not isinstance(rows, list):
        print('seed file must contain a list of rate rows or {"rates": [...]}', file=sys.stderr)
        return 2

    cred = DefaultAzureCredential()
    client = CosmosClient(args.endpoint, credential=cred)
    db = client.get_database_client(args.database)
    cont = db.get_container_client(args.container)

    written = 0
    for row in rows:
        if 'id' not in row:
            row['id'] = row.get('model') or row.get('model_id')
        if not row.get('id'):
            print(f'  ! skipping row without id/model: {row}')
            continue
        try:
            cont.upsert_item(row)
            written += 1
            print(f'  ok {row["id"]}')
        except exceptions.CosmosHttpResponseError as e:
            print(f'  ! {row["id"]}: {e.message}')
    print(f'\nSeeded {written}/{len(rows)} rows into {args.database}/{args.container}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
