"""Async engine / session factory."""

import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def create_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        # The OpenRouter response body is parsed once, with
        # finbot.core.money.loads_decimal (parse_float=Decimal), and that same
        # object is what gets written into extractions.raw_response /
        # corrections.before/after — one parse, one truth. Parsing it a second
        # time here for JSONB storage would create a second copy and an
        # unanswerable question about which one is authoritative. default=str
        # renders the Decimals it contains as JSON strings: lossless as text,
        # and still queryable inside jsonb.
        json_serializer=lambda value: json.dumps(value, default=str, ensure_ascii=False),
    )
    # expire_on_commit=False: with async sessions, a post-commit lazy refresh
    # raises instead of silently issuing SQL, so keep committed objects readable.
    return async_sessionmaker(engine, expire_on_commit=False)
