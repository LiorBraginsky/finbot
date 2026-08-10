"""Unit tests for finbot.adapters.telegram.audio — no real ffmpeg, no
network. No Docker required.

`download_voice`/`fetch_and_convert` are exercised against a real `Bot` with
`tests/support/fake_session.py`'s `FakeSession`, the same fake the rest of
the Telegram-adapter suite uses for `Dispatcher.feed_raw_update` — no
sockets opened. `convert_to_mp3` shells out to a real subprocess, but never
to a real `ffmpeg`: this project does not require `ffmpeg` on the machine
running `pytest`, only inside the built image (see the stage's own `Done
when`), so every test here points `ffmpeg_path` at a small fixture
executable instead.
"""

import asyncio
import os
import stat
from pathlib import Path

import pytest
from aiogram import Bot

from finbot.adapters.telegram.audio import convert_to_mp3, download_voice, fetch_and_convert
from finbot.core.extraction.ports import AudioFetchError
from tests.support.fake_session import FakeSession


def _write_fake_ffmpeg(tmp_path: Path, script: str) -> str:
    """A tiny POSIX shell script standing in for `ffmpeg`: real subprocess
    plumbing (stdin -> stdout, exit code), no real audio tool.
    """
    path = tmp_path / "fake-ffmpeg"
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _bot(voice_files: dict[str, bytes]) -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession(voice_files=voice_files))


# --- download_voice ----------------------------------------------------------


async def test_download_voice_returns_the_scripted_bytes() -> None:
    bot = _bot({"voice-1": b"ogg-opus-bytes"})

    downloaded = await download_voice(bot, "voice-1")

    assert downloaded == b"ogg-opus-bytes"


async def test_download_voice_raises_audio_fetch_error_for_an_unknown_file_id() -> None:
    bot = _bot({})

    with pytest.raises(AudioFetchError):
        await download_voice(bot, "no-such-file")


async def test_download_voice_raises_audio_fetch_error_over_the_size_ceiling() -> None:
    bot = _bot({"voice-1": b"x" * (20 * 1024 * 1024 + 1)})

    with pytest.raises(AudioFetchError, match="limit"):
        await download_voice(bot, "voice-1")


# --- convert_to_mp3 ------------------------------------------------------------


async def test_convert_to_mp3_returns_stdout_on_a_clean_exit(tmp_path: Path) -> None:
    ffmpeg_path = _write_fake_ffmpeg(tmp_path, "cat")

    converted = await convert_to_mp3(b"input-bytes", ffmpeg_path=ffmpeg_path)

    assert converted == b"input-bytes"


async def test_convert_to_mp3_raises_audio_fetch_error_on_a_nonzero_exit(tmp_path: Path) -> None:
    ffmpeg_path = _write_fake_ffmpeg(tmp_path, "echo 'boom' >&2; exit 1")

    with pytest.raises(AudioFetchError, match="boom"):
        await convert_to_mp3(b"input-bytes", ffmpeg_path=ffmpeg_path)


async def test_convert_to_mp3_raises_audio_fetch_error_when_ffmpeg_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(AudioFetchError):
        await convert_to_mp3(b"input-bytes", ffmpeg_path=str(tmp_path / "does-not-exist"))


async def test_convert_to_mp3_raises_audio_fetch_error_on_empty_stdout(tmp_path: Path) -> None:
    """A clean exit with nothing on stdout must not read as success: it
    would base64-encode to "" and bill a real model call for silence.
    """
    ffmpeg_path = _write_fake_ffmpeg(tmp_path, "exit 0")

    with pytest.raises(AudioFetchError, match="no output"):
        await convert_to_mp3(b"input-bytes", ffmpeg_path=ffmpeg_path)


async def test_convert_to_mp3_raises_audio_fetch_error_when_ffmpeg_hangs(tmp_path: Path) -> None:
    """Review finding 1: without a deadline, a hung ffmpeg process leaves a
    claimed message in 'processing' forever (ADR-0013 §5) — `reset_
    processing` only runs at startup.

    Runs `convert_to_mp3` in its own `Task` and bounds it with `asyncio.
    wait`, rather than wrapping the call in a second, outer `asyncio.
    wait_for` on this test's own task: nesting two `wait_for`s on the same
    task hits a real asyncio quirk (the outer one can observe a stale
    cancellation left by the inner one and mis-report `TimeoutError` instead
    of the exception `convert_to_mp3` actually raised) that has nothing to
    do with the behaviour under test. A separate `Task` sidesteps it
    entirely, and is also what actually demonstrates the fix: with the
    internal `asyncio.wait_for` removed, this test fails on the `pending`
    check below (the task never finishes) instead of passing quickly —
    verified by temporarily reverting that line during review.

    The fake ffmpeg `exec`s directly into `sleep`, replacing the shell with
    a single process — exactly like a real hung ffmpeg, and unlike `sleep
    5; cat` under an un-exec'd shell, whose *parent* can be killed instantly
    while the grandchild `sleep` keeps running for the OS to clean up in its
    own time, which made a first version of this test flaky on this host.
    """
    ffmpeg_path = _write_fake_ffmpeg(tmp_path, "exec sleep 100")

    task = asyncio.create_task(
        convert_to_mp3(b"input-bytes", ffmpeg_path=ffmpeg_path, timeout_seconds=1)
    )
    started = asyncio.get_running_loop().time()
    _done, pending = await asyncio.wait({task}, timeout=5)
    elapsed = asyncio.get_running_loop().time() - started

    if pending:
        task.cancel()
        pytest.fail(
            "convert_to_mp3 did not return within 5s of a 1s ffmpeg timeout — "
            "it is not enforcing one"
        )
    assert elapsed < 5, (
        "convert_to_mp3 took long enough that it looks like it waited for ffmpeg "
        "to finish naturally (100s) rather than killing it"
    )
    with pytest.raises(AudioFetchError, match="did not finish"):
        task.result()


# --- fetch_and_convert ---------------------------------------------------------


async def test_fetch_and_convert_downloads_then_converts(tmp_path: Path) -> None:
    ffmpeg_path = _write_fake_ffmpeg(tmp_path, "cat")
    bot = _bot({"voice-1": b"ogg-opus-bytes"})

    converted = await fetch_and_convert(bot, "voice-1", ffmpeg_path=ffmpeg_path)

    assert converted == b"ogg-opus-bytes"


async def test_fetch_and_convert_propagates_a_download_failure_without_running_ffmpeg(
    tmp_path: Path,
) -> None:
    # A script that would prove itself ran by touching a marker file — if
    # this test passed with ffmpeg invoked anyway, the marker would exist.
    marker = tmp_path / "ran"
    ffmpeg_path = _write_fake_ffmpeg(tmp_path, f"touch {marker}; cat")
    bot = _bot({})

    with pytest.raises(AudioFetchError):
        await fetch_and_convert(bot, "no-such-file", ffmpeg_path=ffmpeg_path)

    assert not marker.exists()


async def test_fake_ffmpeg_script_is_executable_sanity_check(tmp_path: Path) -> None:
    """Pins the test helper itself: a `_write_fake_ffmpeg` that silently
    produced a non-executable file would make every test above fail with a
    `PermissionError` that has nothing to do with the behaviour under test.
    """
    path = _write_fake_ffmpeg(tmp_path, "cat")
    assert os.access(path, os.X_OK)
