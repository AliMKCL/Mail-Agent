"""
Golden-file parity test.

Replays the exact request list from ``tests/golden/capture.py`` against
``TARGET_APP_IMPORT`` and asserts that ``status``, ``content_type`` and the
normalized ``body`` match the stored goldens byte-for-byte.

Regenerate the goldens with ``uv run python tests/golden/capture.py``. Only do
that if you intend to change recorded behaviour -- the goldens are the arbiter
of behavioural correctness for the microservices split.
"""

from __future__ import annotations

import json

import pytest

from .capture import REQUESTS, golden_path, load_app, normalize, replay

pytestmark = pytest.mark.golden

# WAVE 6: repoint here
TARGET_APP_IMPORT = "backend.app:app"


@pytest.fixture(scope="module")
def replayed() -> dict[str, dict]:
    """Drive the whole ordered request list once; slug -> live record.

    The list is stateful (sign-up, POST /api/users, the OAuth callback and the
    Go-server-down sync all mutate the temp database), so it must be replayed in
    order, in a single harness, exactly as it was captured.
    """
    return replay(load_app(TARGET_APP_IMPORT))


def _load_golden(slug: str) -> dict:
    path = golden_path(slug)
    if not path.exists():
        pytest.fail(
            f"missing golden file {path}\n"
            "run: uv run python tests/golden/capture.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _describe_diff(slug: str, expected, actual) -> str:
    """Name the golden file and show only the parts that actually differ."""
    lines = [f"golden mismatch for {golden_path(slug).name}"]

    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            before = expected.get(key, "<MISSING>")
            after = actual.get(key, "<MISSING>")
            if before != after:
                lines.append(f"  [{key}]")
                lines.append(f"    golden: {before!r}")
                lines.append(f"    live:   {after!r}")
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            lines.append(f"  length: golden={len(expected)} live={len(actual)}")
        for index, (before, after) in enumerate(zip(expected, actual)):
            if before != after:
                lines.append(f"  [{index}]")
                lines.append(_describe_diff(slug, before, after))
    else:
        lines.append(f"  golden: {expected!r}")
        lines.append(f"  live:   {actual!r}")

    return "\n".join(lines)


@pytest.mark.parametrize("spec", REQUESTS, ids=[spec["slug"] for spec in REQUESTS])
def test_golden_parity(spec: dict, replayed: dict[str, dict]) -> None:
    slug = spec["slug"]
    golden = _load_golden(slug)
    live = replayed[slug]
    name = golden_path(slug).name

    assert live["status"] == golden["status"], (
        f"golden mismatch for {name}: status golden={golden['status']} "
        f"live={live['status']}\n"
        f"  golden body: {golden['body']!r}\n"
        f"  live body:   {live['body']!r}"
    )
    assert live["content_type"] == golden["content_type"], (
        f"golden mismatch for {name}: content_type "
        f"golden={golden['content_type']!r} live={live['content_type']!r}"
    )

    expected_body = normalize(golden["body"])
    assert live["body"] == expected_body, _describe_diff(slug, expected_body, live["body"])
