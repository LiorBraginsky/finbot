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

`CATALOG` is the model-choosable set. `DERIVED_CATALOG` below adds the
categories only the code assigns, and `ALL_CATEGORIES` is what the database
actually holds — see `DERIVED_CATALOG`'s own comment for the distinction.
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

# Categories the **code** assigns, never the model: a bank-feed row whose
# `kind` is `cash_withdrawal` or `transfer_out` is filed here by
# `bank.plan_writes`, from the row's kind alone (ADR-0020). They are
# deliberately absent from `CATALOG` — and therefore from the prompt and from
# the schema's `category` enum — because the model must not be able to file a
# supermarket purchase as "cash": the whole point of these two is that they
# mean "the money left the account and the feed does not say where it went",
# which is a property of the row's kind, not of its merchant.
#
# They are real `categories` rows all the same (seeded by migration 0005), so
# a report groups by them like any other and ✏️ can move a row out of them
# once the owner remembers where the money actually went.
DERIVED_CATALOG: Final[tuple[CategorySpec, ...]] = (
    CategorySpec("cash", "💵", "знята готівка — витрачена, але невідомо на що"),
    CategorySpec("transfers", "↔️", "переказ комусь іншому — куди пішло, стрічка не каже"),
)

# Every category that exists in the database. `CATALOG` is the subset the
# model may choose from; this is the set anything downstream of the model —
# validation, labels, reports — has to cover.
ALL_CATEGORIES: Final[tuple[CategorySpec, ...]] = CATALOG + DERIVED_CATALOG

SLUGS: Final[frozenset[str]] = frozenset(c.slug for c in ALL_CATEGORIES)
MODEL_SLUGS: Final[frozenset[str]] = frozenset(c.slug for c in CATALOG)
FALLBACK_SLUG: Final[str] = "other"

# Explicit raises, not bare `assert`: `python -O` strips assert statements,
# which would silently disable this invariant in exactly the build that runs
# in production.
if FALLBACK_SLUG not in MODEL_SLUGS:
    msg = "FALLBACK_SLUG must be one of the slugs the model may choose"
    raise AssertionError(msg)
if len(SLUGS) != len(ALL_CATEGORIES):
    msg = "catalog slugs must be unique"
    raise AssertionError(msg)
# A slug in both halves would make "the model may choose this" and "only the
# code assigns this" true of the same category at once — the distinction
# DERIVED_CATALOG exists to draw.
if MODEL_SLUGS & {c.slug for c in DERIVED_CATALOG}:
    msg = "a slug cannot be both model-choosable and code-assigned"
    raise AssertionError(msg)
