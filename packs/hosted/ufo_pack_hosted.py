"""The hosted-schema migration pack: the union of every extension any hosted tenant runs.

The hosted service shares one Postgres database across tenants (RFC 0011 §2), so its schema must be
the union of every hosted pack's extension migrations — the cluster-scoped migrate Job runs
`ufoctl migrate` with this pack once per rollout. A per-tenant serve still activates its own
narrower pack (`assistant_hosted` or `apex`); its `init` runs `--skip-migrations`, because alembic
cannot reconcile a narrower pack's script set against an `alembic_version` carrying another pack's
revisions. It bundles no extension the product does not ship, so every table it creates is
workspace-scoped and the RLS bootstrap's fail-loud check passes."""

from ufo_pack_assistant_hosted import EXTENSIONS as ASSISTANT_HOSTED
from ufo_pack_gateway import EXTENSIONS as APEX

from ufo.sdk.manifest import Pack

NAME = "hosted"
VERSION = "0.1.0"
EXTENSIONS = (*ASSISTANT_HOSTED, *(name for name in APEX if name not in ASSISTANT_HOSTED))


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)
