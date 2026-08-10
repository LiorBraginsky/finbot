"""The drift guard: a model changed without a migration fails this test.

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability — this test fails, rather than skips, when Docker is absent.
"""

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from finbot.repo.models import Base

# Alembic's own comparator excludes "type-bound" CHECK constraints — the ones a
# column type such as Enum(native_enum=False) attaches to itself — from the
# metadata side (alembic.util.sqla_compat.all_table_check_constraints), because
# they are considered part of the type, not something autogenerate should manage.
# It cannot make the same exclusion on the reflected side, since a type-bound
# marker does not survive a round trip through the database. Left unfiltered,
# every Enum(native_enum=False) column reports a permanent, spurious
# "remove_constraint" diff. Excluding these known constraints by name keeps
# the guard meaningful for anything else that changes. `message_status` and
# `extraction_status` (Stage 1) are the identical spurious diff as
# `message_kind` (Stage 0) — same cause, one more Enum(native_enum=False)
# column each.
_TYPE_BOUND_CHECK_CONSTRAINT_NAMES = frozenset(
    {"message_kind", "message_status", "extraction_status"}
)


def _ignore_type_bound_check_constraints(
    object_: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "check_constraint" and name in _TYPE_BOUND_CHECK_CONSTRAINT_NAMES)


async def test_migration_matches_models(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(
                MigrationContext.configure(
                    sync_conn,
                    opts={
                        "compare_type": True,
                        "include_object": _ignore_type_bound_check_constraints,
                    },
                ),
                Base.metadata,
            )
        )
    await engine.dispose()
    assert diff == []
