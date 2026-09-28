"""Adding a model field must reach a database that already has the table.

create_all() creates missing tables but never alters an existing one. A field
added to a model whose table already existed therefore never appeared in
production, and every query against that table failed with UndefinedColumn —
while passing locally, because a developer's database was created fresh with
the column already in it. That is how the live site went down on
collection_jobs.b2b_uploaded.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from app.database import Base, _add_missing_columns
import app.models  # noqa: F401 — registers the tables on Base.metadata


@pytest.fixture
def legacy_engine(tmp_path):
    """A database whose collection_jobs predates the newer columns."""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE collection_jobs (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                brand VARCHAR(255),
                season VARCHAR(50),
                status VARCHAR(30)
            )
        """))
        conn.execute(text(
            "INSERT INTO collection_jobs (user_id, brand, season, status) "
            "VALUES (1, 'Carhartt WIP', 'SS27', 'analysed')"))
    return engine


def test_a_new_model_column_is_added_to_an_existing_table(legacy_engine):
    before = {c["name"] for c in inspect(legacy_engine).get_columns("collection_jobs")}
    assert "b2b_uploaded" not in before          # the production situation

    _add_missing_columns(legacy_engine)

    after = {c["name"] for c in inspect(legacy_engine).get_columns("collection_jobs")}
    for column in ("b2b_uploaded", "b2b_uploaded_at", "b2b_note",
                   "parent_job_id", "change_report_json", "report_json"):
        assert column in after, f"{column} was not added"


def test_existing_rows_survive_the_added_columns(legacy_engine):
    _add_missing_columns(legacy_engine)
    with legacy_engine.begin() as conn:
        row = conn.execute(text(
            "SELECT brand, season, b2b_uploaded FROM collection_jobs")).first()
    assert row[0] == "Carhartt WIP" and row[1] == "SS27"
    assert row[2] is None            # nullable; the app supplies the default


def test_running_it_twice_is_harmless(legacy_engine):
    _add_missing_columns(legacy_engine)
    _add_missing_columns(legacy_engine)          # must not raise
    cols = {c["name"] for c in inspect(legacy_engine).get_columns("collection_jobs")}
    assert "b2b_uploaded" in cols


def test_a_table_that_does_not_exist_yet_is_left_to_create_all(legacy_engine):
    """Only existing tables are altered; new ones are created normally."""
    _add_missing_columns(legacy_engine)
    assert "package_runs" not in set(inspect(legacy_engine).get_table_names())


def test_no_model_column_is_missing_after_a_full_setup(tmp_path):
    """The guarantee itself, on a database built the way production is."""
    engine = create_engine(f"sqlite:///{tmp_path / 'full.db'}")
    Base.metadata.create_all(bind=engine)
    _add_missing_columns(engine)

    insp = inspect(engine)
    live = set(insp.get_table_names())
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in live:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns
                    if c.name not in have]
    assert missing == [], f"columns absent from the database: {missing}"
