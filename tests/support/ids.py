"""Deterministic `telegram_update_id`s for integration tests.

Every integration helper used to derive one with `hash(...)`, which is a
latent intermittent gate: Python randomises `str` and `tuple`-of-`str` hashing
per process (`PYTHONHASHSEED`), so the set of update ids a test run uses is
different every run. Two tests whose ids collide make the second
`messages.add_if_new` return `None` — the redelivered-update path — and the
failure appears on one run in however many, then vanishes on rerun. That is
exactly the shape `.claude/orchestration.md` forbids: "an intermittent gate
trains the habit that destroys it."

`crc32` is not a better hash. It is a *stable* one, which is the whole point:
a collision under this function fails on every run, gets noticed, and gets
fixed by changing one test's key. A collision under `hash()` cannot be
reproduced.
"""

from zlib import crc32

# `messages.telegram_update_id` is a BigInteger and real Telegram ids are
# small positive integers; crc32 is already bounded to 32 bits, so no masking
# is needed beyond what it guarantees.
_OFFSET = 1_000_000


def stable_update_id(*parts: object) -> int:
    """A deterministic, positive, test-unique update id derived from `parts`.

    `repr` rather than `str`: it distinguishes `("a", 1)` from `("a1",)` and
    `Decimal("50.00")` from `50.0`, which matters because several helpers key
    on an amount.

    The offset keeps these clear of the small literal ids some tests use
    directly (`1001`, `2001`, ...), so a hand-written id can never collide
    with a derived one.
    """
    return _OFFSET + crc32(repr(parts).encode("utf-8"))
