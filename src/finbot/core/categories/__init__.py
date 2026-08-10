"""The thirteen expense categories: see `catalog.py` for the single source
of truth.
"""

from finbot.core.categories.catalog import CATALOG, FALLBACK_SLUG, SLUGS, CategorySpec

__all__ = ["CATALOG", "FALLBACK_SLUG", "SLUGS", "CategorySpec"]
