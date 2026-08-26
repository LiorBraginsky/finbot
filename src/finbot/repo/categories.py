"""Persistence for finbot.repo.models.Category.

Cached per pipeline run, not globally: categories can be added at runtime
(ADR-0021), and a process-lifetime cache would go stale the moment that
happens.

Three statuses, and the difference matters at every call site here:
`active` (a real category, offered in the picker and counted in reports),
`suggested` (proposed by the model, invisible until the owner taps ➕), and
`merged` (superseded — reserved for Stage 5's merge flow, unused today).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.categories.slugify import slugify_category
from finbot.repo.models import Category

STATUS_ACTIVE = "active"
STATUS_SUGGESTED = "suggested"

# A user-created category has no emoji of its own to inherit; `other`'s is
# the honest placeholder, and the same one the fallback category already uses.
_DEFAULT_EMOJI = "🗂"


@dataclass(frozen=True)
class CategoryView:
    """A category as the adapter needs it: enough to render a row and a
    button, and nothing more. Returned instead of the ORM object so a
    detached-instance error cannot reach the renderer once the session is
    closed.
    """

    id: int
    slug: str
    label: str
    emoji: str
    status: str


def _view(category: Category) -> CategoryView:
    return CategoryView(
        id=category.id,
        slug=category.name,
        label=category.label,
        emoji=category.emoji,
        status=category.status,
    )


async def all_active(session: AsyncSession) -> list[Category]:
    """All categories with status='active', in no particular order."""
    result = await session.execute(select(Category).where(Category.status == STATUS_ACTIVE))
    return list(result.scalars().all())


async def by_slug(session: AsyncSession) -> dict[str, int]:
    """Map of active category slug (`name`) -> id.

    Deliberately `active` only: a suggested category must not be reachable
    through the map the pipeline writes expenses with, or a proposal would
    silently become a real filing before anyone approved it.
    """
    categories = await all_active(session)
    return {category.name: category.id for category in categories}


async def views_by_id(session: AsyncSession) -> dict[int, CategoryView]:
    """Every category, of every status, keyed by id — what a renderer needs
    to label a row.

    Not filtered to `active`: an expense can point at a category that was
    since merged away, and rendering it as a `KeyError` would be worse than
    rendering a stale label.
    """
    result = await session.execute(select(Category))
    return {category.id: _view(category) for category in result.scalars().all()}


async def active_views(session: AsyncSession) -> list[CategoryView]:
    """Active categories for the ✏️ picker, system ones first in seeded
    (`id`) order, then owner-created ones by id.

    `is_system DESC, id` rather than alphabetically: the thirteen have a
    deliberate reading order (`other` last — `catalog.CATALOG`'s own
    docstring), and re-sorting them by label would scatter it. New categories
    append at the end, where a keyboard's shape stays predictable.
    """
    result = await session.execute(
        select(Category)
        .where(Category.status == STATUS_ACTIVE)
        .order_by(Category.is_system.desc(), Category.id)
    )
    return [_view(category) for category in result.scalars().all()]


async def get_view(session: AsyncSession, category_id: int) -> CategoryView | None:
    category = await session.get(Category, category_id)
    return None if category is None else _view(category)


async def resolve_suggestion(session: AsyncSession, label: str) -> tuple[CategoryView, bool] | None:
    """Turn a model-proposed label into a category row, without committing.

    Returns `(view, needs_approval)`, or `None` when the label slugifies onto
    a category the model was already free to choose — a proposal that is a
    reworded `groceries` is noise, not a new category, and must not create a
    row (ADR-0021's own "do not propose a rewording" rule, enforced here
    rather than trusted to the prompt).

    Three outcomes, in the order they are checked:

    1. **An `active` row already exists** for this slug — the owner approved
       this category before, so `needs_approval` is `False` and the caller
       files the expense under it directly. This is the reuse path, and it is
       why the slug has to be deterministic (`slugify_category`).
    2. **A `suggested` row already exists** — the model has proposed this
       before and nobody has approved it. The same row is returned rather
       than a second one, so ten Preply charges produce one ➕ button, not
       ten identical categories.
    3. **Nothing exists** — a `suggested` row is inserted. It is invisible
       everywhere (`by_slug`, `active_views`, reports) until approved.

    Flushed, not committed: the caller owns the transaction, exactly like
    every other function in `repo/`.
    """
    slug = slugify_category(label)
    existing = (
        await session.execute(select(Category).where(Category.name == slug))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.is_system:
            # Case 0, not in the list above because it produces no row: a
            # proposal that lands on one of the seeded slugs is a rewording
            # of a category the model could have picked outright.
            return None
        return _view(existing), existing.status != STATUS_ACTIVE

    created = Category(
        name=slug,
        label=label,
        emoji=_DEFAULT_EMOJI,
        is_system=False,
        status=STATUS_SUGGESTED,
    )
    session.add(created)
    await session.flush()
    return _view(created), True


async def approve(session: AsyncSession, category_id: int, *, created_by: int) -> bool:
    """Flip a `suggested` category to `active`. Returns whether it changed.

    `False` for a category that is already active — which is what a
    redelivered ➕ tap produces, and a no-op success rather than a failure.
    `created_by` is `users.id`, the internal PK, never a Telegram id.
    """
    category = await session.get(Category, category_id)
    if category is None or category.status == STATUS_ACTIVE:
        return False
    category.status = STATUS_ACTIVE
    category.created_by = created_by
    await session.flush()
    return True
