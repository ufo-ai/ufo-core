"""The feature-flag seam: one helper, read through the real OpenFeature SDK, failing to the
default its call site passed.

Core registers no backend, so every case here drives `flag_enabled` against what a deploy actually
has: nothing bound (the SDK's own no-op provider), the sample extension's registered in-memory
backend answering on and off, a backend that decides per workspace, and the three ways a live
backend stops answering — an evaluation that raises, one that outlives the ceiling, and one whose
variations are a shape no read can use. Each of those returns the default the call site passed:
closed where the flag releases something unshipped, open where it withholds something the product
already offers.

Every backend here serves the string variations a flag service holds, because that is the only
shape one can hold: the terraform that creates a flag types its variations `map(string)`. A
provider answering JSON booleans is the case that reads as unreadable, and it has its own test.
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
from ufo.flags import SERVED_FALSE, SERVED_TRUE, flag_enabled, init_flags
from ufo.runtime.workspace import ws

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

    async def resolve_string_details_async(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[str]:
        await asyncio.sleep(60)
        return FlagResolutionDetails(value=SERVED_TRUE)


def _failing_provider() -> InMemoryProvider:
    """A backend whose evaluation raises — an expired token, a malformed response."""

    def evaluate(flag: InMemoryFlag[str], context: EvaluationContext) -> FlagResolutionDetails[str]:
        raise RuntimeError("flag service refused the request")

    return InMemoryProvider(
        {
            FLAG: InMemoryFlag(
                default_variant="off", variants={"off": SERVED_FALSE}, context_evaluator=evaluate
            )
        }
    )


def _per_workspace(on_for: str) -> InMemoryProvider:
    """A backend that answers one workspace's read differently from another's, which is what a
    targeting key is for."""

    def evaluate(flag: InMemoryFlag[str], context: EvaluationContext) -> FlagResolutionDetails[str]:
        served = SERVED_TRUE if context.targeting_key == on_for else SERVED_FALSE
        return FlagResolutionDetails(value=served)

    return InMemoryProvider(
        {
            FLAG: InMemoryFlag(
                default_variant="off", variants={"off": SERVED_FALSE}, context_evaluator=evaluate
            )
        }
    )


async def _check_a_deploy_with_no_backend_reads_every_flag_as_its_call_site_default() -> None:
    """`init_flags(None)` leaves the SDK's no-op provider in place, so a deploy carrying no flag
    service runs on exactly what each call site passes — closed where the caller defaults closed."""
    init_flags(None)
    with ws(uuid4()):
        assert await flag_enabled(FLAG, default=False) is False
        assert await flag_enabled(FLAG, default=True) is True


async def _check_a_backend_holding_no_such_flag_reads_as_the_call_site_default() -> None:
    """The state a deploy is in before an operator creates a flag, and the one a deleted flag leaves
    behind. A caller withholding a shipped screen passes True here, so the screen stands until the
    service answers false — the direction that cannot take a working portal away."""
    init_flags(sample.build_flag_provider(0.0, {}))
    with ws(uuid4()):
        assert await flag_enabled("no-such-flag", default=True) is True
        assert await flag_enabled("no-such-flag", default=False) is False


async def _check_the_selected_backend_answers_on_and_off() -> None:
    """The sample extension's registered backend, bound through the same `init_flags` boot path
    `serve` runs, decides the read rather than the default."""
    init_flags(sample.build_flag_provider(0.0, {}))
    with ws(uuid4()):
        assert await flag_enabled(sample.FLAG_ON, default=False) is True
        assert await flag_enabled(sample.FLAG_OFF, default=True) is False


async def _check_the_read_targets_the_bound_workspace() -> None:
    """The evaluation carries the ambient workspace as its targeting key, which is what lets a
    backend turn a feature on for one workspace while the next one still reads it off."""
    on_workspace = uuid4()
    off_workspace = uuid4()
    init_flags(_per_workspace(str(on_workspace)))
    with ws(on_workspace):
        assert await flag_enabled(FLAG, default=False) is True
    with ws(off_workspace):
        assert await flag_enabled(FLAG, default=False) is False


async def _check_a_backend_that_raises_reads_as_the_default() -> None:
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


async def test_a_backend_answering_a_json_boolean_reads_as_the_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A flag service holds string variations, so a provider handing back `True` is answering
    something no read can use. The helper withholds the feature and names the refusal rather than
    passing an unparsed value off as on — and the SDK answers that refusal instead of raising it,
    so this warning is the only place it surfaces."""
    variants = {"on": True, "off": False}
    init_flags(InMemoryProvider({FLAG: InMemoryFlag(default_variant="on", variants=variants)}))
    with ws(uuid4()), caplog.at_level(logging.WARNING, logger="ufo"):
        assert await flag_enabled(FLAG, default=False) is False
    record = caplog.records[-1]
    assert record.message == "flags.unresolved"
    assert record.ufo["flag"] == FLAG
    assert record.ufo["error_class"] == "TYPE_MISMATCH"


async def test_a_variation_spelled_some_other_way_reads_as_the_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A flag built in the vendor's dashboard takes whatever variations somebody typed, and the
    write verb only refuses the ones it reaches. A string this read cannot map is neither on nor
    off, so it withholds the feature and prints what it was served — the operator's one signal that
    the flag they are toggling decides nothing."""
    variants = {"on": "yes", "off": "no"}
    init_flags(InMemoryProvider({FLAG: InMemoryFlag(default_variant="on", variants=variants)}))
    with ws(uuid4()), caplog.at_level(logging.WARNING, logger="ufo"):
        assert await flag_enabled(FLAG, default=False) is False
    record = caplog.records[-1]
    assert record.message == "flags.unreadable"
    assert record.ufo["flag"] == FLAG
    assert record.ufo["served"] == "yes"


async def test_flag_in_memory_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 5
    for check in checks:
        await check()
