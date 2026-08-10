"""The exception that withholds the polling offset — ADR-0013.

`PersistenceError` is raised **only** by `PersistMessageMiddleware`, and only
around the durable write that must land before an update is acknowledged.
`polling.run_polling` treats it as the single exception class that must abort
the batch and leave the offset untouched; every other failure — a handler
bug, a `sendMessage` error, a callback's own body raising — is logged and
swallowed, because one broken reply must not wedge the household's bot
forever (docs/plans/stage-1-text-to-expense.md, Approach A).
"""


class PersistenceError(Exception):
    """The durable write in `PersistMessageMiddleware` failed."""
