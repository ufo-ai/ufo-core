"""Replay-safe serialization for everything DBOS persists.

DBOS writes workflow inputs, step outputs, and terminal errors to the system database and reads
them back on crash recovery — across deploys, so the build replaying a recording is not the build
that wrote it. A plain pickle restores a pydantic model's field dict verbatim: construction never
runs, so a field added between the two builds is missing from the restored object and the first
read of it raises AttributeError mid-replay, failing the recovered turn. `ReplaySafeSerializer`
keeps the pickle format but records every `BaseModel` as its class plus its field values, rebuilt
through `model_validate` on load — a missing field takes the class's current default, a dropped
one is ignored, and a missing field without a default fails by name at the read instead of as an
AttributeError deep in the loop. Rows recorded before this serializer carry the default
serializer's name and keep loading through it; `_rebuild` is referenced by name from every
recording made here, so it must keep this module and name for as long as those rows replay.

A recording also names each class by the module path of the build that wrote it, and module paths
move between builds. `MOVED_MODULES` is the wire codec for those paths: deserialization resolves a
recorded module through it, so a recording written before a move replays on the build after it.
An entry maps the path a past release wrote to the module that defines the symbol today — including
a module that has since moved out of core into an extension package; a module move lands with its
entry here and an entry lives for as long as recordings naming it replay.
"""

import base64
import io
import pickle
from collections.abc import Callable

from dbos import DBOSClient, Serializer
from pydantic import BaseModel

SERIALIZATION_NAME = "ufo_pickle"

MOVED_MODULES = {
    "ufo.access": "ufo.runtime.access",
    "ufo.access.connectors": "ufo.runtime.access.connectors",
    "ufo.access.credentials": "ufo.runtime.access.credentials",
    "ufo.access.egress_control": "ufo.runtime.access.egress_control",
    "ufo.access.egress_resolver": "ufo.runtime.access.egress_resolver",
    "ufo.access.egress_rules": "ufo.runtime.access.egress_rules",
    "ufo.access.grants": "ufo.runtime.access.grants",
    "ufo.agent_scope": "ufo.runtime.agent_scope",
    "ufo.auth": "ufo.harness.auth",
    "ufo.runtime.auth": "ufo.harness.auth",
    "ufo.auth.bearer": "ufo.harness.auth.bearer",
    "ufo.runtime.auth.bearer": "ufo.harness.auth.bearer",
    "ufo.auth.surface_token": "ufo.harness.auth.surface_token",
    "ufo.runtime.auth.surface_token": "ufo.harness.auth.surface_token",
    "ufo.auth.token_signing": "ufo.harness.auth.token_signing",
    "ufo.runtime.auth.token_signing": "ufo.harness.auth.token_signing",
    "ufo.billing": "ufo.runtime.billing",
    "ufo.billing.accounting": "ufo.runtime.billing.accounting",
    "ufo.billing.balance": "ufo.runtime.billing.balance",
    "ufo.durability": "ufo.harness.durability",
    "ufo.ext": "ufo.runtime.ext",
    "ufo.ext.context": "ufo.runtime.ext.context",
    "ufo.ext.conversation_slots": "ufo.runtime.ext.conversation_slots",
    "ufo.ext.extension_kind": "ufo.host.ext.extension_kind",
    "ufo.runtime.ext.extension_kind": "ufo.host.ext.extension_kind",
    "ufo.ext.loader": "ufo.runtime.ext.manifest",
    "ufo.ext.manifest": "ufo.runtime.ext.manifest",
    "ufo.ext.operator": "ufo.runtime.ext.operator",
    "ufo.ext.scheduled_fire": "ufo.runtime.ext.scheduled_fire",
    "ufo.ext.store": "ufo.host.ext.store",
    "ufo.ext.surface": "ufo.runtime.ext.surface",
    "ufo.hub": "ufo.runtime.hub",
    "ufo.indexing": "ufo.runtime.indexing",
    "ufo.kinds": "ufo.runtime.kinds",
    "ufo.kinds.agent_setup": "ufo.runtime.kinds.agent_setup",
    "ufo.kinds.agents": "ufo.runtime.kinds.agents",
    "ufo.kinds.artifacts": "ufo.host.kinds.artifacts",
    "ufo.runtime.kinds.artifacts": "ufo.host.kinds.artifacts",
    "ufo.kinds.conversations": "ufo.host.kinds.conversations",
    "ufo.runtime.kinds.conversations": "ufo.host.kinds.conversations",
    "ufo.kinds.credential_kind": "ufo.host.kinds.credential_kind",
    "ufo.runtime.kinds.credential_kind": "ufo.host.kinds.credential_kind",
    "ufo.kinds.governance": "ufo.runtime.kinds.governance",
    "ufo.kinds.members": "ufo.host.kinds.members",
    "ufo.runtime.kinds.members": "ufo.host.kinds.members",
    "ufo.kinds.provisioning": "ufo.runtime.kinds.provisioning",
    "ufo.kinds.surface_kind": "ufo.host.kinds.surface_kind",
    "ufo.runtime.kinds.surface_kind": "ufo.host.kinds.surface_kind",
    "ufo.kinds.workspace_kind": "ufo.host.kinds.workspace_kind",
    "ufo.runtime.kinds.workspace_kind": "ufo.host.kinds.workspace_kind",
    "ufo.listings": "ufo.runtime.listings",
    "ufo.loop": "ufo.runtime",
    "ufo.runtime.environment": "ufo.host.environment",
    "ufo.loop.compaction": "ufo_ext_context_compact.compaction",
    "ufo.runtime.compaction": "ufo_ext_context_compact.compaction",
    "ufo_ext_context_summarization.compaction": "ufo_ext_context_compact.compaction",
    "ufo.runtime.rollover": "ufo_ext_context_rollover.rollover",
    "ufo.loop.delivery": "ufo.runtime.delivery",
    "ufo.loop.engine": "ufo.runtime.engine",
    "ufo.loop.profiles": "ufo.runtime.profiles",
    "ufo.loop.prompts": "ufo.runtime.prompts",
    "ufo.loop.prompts.render": "ufo.runtime.prompts.render",
    "ufo.loop.queue": "ufo.runtime.queue",
    "ufo.loop.replies": "ufo.harness.replies",
    "ufo.loop.spawn_catalog": "ufo.host.spawn_catalog",
    "ufo.runtime.spawn_catalog": "ufo.host.spawn_catalog",
    "ufo.loop.steps": "ufo.runtime.steps",
    "ufo.loop.subagents": "ufo.runtime.subagents",
    "ufo.loop.tool_bridge": "ufo.runtime.tool_bridge",
    "ufo.loop.transcript": "ufo.runtime.transcript",
    "ufo.media": "ufo.runtime.media",
    "ufo.media.artifact_url": "ufo.runtime.media.artifact_url",
    "ufo.media.document_renderer": "ufo.harness.document_renderer",
    "ufo.runtime.media.document_renderer": "ufo.harness.document_renderer",
    "ufo.media.image_previews": "ufo.runtime.media.image_previews",
    "ufo.media.preview_renderer": "ufo.runtime.media.preview_renderer",
    "ufo.media.previews": "ufo.runtime.media.previews",
    "ufo.media.site_previewer": "ufo.runtime.media.site_previewer",
    "ufo.memory": "ufo.runtime.memory",
    "ufo.models": "ufo.harness.models",
    "ufo.models.anthropic": "ufo.harness.models.anthropic",
    "ufo.models.catalog": "ufo.harness.models.catalog",
    "ufo.models.catalog_skill": "ufo.harness.models.catalog_skill",
    "ufo.models.interface": "ufo.harness.models.interface",
    "ufo.models.openai": "ufo.harness.models.openai",
    "ufo.models.pricing": "ufo.harness.models.pricing",
    "ufo.models.registry": "ufo.harness.models.registry",
    "ufo.models.spec": "ufo.harness.models.spec",
    "ufo.o11y": "ufo.harness.o11y",
    "ufo.object_name": "ufo.runtime.object_name",
    "ufo.object_scope": "ufo.runtime.object_scope",
    "ufo.object_views": "ufo.runtime.object_views",
    "ufo.objects": "ufo.runtime.objects",
    "ufo.sandbox": "ufo.harness.sandbox",
    "ufo.sandbox.cache": "ufo.harness.sandbox.cache",
    "ufo.sandbox.client_binary": "ufo.harness.sandbox.client_binary",
    "ufo.sandbox.conversation": "ufo.harness.sandbox.conversation",
    "ufo.sandbox.exec_env": "ufo.harness.sandbox.exec_env",
    "ufo.sandbox.ingress_host": "ufo.harness.sandbox.ingress_host",
    "ufo.sandbox.ingress_serve": "ufo.harness.sandbox.ingress_serve",
    "ufo.sandbox.ingress_token": "ufo.harness.sandbox.ingress_token",
    "ufo.sandbox.ingress_url": "ufo.harness.sandbox.ingress_url",
    "ufo.sandbox.local": "ufo.harness.sandbox.local",
    "ufo.sandbox.preview": "ufo.harness.sandbox.preview",
    "ufo.sandbox.select": "ufo.harness.sandbox.select",
    "ufo.sandbox.session": "ufo.harness.sandbox.session",
    "ufo.sandbox.terminal": "ufo.harness.sandbox.terminal",
    "ufo.search": "ufo.runtime.search",
    "ufo.seats": "ufo.runtime.seats",
    "ufo.skills": "ufo.runtime.skills",
    "ufo.skills.runtime": "ufo.runtime.skills.runtime",
    "ufo.skills.selection": "ufo.runtime.skills.selection",
    "ufo.sources": "ufo.runtime.sources",
    "ufo.sources.backend": "ufo.runtime.sources.backend",
    "ufo.sources.connector": "ufo.runtime.sources.connector",
    "ufo.sources.rest": "ufo.runtime.sources.rest",
    "ufo.sources.sync": "ufo.runtime.sources.sync",
    "ufo.surfaces": "ufo.runtime.surfaces",
    "ufo.surfaces.admission": "ufo.runtime.surfaces.admission",
    "ufo.surfaces.artifacts": "ufo.runtime.surfaces.artifacts",
    "ufo.surfaces.cli": "ufo.runtime.surfaces.cli",
    "ufo.surfaces.hub_tail": "ufo.runtime.surfaces.hub_tail",
    "ufo.surfaces.stop": "ufo.runtime.surfaces.stop",
    "ufo.tools": "ufo.runtime.tools",
    "ufo.tools.bridge": "ufo.runtime.tools.bridge",
    "ufo.tools.builtins": "ufo.host.tools.builtins",
    "ufo.tools.context": "ufo.runtime.tools.context",
    "ufo.tools.file_changes": "ufo.runtime.tools.file_changes",
    "ufo.tools.registry": "ufo.runtime.tools.registry",
    "ufo.tools.tasks": "ufo.runtime.tools.tasks",
    "ufo.turns": "ufo.runtime.turns",
    "ufo.turns.activity": "ufo.runtime.turns.activity",
    "ufo.turns.ambient_reply": "ufo.runtime.turns.ambient_reply",
    "ufo.turns.audience": "ufo.runtime.turns.audience",
    "ufo.turns.cancellation": "ufo.runtime.turns.cancellation",
    "ufo.turns.contracts": "ufo.runtime.turns.contracts",
    "ufo.turns.delivery_register": "ufo.runtime.turns.delivery_register",
    "ufo.turns.subjects": "ufo.runtime.turns.subjects",
    "ufo.turns.transcript": "ufo.runtime.turns.transcript",
    "ufo.turns.untrusted": "ufo.harness.untrusted",
    "ufo.turns.workspace_changes": "ufo.runtime.turns.workspace_changes",
    "ufo.workspace": "ufo.runtime.workspace",
}
DBOS_CLIENT_POOL_SIZE = 5


def replay_safe_client(system_database_url: str) -> DBOSClient:
    """The one way this repo constructs a DBOSClient: rows are recorded under this serializer's
    name, so a client built without it cannot decode what the engine wrote — DBOS degrades the
    read to the raw serialized string instead of raising."""
    return DBOSClient(
        system_database_url=system_database_url,
        serializer=ReplaySafeSerializer(),
        system_database_pool_size=DBOS_CLIENT_POOL_SIZE,
    )


def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel:
    return model_class.model_validate(fields)


class _ModelPickler(pickle.Pickler):
    def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]:
        if isinstance(obj, BaseModel):
            return (_rebuild, (type(obj), dict(obj.__dict__)))
        return NotImplemented


class _CompatUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> object:
        return super().find_class(MOVED_MODULES.get(module, module), name)


class ReplaySafeSerializer(Serializer):
    """The serializer every DBOS launch and client in this repo is configured with."""

    def name(self) -> str:
        return SERIALIZATION_NAME

    def serialize(self, data: object) -> str:
        buffer = io.BytesIO()
        _ModelPickler(buffer).dump(data)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def deserialize(self, serialized_data: str) -> object:
        return _CompatUnpickler(io.BytesIO(base64.b64decode(serialized_data))).load()
