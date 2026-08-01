"""The assistant pack plus the deterministic eval environment (fake email, calendar, and code-search
connectors) and the Docker carrier, so an eval deploy can select `[sandbox] backend = "docker"` and
run each conversation's `/workspace` as a real bind-mounted directory rather than a rewritten argv
path. Registering a carrier costs nothing until the config names it.

Registry entries surface to `list_external_tools` regardless of grants, so the fake providers must
never ride a product pack — an eval deploy selects this pack (`[pack] name = "assistant_eval"`).
The real broker extensions come out for the same reason in reverse: an eval deploy has no Composio
or Pipedream keys, so their providers are unkeyed decoys the agent burns turns failing against
instead of finding the environment."""

import ufo_pack_assistant

from ufo.sdk.manifest import Pack

NAME = "assistant_eval"
VERSION = "0.1.0"
REAL_BROKERS = frozenset({"composio", "pipedream"})
EXTENSIONS = (
    *(name for name in ufo_pack_assistant.EXTENSIONS if name not in REAL_BROKERS),
    "eval_env",
    "docker",
)


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)
