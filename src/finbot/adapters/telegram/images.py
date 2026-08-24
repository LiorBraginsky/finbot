"""Downloads a Telegram photo to memory and turns it into a data URL.

The only module in this project that touches aiogram's download API for a
photo — `core/extraction/pipeline.py` calls it only through the `fetch_image`
callable it is handed, and catches `ImageFetchError`
(`core.extraction.ports`), never anything defined here, which is what keeps
`core` free of this module's imports (CLAUDE.md rule 3). Parallel to
`audio.py` in every respect but the encoding at the end: audio ships as a
base64 string inside an `input_audio` content part, an image ships as the
OpenAI-style `image_url` content part the Stage 2.5 spike measured working —
`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}` —
so the seam here produces that whole `data:` URL, not just raw bytes.

**No resizing, no new dependency.** A bank-feed screenshot is already small
enough at Telegram's own compression, and Pillow would be the first image
library this project has ever needed.

Nothing here is written to disk: `bot.download()` streams into an in-memory
`io.BytesIO` and the bytes never touch a temp file — matching ADR-0009
(media is never archived, on disk or otherwise).
"""

import base64

from aiogram import Bot

from finbot.core.extraction.ports import ImageFetchError

# Telegram's own ceiling for a bot's getFile/download (core.telegram.org/
# bots/api#getfile): "the file size must not exceed 20MB". Checked again
# here rather than trusted, mirroring audio.py's own `_MAX_AUDIO_BYTES` for
# the same reason: nothing about a photo's own metadata guarantees this.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_RIFF_MAGIC = b"RIFF"
_WEBP_MAGIC = b"WEBP"


async def download_photo(bot: Bot, file_id: str) -> bytes:
    """Downloads `file_id` into memory — never a path, never a temp file —
    and returns the raw bytes Telegram sent. Raises `ImageFetchError` for
    anything that keeps those bytes from arriving: a failed
    `getFile`/download call, or a file over `_MAX_IMAGE_BYTES`. Mirrors
    `audio.download_voice` exactly.
    """
    try:
        buffer = await bot.download(file_id)
    except Exception as exc:
        raise ImageFetchError(f"failed to download photo ({type(exc).__name__}: {exc})") from exc
    if buffer is None:
        raise ImageFetchError("Telegram returned no file for this photo")

    # `.read()`, not `.getvalue()`: see audio.download_voice's own note —
    # `Bot.download()` is typed `BinaryIO | None`, and only `.read()` is on
    # that typed interface.
    image_bytes = buffer.read()
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise ImageFetchError(
            f"photo is {len(image_bytes)} bytes, over the {_MAX_IMAGE_BYTES}-byte limit"
        )
    return image_bytes


def sniff_mime(data: bytes) -> str:
    """The magic-bytes check for the three formats Telegram photos and
    OpenRouter's `image_url` both support: JPEG (`FF D8 FF`), PNG
    (`89 50 4E 47 0D 0A 1A 0A`), WebP (`RIFF....WEBP`). Anything else raises
    `ImageFetchError` — never guessed, never passed through unchecked.
    """
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data[:4] == _RIFF_MAGIC and data[8:12] == _WEBP_MAGIC:
        return "image/webp"
    raise ImageFetchError("data does not sniff as a supported image format (jpeg/png/webp)")


def to_data_url(data: bytes) -> str:
    """`sniff_mime(data)` plus a base64 encoding, combined into the exact
    `data:<mime>;base64,<...>` string `bank.build_request`'s `image_url`
    content part expects.
    """
    mime = sniff_mime(data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def fetch_as_data_url(bot: Bot, file_id: str) -> str:
    """Downloads and encodes one photo. Everything that can go wrong here —
    a download failure, an oversized file, an unrecognised format — is
    raised as `ImageFetchError`, and `core.extraction.pipeline` treats all of
    them identically: mark the message for retry, never write an
    `extractions` row, because none of them ever reached a model.
    """
    data = await download_photo(bot, file_id)
    return to_data_url(data)
