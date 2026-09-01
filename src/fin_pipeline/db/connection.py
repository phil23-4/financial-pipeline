import json
import re
from datetime import datetime, timezone

from fin_pipeline.config.settings import DB_ENDPOINT, DB_AUTH
from fin_pipeline.db.db import initialize_schema, surreal_query


def _prune_none_values(value):
    """Remove optional fields with no value so SurrealDB does not reject nested NULLs."""
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            if child is None:
                continue
            cleaned[key] = _prune_none_values(child)
        return cleaned
    if isinstance(value, list):
        cleaned_items = []
        for item in value:
            if item is None:
                continue
            cleaned_items.append(_prune_none_values(item))
        return cleaned_items
    return value


def _surrealql_literal(value):
    """Render Python values into SurrealQL literals for direct SQL insertion."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        utc_dt = value.astimezone(timezone.utc)
        return f"d'{utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
    if isinstance(value, str):
        iso_like = re.match(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$", value)
        if iso_like:
            return f"d'{value}'"
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_surrealql_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{key}: {_surrealql_literal(val)}" for key, val in value.items())
        return "{" + inner + "}"
    return json.dumps(value, ensure_ascii=False)


class _HttpSurrealConnection:
    """Minimal compatibility adapter around the repo's HTTP helper functions."""

    async def connect(self):
        return None

    async def signin(self, auth):
        return auth

    async def use(self, namespace: str, database: str):
        return {"namespace": namespace, "database": database}

    async def delete_record(self, record_id: str):
        """Delete a stale record so a fresh write does not reuse partial data."""
        response = surreal_query(f"DELETE {record_id};", timeout=60)
        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(response["error"])
        return response

    async def upsert(self, record_id: str, payload: dict):
        payload = _prune_none_values(dict(payload))
        if "updatedAt" not in payload or payload["updatedAt"] is None:
            payload["updatedAt"] = datetime.now(timezone.utc)

        query = f"UPSERT {record_id} CONTENT {_surrealql_literal(payload)};"
        response = surreal_query(query, timeout=120)
        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(response["error"])
        return response

    async def query(self, sql: str, params: dict | None = None):
        response = surreal_query(sql, timeout=120)
        if isinstance(response, dict) and response.get("error"):
            raise RuntimeError(response["error"])
        return response

    async def close(self):
        return None


class SurrealConnection:
    """Async Context Manager context wrapper that uses the project's HTTP DB helper."""

    def __init__(self):
        self.db = None

    async def __aenter__(self):
        self.db = _HttpSurrealConnection()
        await self.db.connect()
        await self.db.signin(DB_AUTH)
        await self.db.use(namespace=DB_AUTH["namespace"], database=DB_AUTH["database"])
        initialize_schema()
        return self.db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            await self.db.close()
