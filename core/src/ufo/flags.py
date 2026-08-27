"""Feature flags: one OpenFeature client, read through one helper, failing closed to the default.

Core owns the seam and no backend. A deploy selects one an extension registers at the
`flag_providers` Manifest point (`[flags] backend`), and `init_flags` binds it to the process-wide
OpenFeature API at boot — so `flag_enabled` is the only place in the tree that touches the SDK and
swapping Cloudflare Flagship for another provider is a config line.

`flag_enabled` fails closed to the value the call site passes. A deploy that selects no backend
resolves every flag through OpenFeature's own no-op provider; an evaluation that raises or outlives
`FLAG_TIMEOUT_SECONDS` returns the same default. A flag decides whether a feature is offered, so a
provider that cannot answer withholds the feature rather than failing a turn or holding one open on
a network call. The evaluation's targeting key is the ambient workspace, which is what lets a
backend turn a feature on for one workspace at a time.
"""

import asyncio

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.provider import FeatureProvider

from ufo.o11y import warn
from ufo.workspace import ws_current

FLAG_TIMEOUT_SECONDS = 2.0


def init_flags(provider: FeatureProvider | None) -> None:
    """Bind the deploy's flag provider to the process-wide OpenFeature API. None leaves the no-op
    provider in place, so every flag resolves to its code default."""
    if provider is None:
        return
    api.set_provider(provider)


async def flag_enabled(flag: str, *, default: bool) -> bool:
    """Whether `flag` is on for the bound workspace, or `default` when nothing answers in time."""
    context = EvaluationContext(targeting_key=str(ws_current().workspace_id))
    try:
        async with asyncio.timeout(FLAG_TIMEOUT_SECONDS):
            return await api.get_client().get_boolean_value_async(flag, default, context)
    except Exception as error:
        warn(
            "flags.unresolved",
            flag=flag,
            default=default,
            error_class=type(error).__name__,
            error=str(error),
        )
        return default
