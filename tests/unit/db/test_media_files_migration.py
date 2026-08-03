"""
Migration coverage for the ``media_files`` table.

The unit-test fixtures build their schema with ``Base.metadata.create_all``, so no other
test in the suite runs a migration at all. That leaves the table's real shape — the one a
deployment actually gets — unverified, and two things about it are load-bearing in ways a
reader would not guess: there is deliberately *no* column for the media bytes, and
``referenced_at`` is what keeps the sweeper from deleting media a span still references.

So the migration is driven for real, against a throwaway SQLite file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import phoenix

_REVISION_BEFORE = "c9d0e1f2a3b4"
"""The revision immediately before ``media_files`` exists."""

_REVISION_UNDER_TEST = "e0307b79758d"
"""Creates ``media_files``."""


def _alembic_config() -> Config:
    db_package = Path(phoenix.__file__).parent / "db"
    config = Config(str(db_package / "alembic.ini"))
    config.set_main_option("script_location", str(db_package / "migrations"))
    return config


def _migrate(engine: sa.Engine, revision: str, *, down: bool = False) -> None:
    config = _alembic_config()
    with engine.connect() as connection:
        # `env.py` prefers an injected connection over building its own engine from the
        # environment, which is what lets this run against a temporary file.
        config.attributes["connection"] = connection
        (command.downgrade if down else command.upgrade)(config, revision)


def _columns(connection: sa.Connection) -> dict[str, bool]:
    """Column name to whether it is nullable."""
    return {
        row[1]: not bool(row[3])  # PRAGMA reports notnull as 1 for NOT NULL
        for row in connection.execute(sa.text("PRAGMA table_info(media_files)"))
    }


def _indexes(connection: sa.Connection) -> set[str]:
    return {row[1] for row in connection.execute(sa.text("PRAGMA index_list(media_files)"))}


def _table_exists(connection: sa.Connection) -> bool:
    return bool(
        connection.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_files'")
        ).first()
    )


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[sa.Engine]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'phoenix.db'}")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def migrated(engine: sa.Engine) -> sa.Engine:
    _migrate(engine, _REVISION_UNDER_TEST)
    return engine


class TestMediaFilesTable:
    def test_creates_the_table(self, engine: sa.Engine) -> None:
        _migrate(engine, _REVISION_BEFORE)
        with engine.begin() as connection:
            assert not _table_exists(connection)

        _migrate(engine, _REVISION_UNDER_TEST)
        with engine.begin() as connection:
            assert _table_exists(connection)

    def test_has_exactly_the_expected_columns(self, migrated: sa.Engine) -> None:
        with migrated.begin() as connection:
            assert set(_columns(connection)) == {
                "sha256",
                "media_type",
                "size_bytes",
                "file_name",
                "created_at",
                "referenced_at",
            }

    @pytest.mark.parametrize(
        "column",
        [
            pytest.param("media_type", id="media-type"),
            pytest.param("size_bytes", id="size"),
            pytest.param("created_at", id="created-at"),
        ],
    )
    def test_required_columns_are_not_nullable(self, migrated: sa.Engine, column: str) -> None:
        with migrated.begin() as connection:
            assert _columns(connection)[column] is False

    def test_has_no_column_for_the_bytes(self, migrated: sa.Engine) -> None:
        """
        Media lives in object storage. A column here would be the only large binary in
        the schema, and bytes in the database inflate every backup and snapshot.
        """
        with migrated.begin() as connection:
            assert "content" not in _columns(connection)

    def test_referenced_at_is_nullable(self, migrated: sa.Engine) -> None:
        """NULL means never used, which is the only state the sweeper may reclaim."""
        with migrated.begin() as connection:
            assert _columns(connection)["referenced_at"] is True

    def test_file_name_is_nullable(self, migrated: sa.Engine) -> None:
        """A URL import may not supply one."""
        with migrated.begin() as connection:
            assert _columns(connection)["file_name"] is True

    def test_indexes_created_at_for_the_sweep(self, migrated: sa.Engine) -> None:
        with migrated.begin() as connection:
            assert "ix_media_files_created_at" in _indexes(connection)

    def test_accepts_a_row_with_no_content(self, migrated: sa.Engine) -> None:
        """The shape every upload writes: metadata only, bytes in the store."""
        with migrated.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO media_files (sha256, media_type, size_bytes) "
                    "VALUES (:sha256, 'image/png', 68)"
                ),
                {"sha256": "a" * 64},
            )
            row = connection.execute(
                sa.text("SELECT referenced_at, created_at FROM media_files")
            ).one()
        assert row[0] is None
        # `created_at` has a server default, so an insert that omits it still gets one.
        assert row[1] is not None

    def test_the_digest_is_the_primary_key(self, migrated: sa.Engine) -> None:
        """Re-uploading identical bytes must collide rather than duplicate."""
        with migrated.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO media_files (sha256, media_type, size_bytes) "
                    "VALUES (:sha256, 'image/png', 68)"
                ),
                {"sha256": "a" * 64},
            )
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO media_files (sha256, media_type, size_bytes) "
                        "VALUES (:sha256, 'image/png', 68)"
                    ),
                    {"sha256": "a" * 64},
                )

    def test_downgrade_drops_the_table(self, migrated: sa.Engine) -> None:
        _migrate(migrated, _REVISION_BEFORE, down=True)
        with migrated.begin() as connection:
            assert not _table_exists(connection)
