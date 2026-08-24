"""Unit tests for evals.scoring.load_bank_golden_cases — no network, no
Docker, no real household screenshot: every image here is a tiny synthetic
byte string built in the test, just enough to sniff as a real format.

The ADR-0016 guard itself (repo root, subdirectory, relative path, symlink)
has its own exhaustive case table in tests/unit/test_evals_paths.py, against
`evals.paths.ensure_outside_repo` directly — the tests here only prove the
loader actually wires both `--cases` and `--images-dir` through that guard,
not a second copy of its matrix.
"""

import json
from pathlib import Path

import pytest
from evals.paths import REPO_ROOT, RepoPathError
from evals.scoring import load_bank_golden_cases, to_data_url

from finbot.adapters.telegram.images import to_data_url as _bot_to_data_url
from finbot.core.extraction.ports import ImageFetchError

_JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_NOT_AN_IMAGE = b"this is not an image, just text pretending to be one"


def _case_line(
    *,
    case_id: str = "c1",
    image: str = "c1.jpeg",
    anchor_date: str = "2026-08-24",
    is_transaction_feed: bool = True,
    rows: list[dict] | None = None,
) -> str:
    payload = {
        "id": case_id,
        "image": image,
        "anchor_date": anchor_date,
        "is_transaction_feed": is_transaction_feed,
        "rows": rows
        if rows is not None
        else [
            {
                "kind": "expense",
                "amount": "10.00",
                "category": "groceries",
                "occurred_offset_days": 0,
                "partially_visible": False,
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _write_cases(tmp_path: Path, lines: list[str]) -> Path:
    cases_path = tmp_path / "bank_v1.jsonl"
    cases_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cases_path


# --- ADR-0016's guard, wired through both --cases and --images-dir --------


def test_load_bank_golden_cases_refuses_a_cases_path_inside_the_repo(tmp_path: Path) -> None:
    bad_cases_path = REPO_ROOT / "evals" / "golden" / "bank" / "bank_v1.jsonl"
    with pytest.raises(RepoPathError):
        load_bank_golden_cases(bad_cases_path, images_dir=tmp_path)


def test_load_bank_golden_cases_refuses_an_images_dir_inside_the_repo(tmp_path: Path) -> None:
    cases_path = _write_cases(tmp_path, [_case_line()])
    with pytest.raises(RepoPathError):
        load_bank_golden_cases(cases_path, images_dir=REPO_ROOT / "evals" / "golden" / "bank")


def test_load_bank_golden_cases_accepts_paths_genuinely_outside_the_repo(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.jpeg").write_bytes(_JPEG_BYTES)
    cases_path = _write_cases(tmp_path, [_case_line()])

    cases = load_bank_golden_cases(cases_path, images_dir=images_dir)

    assert len(cases) == 1
    assert cases[0].case_id == "c1"


# --- Case parsing rules -----------------------------------------------------


def test_load_bank_golden_cases_raises_naming_the_file_when_an_image_is_missing(
    tmp_path: Path,
) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    # "c1.jpeg" is deliberately never written.
    cases_path = _write_cases(tmp_path, [_case_line(case_id="c1", image="c1.jpeg")])

    with pytest.raises(FileNotFoundError, match=r"c1\.jpeg"):
        load_bank_golden_cases(cases_path, images_dir=images_dir)


def test_load_bank_golden_cases_raises_on_an_unrecognised_image_format(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.jpeg").write_bytes(_NOT_AN_IMAGE)
    cases_path = _write_cases(tmp_path, [_case_line()])

    with pytest.raises(ImageFetchError):
        load_bank_golden_cases(cases_path, images_dir=images_dir)


def test_load_bank_golden_cases_rejects_a_bare_number_amount(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.jpeg").write_bytes(_JPEG_BYTES)
    bad_row = {
        "kind": "expense",
        "amount": 10.0,  # bare number, never a string — CLAUDE.md rule 2
        "category": "groceries",
        "occurred_offset_days": 0,
        "partially_visible": False,
    }
    cases_path = _write_cases(tmp_path, [_case_line(rows=[bad_row])])

    with pytest.raises(TypeError):
        load_bank_golden_cases(cases_path, images_dir=images_dir)


def test_load_bank_golden_cases_raises_when_anchor_date_is_missing(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.jpeg").write_bytes(_JPEG_BYTES)
    payload = json.loads(_case_line())
    del payload["anchor_date"]
    cases_path = _write_cases(tmp_path, [json.dumps(payload)])

    with pytest.raises(KeyError):
        load_bank_golden_cases(cases_path, images_dir=images_dir)


def test_load_bank_golden_cases_reads_png_too(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.png").write_bytes(_PNG_BYTES)
    cases_path = _write_cases(tmp_path, [_case_line(image="c1.png")])

    cases = load_bank_golden_cases(cases_path, images_dir=images_dir)

    assert cases[0].image_data_url.startswith("data:image/png;base64,")


def test_load_bank_golden_cases_reads_multiple_cases_in_feed_order(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.jpeg").write_bytes(_JPEG_BYTES)
    (images_dir / "c2.jpeg").write_bytes(_JPEG_BYTES)
    cases_path = _write_cases(
        tmp_path,
        [_case_line(case_id="c1", image="c1.jpeg"), _case_line(case_id="c2", image="c2.jpeg")],
    )

    cases = load_bank_golden_cases(cases_path, images_dir=images_dir)

    assert [case.case_id for case in cases] == ["c1", "c2"]


def test_load_bank_golden_cases_parses_rows_including_non_expense_kinds(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "c1.jpeg").write_bytes(_JPEG_BYTES)
    rows = [
        {"kind": "savings", "amount": "6.35", "partially_visible": False},
        {
            "kind": "expense",
            "amount": "193.65",
            "category": "groceries",
            "occurred_offset_days": 0,
            "partially_visible": False,
        },
    ]
    cases_path = _write_cases(tmp_path, [_case_line(rows=rows)])

    cases = load_bank_golden_cases(cases_path, images_dir=images_dir)

    savings_row, expense_row = cases[0].rows
    assert savings_row.kind == "savings"
    assert savings_row.category is None
    assert savings_row.occurred_offset_days is None
    assert expense_row.category == "groceries"
    assert expense_row.occurred_offset_days == 0


# --- Identity pin: the same data-URL builder the bot itself uses ----------


def test_load_bank_golden_cases_reuses_the_bots_own_to_data_url_function() -> None:
    """The hard-to-fool half of the pin: identity, not behaviour — mirrors
    `test_evals_scoring.py::test_scoring_reuses_the_bots_own_convert_to_mp3_
    function`. A future private reimplementation of the data-URL builder
    fails this immediately, even if it behaves identically.
    """
    assert to_data_url is _bot_to_data_url
