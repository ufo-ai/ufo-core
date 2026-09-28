"""Feature flags: one OpenFeature client, read through one helper, failing closed to the default.

Core owns the seam and no backend. A deploy selects one an extension registers at the
`flag_providers` Manifest point (`[flags] backend`), and `init_flags` binds it to the process-wide
OpenFeature API at boot — so `flag_enabled` is the only place in the tree that touches the SDK and
swapping providers is a config line.

A flag is read as a string, because a string is what a flag service holds, so the deploy's flags
serve `SERVED_TRUE` and `SERVED_FALSE` rather than JSON booleans. Every backend answers in those
two spellings; anything else is an answer no read can use.

`flag_enabled` fails closed to the value the call site passes. A deploy that selects no backend
resolves every flag through OpenFeature's own no-op provider; an evaluation that raises, errors,
answers neither spelling, or outlives `FLAG_TIMEOUT_SECONDS` returns the same default and warns. A
flag decides whether a feature is offered, so a provider that cannot answer withholds the feature
rather than failing a turn or holding one open on a network call. The evaluation's targeting key
is the ambient workspace, which is what lets a backend turn a feature on for one workspace at a
time.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.provider import FeatureProvider

from ufo.harness.o11y import warn
from ufo.runtime.workspace import ws_current

FLAG_TIMEOUT_SECONDS = 2.0
SERVED_TRUE = "true"
SERVED_FALSE = "false"


def init_flags(provider: FeatureProvider | None) -> None:
    """Bind the deploy's flag provider to the process-wide OpenFeature API. None leaves the no-op
    provider in place, so every flag resolves to its code default."""
    if provider is None:
        return
    api.set_provider(provider)


@runtime_checkable
class _AsyncProvider(Protocol):
    async def shutdown_async(self) -> None: ...


async def shutdown_flags(provider: FeatureProvider | None) -> None:
    """Close the provider after its readers stop and before their event loop closes."""
    if provider is None:
        return
    try:
        match provider:
            case _AsyncProvider():
                await provider.shutdown_async()
    finally:
        with ThreadPoolExecutor(max_workers=1) as executor:
            await asyncio.get_running_loop().run_in_executor(executor, provider.shutdown)


async def flag_enabled(flag: str, *, default: bool) -> bool:
    """Whether `flag` is on for the bound workspace, or `default` when nothing answers in time."""
    context = EvaluationContext(targeting_key=str(ws_current().workspace_id))
    fallback = SERVED_TRUE if default else SERVED_FALSE
    try:
        async with asyncio.timeout(FLAG_TIMEOUT_SECONDS):
            details = await api.get_client().get_string_details_async(flag, fallback, context)
    except Exception as error:
        warn(
            "flags.unresolved",
            flag=flag,
            default=default,
            error_class=type(error).__name__,
            error=str(error),
        )
        return default
    # The SDK answers a provider's failure rather than raising it, so the error code is the only
    # place a refused token or an unusable variation shows up.
    if details.error_code is not None:
        warn(
            "flags.unresolved",
            flag=flag,
            default=default,
            error_class=str(details.error_code),
            error=details.error_message or "",
        )
        return default
    if details.value not in (SERVED_TRUE, SERVED_FALSE):
        warn("flags.unreadable", flag=flag, default=default, served=details.value)
        return default
    return details.value == SERVED_TRUE
