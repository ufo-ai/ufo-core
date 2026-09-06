"""The `open` backend answers every flag on through the same client the fleet reads Flagship
through, so a call site that reads closed where nothing answers offers its feature on a stack that
selects it — except a flag declared `open=False`, which selects between shapes the fleet already
serves one of and reads off so the stack lands on the fleet's."""

from uuid import uuid4

from openfeature.provider.in_memory_provider import InMemoryProvider
from ufo_ext_flags_open import FLAG_BACKEND, build, manifest

from ufo.flags import flag_enabled, init_flags
from ufo.runtime.workspace import ws
from ufo.sdk.manifest import FlagSpec

DECLARED = {
    "enable-notification-app": FlagSpec(key="enable-notification-app", what="offered"),
    "enable-lanes-shell": FlagSpec(key="enable-lanes-shell", what="a shell", open=False),
}


async def test_every_flag_reads_on_except_one_declared_closed_under_this_backend() -> None:
    try:
        init_flags(build(0.0, DECLARED))
        with ws(uuid4()):
            closed_by_default = await flag_enabled("enable-notification-app", default=False)
            open_by_default = await flag_enabled("enable-memory-tab", default=True)
            never_declared = await flag_enabled("no-such-flag", default=False)
            selector = await flag_enabled("enable-lanes-shell", default=False)
    finally:
        init_flags(InMemoryProvider({}))

    assert closed_by_default is True
    assert open_by_default is True
    assert never_declared is True
    assert selector is False


def test_the_manifest_registers_the_open_backend() -> None:
    [spec] = manifest().flag_providers
    assert spec.backend == FLAG_BACKEND
    assert spec.build(900.0, {}).get_metadata().name == FLAG_BACKEND
