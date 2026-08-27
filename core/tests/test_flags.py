"""The feature-flag seam: one helper, read through the real OpenFeature SDK, failing closed.

Core registers no backend, so every case here drives `flag_enabled` against what a deploy actually
has: nothing bound (the SDK's own no-op provider), the sample extension's registered in-memory
backend answering on and off, a backend that decides per workspace, and the two ways a live backend
stops answering — an evaluation that raises and one that outlives the ceiling. Each of those returns
the default the call site passed, which is the closed state a flagged feature ships in.
"""

import asyncio
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
import ufo_ext_sample as sample
from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider

from ufo import flags
from ufo.flags import flag_enabled, init_flags
from ufo.workspace import ws

FLAG = "probe-flag"


@pytest.fixture(autouse=True)
def unbound_provider() -> Iterator[None]:
    """Each test starts and leaves the process-wide OpenFeature API with no provider bound, so one
    test's backend never answers the next one's read."""
    api.clear_providers()
    yield
    api.clear_providers()


class _SlowProvider(InMemoryProvider):
    """A backend that never answers inside the ceiling: it awaits far longer than a read waits."""

    def __init__(self) -> None:
        super().__init__({})

    async def resolve_boolean_details_async(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[bool]:
        await asyncio.sleep(60)
        return FlagResolutionDetails(value=True)


def _failing_provider() -> InMemoryProvider:
    """A backend whose evaluation raises — an expired token, a malformed response."""

    def evaluate(
        flag: InMemoryFlag[bool], context: EvaluationContext
    ) -> FlagResolutionDetails[bool]:
        raise RuntimeError("flag service refused the request")

    return InMemoryProvider(
        {
            FLAG: InMemoryFlag(
                default_variant="off", variants={"off": False}, context_evaluator=evaluate
            )
        }
    )


def _per_workspace(on_for: str) -> InMemoryProvider:
    """A backend that answers one workspace's read differently from another's, which is what a
    targeting key is for."""

    def evaluate(
        flag: InMemoryFlag[bool], context: EvaluationContext
    ) -> FlagResolutionDetails[bool]:
        return FlagResolutionDetails(value=context.targeting_key == on_for)

    return InMemoryProvider(
        {
            FLAG: InMemoryFlag(
                default_variant="off", variants={"off": False}, context_evaluator=evaluate
            )
        }
    )


async def test_a_deploy_with_no_backend_reads_every_flag_as_its_call_site_default() -> None:
    """`init_flags(None)` leaves the SDK's no-op provider in place, so a deploy carrying no flag
    service runs on exactly what each call site passes — closed where the caller defaults closed."""
    init_flags(None)
    with ws(uuid4()):
        assert await flag_enabled(FLAG, default=False) is False
        assert await flag_enabled(FLAG, default=True) is True


async def test_the_selected_backend_answers_on_and_off() -> None:
    """The sample extension's registered backend, bound through the same `init_flags` boot path
    `serve` runs, decides the read rather than the default."""
    init_flags(sample.build_flag_provider(0.0))
    with ws(uuid4()):
        assert await flag_enabled(sample.FLAG_ON, default=False) is True
        assert await flag_enabled(sample.FLAG_OFF, default=True) is False


async def test_the_read_targets_the_bound_workspace() -> None:
    """The evaluation carries the ambient workspace as its targeting key, which is what lets a
    backend turn a feature on for one workspace while the next one still reads it off."""
    on_workspace = uuid4()
    off_workspace = uuid4()
    init_flags(_per_workspace(str(on_workspace)))
    with ws(on_workspace):
        assert await flag_enabled(FLAG, default=False) is True
    with ws(off_workspace):
        assert await flag_enabled(FLAG, default=False) is False


async def test_a_backend_that_raises_reads_as_the_default() -> None:
    """A live backend that fails mid-evaluation withholds the feature rather than failing the turn
    that read the flag."""
    init_flags(_failing_provider())
    with ws(uuid4()):
        assert await flag_enabled(FLAG, default=False) is False
        assert await flag_enabled(FLAG, default=True) is True


async def test_a_backend_that_outlives_the_ceiling_reads_as_the_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The bound wait is the point: a flag service that stops answering costs a turn
    `FLAG_TIMEOUT_SECONDS`, never the turn itself, and the operator gets one warning naming the
    flag."""
    monkeypatch.setattr(flags, "FLAG_TIMEOUT_SECONDS", 0.01)
    init_flags(_SlowProvider())
    with ws(uuid4()), caplog.at_level(logging.WARNING, logger="ufo"):
        assert await flag_enabled(FLAG, default=False) is False
    record = caplog.records[-1]
    assert record.message == "flags.unresolved"
    assert record.ufo["flag"] == FLAG
    assert record.ufo["default"] is False
