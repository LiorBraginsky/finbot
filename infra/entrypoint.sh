#!/bin/sh
set -e
alembic upgrade head
exec python -m finbot.adapters.telegram.main
