"""Downloads a Telegram voice note to memory and converts it to mp3.

The only module in this project that touches aiogram's download API or
shells out to `ffmpeg` — `core/extraction/pipeline.py` calls it only through
the `fetch_audio` callable it is handed, and catches `AudioFetchError`
(`core.extraction.ports`), never anything defined here, which is what keeps
`core` free of this module's imports (CLAUDE.md rule 3).

Always converts, unconditionally — never tries the original OGG/Opus first
and falls back (docs/roadmap.md Stage 2's decision 2). Telegram sends voice
as OGG/Opus; OpenRouter's documented audio-input formats do not include it
(`ogg` there means OGG *Vorbis* — a different codec in the same container,
per ADR-0004's own consequence). One code path costs about 100ms of
`ffmpeg`; two paths would mean provider-format failures that only ever show
up in production, on whichever provider happens to reject Opus-in-Ogg.

Nothing here is written to disk: `bot.download()` streams into an in-memory
`io.BytesIO`, and `ffmpeg` reads that buffer from its stdin and writes mp3 to
its stdout — matching ADR-0009 (media is never archived, on disk or
otherwise) one step earlier than "never archived" strictly requires.
"""

import asyncio

from aiogram import Bot

from finbot.core.extraction.ports import AudioFetchError

# Telegram's own ceiling for a bot's getFile/download (core.telegram.org/
# bots/api#getfile): "the file size must not exceed 20MB". Checked again
# here rather than trusted, since MAX_VOICE_SECONDS bounds duration, not
# bytes, and a malformed or hostile file could claim any duration in its
# metadata while still exceeding Telegram's own limit.
_MAX_AUDIO_BYTES = 20 * 1024 * 1024

_FFMPEG_ARGS = ("-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "mp3", "pipe:1")


async def download_voice(bot: Bot, file_id: str) -> bytes:
    """Downloads `file_id` into memory — never a path, never a temp file —
    and returns the raw bytes Telegram sent (OGG/Opus). Raises
    `AudioFetchError` for anything that keeps those bytes from arriving: a
    failed `getFile`/download call, or a file over `_MAX_AUDIO_BYTES`.
    """
    try:
        buffer = await bot.download(file_id)
    except Exception as exc:
        raise AudioFetchError(
            f"failed to download voice note ({type(exc).__name__}: {exc})"
        ) from exc
    if buffer is None:
        raise AudioFetchError("Telegram returned no file for this voice note")

    # `.read()`, not `.getvalue()`: `Bot.download()` is typed `BinaryIO |
    # None`, and `BinaryIO` (unlike the concrete `io.BytesIO` it actually
    # returns here) has no `.getvalue()` — `.read()` is on the typed
    # interface and, with `download()`'s own `seek=True` default already
    # rewinding to the start, reads exactly the same bytes.
    ogg_bytes = buffer.read()
    if len(ogg_bytes) > _MAX_AUDIO_BYTES:
        raise AudioFetchError(
            f"voice note is {len(ogg_bytes)} bytes, over the {_MAX_AUDIO_BYTES}-byte limit"
        )
    return ogg_bytes


async def convert_to_mp3(
    ogg_bytes: bytes, *, ffmpeg_path: str = "ffmpeg", timeout_seconds: int = 30
) -> bytes:
    """Runs `ffmpeg` with `ogg_bytes` on stdin and mp3 bytes on stdout — no
    temp files. `ffmpeg_path` defaults to resolving `ffmpeg` on `PATH`
    (present in the image; see `infra/Dockerfile`) and exists as a parameter
    only so tests can point it at a fixture executable instead of a real
    binary (this project does not require `ffmpeg` on the machine running
    `pytest`, only inside the built image — see the stage's own `Done when`).

    `timeout_seconds` (`Settings.ffmpeg_timeout_seconds`) is not optional:
    the model call has its own timeout, the download has aiogram's own 30s,
    and without a deadline here `ffmpeg` would be the one external call on
    the drain path that could hang forever — the claimed row would sit in
    `processing` until the container restarts, since `reset_processing`
    only runs at startup (ADR-0013 §5). On expiry the process is killed and
    reaped (never left as a zombie) before raising.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            *_FFMPEG_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise AudioFetchError(f"failed to start ffmpeg ({type(exc).__name__}: {exc})") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=ogg_bytes), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AudioFetchError(f"ffmpeg did not finish within {timeout_seconds}s") from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise AudioFetchError(f"ffmpeg exited {process.returncode}: {detail}")
    if not stdout:
        # A clean exit with nothing on stdout is not success: base64-
        # encoding b"" and sending it as audio would bill a real model call
        # for silence, and the miss would land in the dataset looking like
        # a model failure rather than what it actually was — a conversion
        # that produced nothing.
        raise AudioFetchError("ffmpeg exited 0 but produced no output")
    return stdout


async def fetch_and_convert(
    bot: Bot, file_id: str, *, ffmpeg_path: str = "ffmpeg", timeout_seconds: int = 30
) -> bytes:
    """Downloads and converts one voice note. Everything that can go wrong
    here — a download failure, an oversized file, an ffmpeg failure or
    timeout, empty output — is raised as `AudioFetchError`, and
    `core.extraction.pipeline` treats all of them identically: mark the
    message for retry, never write an `extractions` row, because none of
    them ever reached a model.
    """
    ogg_bytes = await download_voice(bot, file_id)
    return await convert_to_mp3(ogg_bytes, ffmpeg_path=ffmpeg_path, timeout_seconds=timeout_seconds)
