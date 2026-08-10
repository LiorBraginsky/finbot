"""Period-boundary math for `/day` `/week` `/month`: pure, no SQL, no clock.

`today` is a parameter, never `datetime.now()` read from inside this module —
the same rule the plan's 'Relative dates' decision applies to extraction, and
for the same reason: it is what makes date tests deterministic.
"""

from datetime import date, timedelta
from typing import Literal, assert_never

Period = Literal["day", "week", "month"]


def resolve(period: Period, today: date) -> tuple[date, date]:
    """The `[date_from, date_to]` window for `period`, inclusive of `today`."""
    if period == "day":
        return today, today
    if period == "week":
        monday = today - timedelta(days=today.weekday())
        return monday, today
    if period == "month":
        return today.replace(day=1), today
    # `Period` is a closed Literal; the branches above are exhaustive by
    # construction. `assert_never` is the idiom mypy's --strict
    # `warn_unreachable` expects here — a plain `raise ValueError` on this
    # line reads as dead code to mypy and fails the gate outright, but a
    # caller that ignores the type checker (e.g. handlers.py's own
    # `cast(Period, command.command)`) still gets a loud runtime failure.
    assert_never(period)
