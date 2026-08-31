"""The `extension` object kind: the deploy's active extensions projected as workspace objects.

Instances are the manifests this process loaded, so they are declarations rather than rows and
their envelope timestamps are null. The spec is what a member can encounter of an extension — the
tools they call, the object kinds they read, the credential slots they fill, the surfaces they
message on, and the jobs, hooks, sources, and subagents that act for them — named, never valued: a
slot appears by name only, and no read of this kind reaches a stored secret. Status is the other
side of the declaration, what the extension asks of the deploy: metered public egress for its
sandbox tools, and the seams it requires another extension to serve.

Installing or removing an extension is a deploy act through the lockfile, not a chat act, so every
mutation refuses.

No extension can express this kind: an `ExtensionContext` carries its own store, credentials, and
seams, never the deploy's manifest set, so only the loader — which assembles that set — can project
it. It builds the kind there and binds it with no extension context, so the handlers read the
ambient workspace directly."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ufo.runtime.ext.context import JsonValue
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.object_name import validate_object_name
from ufo.runtime.objects import (
    ObjectDetail,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import ToolContext

EXTENSION_KIND = "extension"
INSTALL_IS_A_DEPLOY_ACT = (
    "an extension is installed and removed in the deploy's lockfile with `ufoctl ext install` and "
    "`ufoctl ext remove`, and takes effect on the next serve — never through an object"
)


def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]:
    """The `extension` object name for each active manifest — object names are lowercase and
    hyphenated, so `scheduled_tasks` addresses as `scheduled-tasks`. The rendered name is held to
    the one object-name grammar, and two manifest names rendering the same object name fail loud
    here, at boot, rather than silently hiding one extension behind the other."""
    named: dict[str, Manifest] = {}
    for manifest in manifests:
        name = re.sub(r"[^a-z0-9]+", "-", manifest.name.lower()).strip("-")
        validate_object_name(name)
        if name in named:
            raise ValueError(
                f"extensions {named[name].name!r} and {manifest.name!r} both render object "
                f"name {name!r}"
            )
        named[name] = manifest
    return named


class ExtensionSpec(BaseModel):
    """One active manifest's declaration as a read renders it: the extension's own name and
    version, and the names of everything it contributes that a member reaches. A credential slot is
    named here and valued nowhere."""

    model_config = ConfigDict(extra="forbid")
    extension: str
    version: str
    tools: tuple[str, ...] = ()
    object_kinds: tuple[str, ...] = ()
    credential_slots: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    subagents: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtensionObjects:
    """Read-only handlers over the active manifest set: list names every extension with its version
    and how much it contributes, get renders one declaration beside what it asks of the deploy, and
    every mutation refuses."""

    extensions: Mapping[str, Manifest]

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        specs = {name: self._spec(manifest) for name, manifest in sorted(self.extensions.items())}
        rows = tuple(
            ObjectRow(
                name=name,
                summary=(
                    f"{spec.extension} {spec.version}, {len(spec.tools)} tools, "
                    f"{len(spec.credential_slots)} credential slots"
                ),
                fields={
                    "version": spec.version,
                    "tool_count": len(spec.tools),
                    "credential_slot_count": len(spec.credential_slots),
                },
            )
            for name, spec in specs.items()
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None:
        manifest = self.extensions.get(name)
        if manifest is None:
            return None
        return ObjectDetail(spec=self._spec(manifest), created_at=None, updated_at=None)

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        manifest = self.extensions.get(name)
        if manifest is None:
            return None
        return {
            "sandbox_internet": manifest.sandbox_internet,
            "requires": list(manifest.requires),
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: ExtensionSpec,
        old: ExtensionSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(INSTALL_IS_A_DEPLOY_ACT)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(INSTALL_IS_A_DEPLOY_ACT)

    def _spec(self, manifest: Manifest) -> ExtensionSpec:
        return ExtensionSpec(
            extension=manifest.name,
            version=manifest.version,
            tools=tuple(
                tool.name
                for tool in (
                    *manifest.tools,
                    *(tool for connector in manifest.connectors for tool in connector.tools),
                )
            ),
            object_kinds=tuple(kind.name for kind in manifest.objects),
            credential_slots=tuple(slot.name for slot in manifest.credentials),
            surfaces=tuple(surface.name for surface in manifest.surfaces),
            jobs=tuple(job.name for job in manifest.jobs),
            hooks=tuple(sorted({hook.event for hook in manifest.hooks})),
            sources=tuple(source.backend for source in manifest.sources),
            subagents=tuple(profile.name for profile in manifest.subagents),
        )


EXTENSION_DESCRIPTION = (
    "An extension this deploy loaded: its version and what it declares — tools, object kinds, "
    "credential slots, surfaces, jobs, hooks, sources, and subagents. Read-only; "
    "installing or removing one is a deploy act through the lockfile."
)
EXTENSION_GUIDANCE = (
    "The extensions this deploy is running, one object each, named lowercase and hyphenated "
    "(`scheduled_tasks` reads as `scheduled-tasks`; the spec's `extension` field carries the "
    "manifest's own name). Get one to see the tool names it adds to "
    "this turn, the object kinds it registers, the credential slots it declares (names only; "
    "whether one is filled is the `credential` kind's `filled` field), the chat surfaces, jobs, "
    "hook events, source backends, and subagent profiles. Status carries what it asks of the "
    "deploy: `sandbox_internet` when its sandbox tools need metered public egress, and `requires` "
    "for the seams another extension must serve. Listings filter and order on `version`, "
    "`tool_count`, and `credential_slot_count` — order by `tool_count` desc for the extensions "
    "contributing the most tools, or filter `credential_slot_count: 0` for the ones needing no "
    "key. Instances are declarations, not rows, so their timestamps are null. Create, update, and "
    "delete are all refused: extensions are pinned in the deploy's lockfile with `ufoctl ext`."
)
