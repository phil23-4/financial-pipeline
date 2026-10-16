from unittest.mock import AsyncMock, patch

import pytest

from fin_pipeline.db import db as database
from fin_pipeline.db import relations
from fin_pipeline.db.db import _build_schema_sql


def test_company_key_normalizes_name_case_and_whitespace():
    assert relations._company_key("tlv", " Banca   Transilvania ") == (
        relations._company_key("TLV", "banca transilvania")
    )


def test_company_key_distinguishes_companies_with_same_ticker():
    assert relations._company_key("ABC", "Acme Corporation") != (
        relations._company_key("ABC", "Another Company")
    )


def test_company_schema_allows_duplicate_tickers_but_requires_unique_company_keys():
    schema = _build_schema_sql()

    assert "idx_company_ticker_lookup ON TABLE company COLUMNS ticker;" in schema
    assert "idx_company_key ON TABLE company COLUMNS companyKey UNIQUE;" in schema
    assert "idx_company_ticker ON TABLE company COLUMNS ticker UNIQUE;" not in schema


def test_schema_initialization_removes_the_old_unique_ticker_index(monkeypatch):
    monkeypatch.setattr(database, "COMPANY_TABLE", "")

    with patch("fin_pipeline.db.db.surreal_query", return_value=[]) as query:
        assert database.initialize_schema() is True

    assert any(
        call.args[0] == "REMOVE INDEX IF EXISTS idx_company_ticker ON TABLE company;"
        for call in query.call_args_list
    )


@pytest.mark.asyncio
async def test_same_ticker_different_names_create_distinct_company_nodes(monkeypatch):
    monkeypatch.setattr(relations, "COMPANY_TABLE", "company")
    db = AsyncMock()
    db.query.side_effect = [
        [{"result": []}],
        [{"result": []}],
        [{"result": []}],
        [{"result": []}],
    ]

    with patch(
        "fin_pipeline.db.relations.surreal_query",
        return_value=[{"status": "OK", "result": []}],
    ) as write_query:
        first_id = await relations.ensure_company_exists(db, "ABC", "Acme Corporation")
        second_id = await relations.ensure_company_exists(db, "ABC", "Another Company")

    assert first_id != second_id
    assert write_query.call_count == 2


@pytest.mark.asyncio
async def test_company_without_name_is_not_merged_by_ticker(monkeypatch):
    monkeypatch.setattr(relations, "COMPANY_TABLE", "company")
    db = AsyncMock()

    with patch("fin_pipeline.db.relations.surreal_query") as write_query:
        company_id = await relations.ensure_company_exists(db, "ABC")

    assert company_id is None
    db.query.assert_not_awaited()
    write_query.assert_not_called()


@pytest.mark.asyncio
async def test_same_name_company_reuses_existing_key(monkeypatch):
    monkeypatch.setattr(relations, "COMPANY_TABLE", "company")
    db = AsyncMock()
    db.query.return_value = [{"result": [{"id": "company:known"}]}]

    company_id = await relations.ensure_company_exists(
        db, "ABC", "Acme Corporation"
    )

    assert company_id == "company:known"
    db.query.assert_awaited_once()


@pytest.mark.asyncio
async def test_matching_legacy_company_is_assigned_company_key(monkeypatch):
    monkeypatch.setattr(relations, "COMPANY_TABLE", "company")
    db = AsyncMock()
    db.query.side_effect = [
        [{"result": []}],
        [{"result": [{"id": "company:ABC", "companyName": "  ACME   Corporation "}]}],
    ]

    with patch(
        "fin_pipeline.db.relations.surreal_query",
        return_value=[{"status": "OK", "result": []}],
    ) as write_query:
        company_id = await relations.ensure_company_exists(
            db, "ABC", "Acme Corporation"
        )

    assert company_id == "company:ABC"
    assert "companyKey" in write_query.call_args.args[0]
