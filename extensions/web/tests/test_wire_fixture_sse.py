"""The SSE fixture is generated from the real projection and checked in where the frontend's
vitest suite replays it, so a projection change is a fixture diff that runs the browser tests."""

from typing import get_args

from ufo_testsupport.sse_fixture import SSE_FIXTURE_PATH, rendered_sse, sse_frames

from ufo.runtime.hub import ArtifactsChanged, LiveFrame


def test_sse_fixture_is_fresh() -> None:
    assert SSE_FIXTURE_PATH.read_text() == rendered_sse(), (
        "stale sse fixture; regenerate: uv run python -m ufo_testsupport.sse_fixture"
    )


def test_sse_fixture_carries_every_live_frame_kind() -> None:
    assert set(sse_frames()) | {ArtifactsChanged} == set(get_args(LiveFrame))
