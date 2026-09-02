import json
import re
from datetime import datetime, timezone

from fin_pipeline.config.settings import DB_ENDPOINT, DB_AUTH
from fin_pipeline.db.db import initialize_schema, surreal_query, surreal_rpc
from fin_pipeline.utils.db_utils import quote_record_id


# SurrealDB's /sql endpoint has a smaller request limit than /rpc.
_SQL_UPSERT_LIMIT_BYTES = 900_000


def _raise_for_query_error(response):
    """Raise for both HTTP errors and SurrealDB errors inside result arrays."""
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(response["error"])
    if isinstance(response, list):
        for result in response:
            if isinstance(result, dict) and result.get("status") == "ERR":
                raise RuntimeError(str(result.get("result", "Unknown query error")))
    return response





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
        response = surreal_query(f"DELETE {quote_record_id(record_id)};", timeout=60)
        return _raise_for_query_error(response)

    async def upsert(self, record_id: str, payload: dict):
        payload = _prune_none_values(dict(payload))
        if "updatedAt" not in payload or payload["updatedAt"] is None:
            payload["updatedAt"] = datetime.now(timezone.utc)

        quoted_record_id = quote_record_id(record_id)
        query = f"UPSERT {quoted_record_id} CONTENT {_surrealql_literal(payload)};"
        if len(query.encode("utf-8")) > _SQL_UPSERT_LIMIT_BYTES:
            table, _, key = record_id.partition(":")
            response = surreal_rpc(
                "query",
                [
                    "UPSERT type::record($table, $key) CONTENT $payload;",
                    {"table": table, "key": key, "payload": payload},
                ],
                timeout=240,
            )
        else:
            response = surreal_query(query, timeout=120)
        return _raise_for_query_error(response)

    async def query(self, sql: str, params: dict | None = None):
        response = surreal_query(sql, timeout=120)
        return _raise_for_query_error(response)

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


class SurrealPooledConnection:
    """Async Context Manager that uses a pooled SurrealDB connection for better performance.
    
    Recommended for high-concurrency scenarios to reuse connections across requests.
    """

    def __init__(self):
        from fin_pipeline.db.connection_pool import SurrealConnectionPool
        self.pool = SurrealConnectionPool()
        self.db = None

    async def __aenter__(self):
        self.db = await self.pool.get_connection()
        initialize_schema()
        return self.db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            await self.db.close()

