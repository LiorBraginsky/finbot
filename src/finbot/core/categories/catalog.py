"""The thirteen categories: the single source of truth.

Slug is the stable identifier used in the prompt, the JSON-Schema `enum`,
`evals/golden/` and reports. The Ukrainian label is presentation and lives in
the adapter (Stage 1 Step 3's `render.py`); `description` steers the model and
lives in the prompt (`docs/plans/stage-1-text-to-expense.md` → Decisions
taken).

The migration (`migrations/versions/0002_stage1_expenses.py`) spells the
thirteen literally — migrations never import `finbot` — and
`tests/integration/test_categories_seed.py` asserts the seeded rows equal
`CATALOG`, so the two cannot drift.

`other` stays last in catalog order: the schema `enum` follows catalog order,
and the fallback reading last is the one thing a reader should be able to
rely on.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class CategorySpec:
    slug: str
    emoji: str
    description: str


CATALOG: Final[tuple[CategorySpec, ...]] = (
    CategorySpec("groceries", "🛒", "супермаркет, ринок, вода, продукти додому"),
    CategorySpec("dining_out", "🍽", "кава, ресторан, доставка їжі, бізнес-ланч"),
    CategorySpec("transport", "🚕", "таксі, метро, паливо, парковка, СТО, квитки"),
    CategorySpec("housing", "🏠", "оренда, комуналка, інтернет, ОСББ, ремонт"),
    CategorySpec("health", "💊", "аптека, лікар, аналізи, стоматолог, оптика"),
    CategorySpec("household", "🧴", "побутова хімія, гігієна, посуд, меблі, інструменти"),
    CategorySpec("clothing", "👕", "одяг, взуття, аксесуари, хімчистка, ремонт взуття"),
    CategorySpec("entertainment", "🎬", "кіно, концерти, книги, ігри, спорт, хобі, подорожі"),
    CategorySpec("subscriptions", "📱", "мобільний, стримінг, софт, хмара, абонементи"),
    CategorySpec("gifts", "🎁", "подарунки, донати на ЗСУ, благодійність"),
    CategorySpec("pets", "🐾", "корм, ветеринар, грумінг"),
    CategorySpec("hookah", "💨", "кальянна, тютюн, вугілля, обслуговування"),
    CategorySpec(
        "other", "🗂", "усе, що не підходить вище — обовʼязковий fallback, не вигадувати нове"
    ),
)

SLUGS: Final[frozenset[str]] = frozenset(c.slug for c in CATALOG)
FALLBACK_SLUG: Final[str] = "other"

# Explicit raises, not bare `assert`: `python -O` strips assert statements,
# which would silently disable this invariant in exactly the build that runs
# in production.
if FALLBACK_SLUG not in SLUGS:
    msg = "FALLBACK_SLUG must be one of the catalog's own slugs"
    raise AssertionError(msg)
if len(SLUGS) != len(CATALOG):
    msg = "catalog slugs must be unique"
    raise AssertionError(msg)
