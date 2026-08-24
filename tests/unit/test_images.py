"""Unit tests for finbot.adapters.telegram.images — no network. No Docker
required.

`download_photo`/`fetch_as_data_url` are exercised against a real `Bot` with
`tests/support/fake_session.py`'s `FakeSession`, exactly like
`tests/unit/test_audio.py` does for voice — `_voice_files` is a generic
`file_id -> bytes` map (see `FakeSession`'s own docstring) that serves a
photo download unchanged.
"""

import base64

import pytest
from aiogram import Bot

from finbot.adapters.telegram.images import (
    download_photo,
    fetch_as_data_url,
    sniff_mime,
    to_data_url,
)
from finbot.core.extraction.ports import ImageFetchError
from tests.support.fake_session import FakeSession

_JPEG_BYTES = b"\xff\xd8\xff" + b"rest-of-a-jpeg"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"rest-of-a-png"
_WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"rest-of-a-webp"
_UNKNOWN_BYTES = b"not-an-image-at-all"


def _bot(photo_files: dict[str, bytes]) -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession(voice_files=photo_files))


# --- sniff_mime ----------------------------------------------------------


def test_sniff_mime_recognises_jpeg() -> None:
    assert sniff_mime(_JPEG_BYTES) == "image/jpeg"


def test_sniff_mime_recognises_png() -> None:
    assert sniff_mime(_PNG_BYTES) == "image/png"


def test_sniff_mime_recognises_webp() -> None:
    assert sniff_mime(_WEBP_BYTES) == "image/webp"


def test_sniff_mime_raises_image_fetch_error_for_an_unknown_format() -> None:
    with pytest.raises(ImageFetchError, match="supported image format"):
        sniff_mime(_UNKNOWN_BYTES)


# --- to_data_url -----------------------------------------------------------


def test_to_data_url_builds_the_expected_prefix_and_payload() -> None:
    url = to_data_url(_JPEG_BYTES)
    assert url == f"data:image/jpeg;base64,{base64.b64encode(_JPEG_BYTES).decode('ascii')}"


def test_to_data_url_raises_image_fetch_error_for_an_unknown_format() -> None:
    with pytest.raises(ImageFetchError):
        to_data_url(_UNKNOWN_BYTES)


# --- download_photo ----------------------------------------------------------


async def test_download_photo_returns_the_scripted_bytes() -> None:
    bot = _bot({"photo-1": _JPEG_BYTES})

    downloaded = await download_photo(bot, "photo-1")

    assert downloaded == _JPEG_BYTES


async def test_download_photo_raises_image_fetch_error_for_an_unknown_file_id() -> None:
    bot = _bot({})

    with pytest.raises(ImageFetchError):
        await download_photo(bot, "no-such-file")


async def test_download_photo_raises_image_fetch_error_over_the_size_ceiling() -> None:
    bot = _bot({"photo-1": b"x" * (20 * 1024 * 1024 + 1)})

    with pytest.raises(ImageFetchError, match="limit"):
        await download_photo(bot, "photo-1")


async def test_download_photo_raises_image_fetch_error_when_bot_download_returns_none() -> None:
    """`Bot.download()` is typed `BinaryIO | None` — a `None` at that seam
    must not reach `.read()` and raise `AttributeError` instead of the
    project's own exception type.
    """

    class _NoneReturningBot(Bot):
        async def download(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
            return None

    none_bot = _NoneReturningBot(token="42:TESTTOKEN", session=FakeSession())
    with pytest.raises(ImageFetchError, match="no file"):
        await download_photo(none_bot, "photo-1")


# --- fetch_as_data_url ---------------------------------------------------------


async def test_fetch_as_data_url_downloads_then_encodes() -> None:
    bot = _bot({"photo-1": _PNG_BYTES})

    url = await fetch_as_data_url(bot, "photo-1")

    assert url == f"data:image/png;base64,{base64.b64encode(_PNG_BYTES).decode('ascii')}"


async def test_fetch_as_data_url_raises_image_fetch_error_for_an_unrecognised_format() -> None:
    bot = _bot({"photo-1": _UNKNOWN_BYTES})

    with pytest.raises(ImageFetchError):
        await fetch_as_data_url(bot, "photo-1")
