"""Command handlers. Persistence already happened in middleware by the time
these run — see middlewares.py.

`build_router` is a factory, not a module-level singleton: an aiogram
`Router` can only ever be attached to one `Dispatcher` (attaching it twice
raises `RuntimeError`), and `build_dispatcher` (see main.py) is called once
per test as well as once per process — each call needs its own instance.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message


def build_router() -> Router:
    router = Router(name="commands")

    @router.message(Command("ping"))
    async def ping(message: Message) -> None:
        await message.answer("pong")

    return router
