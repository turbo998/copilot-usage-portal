"""Cosmos DB repository (singleton)."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable

from azure.cosmos import CosmosClient, ContainerProxy, exceptions
from azure.identity import DefaultAzureCredential


@lru_cache(maxsize=1)
def _client() -> CosmosClient:
    endpoint = os.environ["COSMOS_ENDPOINT"]
    # Prefer managed identity in Azure; falls back to az login locally.
    cred = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    return CosmosClient(url=endpoint, credential=cred)


@lru_cache(maxsize=4)
def container(name: str) -> ContainerProxy:
    db_name = os.environ["COSMOS_DATABASE"]
    db = _client().get_database_client(db_name)
    return db.get_container_client(name)


def events_container() -> ContainerProxy:
    return container(os.environ["COSMOS_EVENTS_CONTAINER"])


def cost_container() -> ContainerProxy:
    return container(os.environ["COSMOS_COST_CONTAINER"])


def plan_config_container() -> ContainerProxy:
    """Cosmos container that stores the active GHCP plan configuration.

    Schema (id='current'):
        {
          "id": "current",
          "plan": "Pro+" | "Pro" | "Business" | "Enterprise",
          "monthly_credits_included": int,
          "paid_credit_unit_price_usd": float,
          "updated_at": ISO-8601 string
        }

    The container name comes from COSMOS_PLAN_CONFIG_CONTAINER, defaulting
    to ``plan_config`` so existing deployments can opt in without changing
    Bicep parameters. Callers must tolerate the container not existing yet
    (see ``/api/quota`` for the fallback behaviour).
    """
    name = os.environ.get("COSMOS_PLAN_CONFIG_CONTAINER", "plan_config")
    return container(name)


def upsert_event(event: dict) -> tuple[bool, bool]:
    """Upsert a usage event. Returns (accepted, was_duplicate)."""
    c = events_container()
    try:
        # Use create_item with id; if exists, treat as duplicate (idempotent)
        c.create_item(body=event, enable_automatic_id_generation=False)
        return True, False
    except exceptions.CosmosResourceExistsError:
        return True, True
    except exceptions.CosmosHttpResponseError:
        raise


def query(sql: str, params: Iterable[dict] | None = None, partition_key: str | None = None,
          container_name: str | None = None) -> list[dict]:
    c = container(container_name) if container_name else events_container()
    kwargs: dict = {"query": sql, "parameters": list(params or [])}
    if partition_key is not None:
        kwargs["partition_key"] = partition_key
    else:
        kwargs["enable_cross_partition_query"] = True
    return list(c.query_items(**kwargs))
