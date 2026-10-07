"""Credential slots a workspace declares for itself, for a provider no extension covers.

A keyed provider an extension knows about arrives as a `CredentialSlot` in its manifest: the sandbox
variable the agent sends, the host the secret rides to, the header it rides in. A provider nobody
wrote a manifest for had nowhere to put that shape, so a workspace holding an API key of its own
could not use it from the sandbox at all.

An admin writes the same three fields here instead — as a row of this workspace's own, through
`object_apply` on kind `credential_slot`, in chat, with no deploy change. The
extension holds the declarations in its own table and answers them per workspace through
`Manifest.workspace_credentials`, so core hands each one to exactly the code a manifest's slot
already drives: the egress proxy's injection, the sandbox's exported sentinel, and the
`credential` object kind. The secret itself is not here — it keeps its home
in core's sealed `credential` row under the same slot name, and arrives through the same private
`request_credentials` handoff, so it never enters the transcript.

The host is a declaration's one dangerous field, because an exactly scoped host is the proxy's
allowlist and an admin writes it. `validate` refuses what is not a public DNS name, and the proxy
service resolves every host a session names and refuses one that answers a private address."""

import re
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.manifest import (
    CredentialSlot,
    InjectionTarget,
    Manifest,
    PromptSection,
    WorkspaceCredentials,
)
from ufo.sdk.objects import (
    AdminRequired,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    object_page,
)
from ufo.sdk.tools import ToolContext

NAME = "workspace_credentials"
VERSION = "0.1.0"
SLOT_KIND = "credential_slot"
SECTION_NAME = "workspace_credentials"
SENTINEL_PREFIX = "UFO_SENTINEL_WORKSPACE_"
REQUEST_DIMENSION = "requests"
DEFAULT_HEADER = "Authorization"
DECLARATION_GATE = "only a workspace admin can declare a credential slot"
DELETE_GATE = "only a workspace admin can remove a credential slot"

SLOT_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}")
ENV_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
HEADER_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")
HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_PATTERN = re.compile(rf"{HOST_LABEL}(?:\.{HOST_LABEL})+")
NUMERIC_TLD = re.compile(r"\.[0-9]+$")
HOST_MAX_LENGTH = 253
PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".home.arpa", ".arpa", ".test")
STRIPPED_HEADERS = frozenset({"connection", "proxy-connection"})
DESCRIPTION_MAX_LENGTH = 400

SLOT_TABLE = sa.Table(
    "workspace_credential_slot",
    sa.MetaData(),
    sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey("workspace.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("slot", sa.Text(), primary_key=True),
    sa.Column("env", sa.Text(), nullable=False),
    sa.Column("host", sa.Text(), nullable=False),
    sa.Column("header", sa.Text(), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.UniqueConstraint("workspace_id", "env", name="uq_workspace_credential_slot_env"),
)


class SlotInvalid(ValueError):
    """A declaration names something the wire cannot carry, or something the deploy already
    claims."""


@dataclass(frozen=True)
class WorkspaceSlot:
    """One declaration as the row holds it: what an admin named, and nothing derived from the
    workspace it belongs to. `sentinel` is what the sandbox holds in `env`, and the proxy swaps it
    for the stored secret on the wire to `host`."""

    slot: str
    env: str
    host: str
    header: str
    description: str

    @property
    def sentinel(self) -> str:
        return f"{SENTINEL_PREFIX}{self.slot}".upper()

    def credential_slot(self) -> CredentialSlot:
        """The declaration as a manifest carries it — the one projection the egress proxy's
        injection and the sandbox's export both read."""
        return CredentialSlot(
            name=self.slot,
            description=self.description,
            injection=InjectionTarget(
                host=self.host,
                header=self.header,
                sentinel=self.sentinel,
                env=self.env,
                dimension=REQUEST_DIMENSION,
            ),
        )


class SlotSpec(BaseModel):
    """What an admin writes: the sandbox variable the agent sends, the host its value rides to, the
    header it rides in, and what the slot is for. Never a value — the secret arrives through
    `request_credentials`, a private handoff, and no spec carries one."""

    model_config = ConfigDict(extra="forbid")
    slot: str
    env: str
    host: str
    header: str = DEFAULT_HEADER
    description: str = ""


def slot_object_name(slot: str) -> str:
    """The name this kind addresses a declaration by — the slot as a slug, matching the name the
    `credential` kind gives the same slot, so one screen's two rows read as one thing."""
    return re.sub(r"[^a-z0-9]+", "-", slot.lower()).strip("-")


def validate(
    slot: WorkspaceSlot, *, declared_slots: frozenset[str], declared_env: frozenset[str]
) -> None:
    """Refuse a declaration before it reaches a row. The name and the env var are the sandbox's
    namespace, shared with every manifest-declared slot, and one variable carries one value — so a
    name or a variable this deploy already declares is refused rather than shadowed. The host must
    read as a public DNS name: an address or an internal suffix would name the deploy's own network
    rather than a provider."""
    if SLOT_PATTERN.fullmatch(slot.slot) is None:
        raise SlotInvalid(
            f"credential slot name {slot.slot!r} must be lower-case letters, digits and "
            "underscores, 3 to 64 characters"
        )
    if slot.slot in declared_slots:
        raise SlotInvalid(f"an installed extension already declares slot {slot.slot!r}")
    if ENV_PATTERN.fullmatch(slot.env) is None:
        raise SlotInvalid(
            f"env var {slot.env!r} must be upper-case letters, digits and underscores, 3 to 64 "
            "characters"
        )
    if slot.env in declared_env:
        raise SlotInvalid(
            f"this deploy already exports {slot.env!r} into the sandbox; one variable carries one "
            "value"
        )
    if HEADER_PATTERN.fullmatch(slot.header) is None:
        raise SlotInvalid(f"header {slot.header!r} is not a header name")
    if slot.header.lower() in STRIPPED_HEADERS:
        raise SlotInvalid(f"header {slot.header!r} is not carried by the egress proxy")
    if (
        HOST_PATTERN.fullmatch(slot.host) is None
        or NUMERIC_TLD.search(slot.host) is not None
        or len(slot.host) > HOST_MAX_LENGTH
        or slot.host.endswith(PRIVATE_HOST_SUFFIXES)
    ):
        raise SlotInvalid(
            f"host {slot.host!r} is not a public DNS name — the secret rides to a provider on the "
            "internet, addressed by name"
        )
    if len(slot.description) > DESCRIPTION_MAX_LENGTH:
        raise SlotInvalid(f"description is longer than {DESCRIPTION_MAX_LENGTH} characters")


async def read_slots(ctx: ExtensionContext, workspace_id: UUID) -> tuple[WorkspaceSlot, ...]:
    """Every slot this workspace declares, in slot order."""
    async with ctx.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                SLOT_TABLE.c.slot,
                SLOT_TABLE.c.env,
                SLOT_TABLE.c.host,
                SLOT_TABLE.c.header,
                SLOT_TABLE.c.description,
            )
            .where(SLOT_TABLE.c.workspace_id == workspace_id)
            .order_by(SLOT_TABLE.c.slot)
        )
    return tuple(
        WorkspaceSlot(
            slot=row.slot,
            env=row.env,
            host=row.host,
            header=row.header,
            description=row.description,
        )
        for row in rows
    )


async def declared_credentials(
    ctx: ExtensionContext, workspace_id: UUID
) -> tuple[CredentialSlot, ...]:
    """What core asks this extension per workspace: the slots it holds, in the shape a manifest
    declares them in."""
    return tuple(slot.credential_slot() for slot in await read_slots(ctx, workspace_id))


async def put_slot(ctx: ExtensionContext, workspace_id: UUID, slot: WorkspaceSlot) -> None:
    """Write one declaration, replacing what the workspace declared for that slot. The env var is
    claimed once across the workspace, so a second slot naming it is refused here rather than
    silently winning the sandbox's env merge."""
    async with ctx.transaction() as connection:
        claimed = (
            await connection.execute(
                sa.select(SLOT_TABLE.c.slot).where(
                    SLOT_TABLE.c.workspace_id == workspace_id,
                    SLOT_TABLE.c.env == slot.env,
                    SLOT_TABLE.c.slot != slot.slot,
                )
            )
        ).scalar_one_or_none()
        if claimed is not None:
            raise SlotInvalid(
                f"credential slot {claimed!r} already exports {slot.env!r} into the sandbox"
            )
        updated = await connection.execute(
            sa.update(SLOT_TABLE)
            .values(
                env=slot.env,
                host=slot.host,
                header=slot.header,
                description=slot.description,
            )
            .where(
                SLOT_TABLE.c.workspace_id == workspace_id,
                SLOT_TABLE.c.slot == slot.slot,
            )
        )
        if updated.rowcount == 0:
            await connection.execute(
                sa.insert(SLOT_TABLE).values(
                    workspace_id=workspace_id,
                    slot=slot.slot,
                    env=slot.env,
                    host=slot.host,
                    header=slot.header,
                    description=slot.description,
                )
            )


async def delete_slot(ctx: ExtensionContext, workspace_id: UUID, slot: str) -> None:
    """Drop one declaration and the secret filled against it — the slot is the workspace's own, so
    nothing declares it once the row is gone and a stored value would be unreachable.

    The value goes first: the declared-slot gate over `clear` reads the live declarations, and this
    row is the one that declares this slot."""
    await ctx.credentials.clear(slot)
    async with ctx.transaction() as connection:
        await connection.execute(
            sa.delete(SLOT_TABLE).where(
                SLOT_TABLE.c.workspace_id == workspace_id,
                SLOT_TABLE.c.slot == slot,
            )
        )


@dataclass(frozen=True)
class SlotObjects:
    """The kind's handlers over this workspace's declarations: list and get render what an admin
    wrote, apply declares or rewrites one, delete removes it with its value. The value appears in
    no read — it is core's sealed row, and this kind never touches it."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = tuple(
            ObjectRow(
                name=slot_object_name(slot.slot),
                summary=f"{slot.description or slot.slot} — {slot.env} → {slot.host}",
                fields={"slot": slot.slot, "env": slot.env, "host": slot.host},
            )
            for slot in await self._slots(ctx)
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SlotSpec] | None:
        found = await self._named(ctx, name)
        if found is None:
            return None
        return ObjectDetail(
            spec=SlotSpec(
                slot=found.slot,
                env=found.env,
                host=found.host,
                header=found.header,
                description=found.description,
            ),
            created_at=None,
            updated_at=None,
        )

    async def status(
        self, ctx: ToolContext, name: str, *, expected_generation: UUID | None
    ) -> dict[str, JsonValue] | None:
        found = await self._named(ctx, name)
        if found is None:
            return None
        return {"sentinel": found.sentinel}

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: SlotSpec,
        old: SlotSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        """Declare a credential slot of this workspace's own. The declaration writes the proxy's
        allowlist and the sandbox's environment, so it is the workspace admin's act."""
        ext = self._ext(ctx)
        if not await ctx.require_speaking_admin(DECLARATION_GATE):
            raise AdminRequired(DECLARATION_GATE)
        declared = WorkspaceSlot(
            slot=spec.slot,
            env=spec.env,
            host=spec.host,
            header=spec.header or DEFAULT_HEADER,
            description=spec.description,
        )
        if slot_object_name(declared.slot) != name:
            raise ValueError(
                f"credential slot {declared.slot!r} is addressed as "
                f"{slot_object_name(declared.slot)!r}, not {name!r}"
            )
        validate(
            declared,
            declared_slots=ext.deploy_credentials.slots,
            declared_env=ext.deploy_credentials.env,
        )
        await put_slot(ext, ext.workspace_id, declared)

    async def delete(
        self, ctx: ToolContext, name: str, *, expected_generation: UUID | None
    ) -> None:
        ext = self._ext(ctx)
        found = await self._named(ctx, name)
        if not await ctx.require_speaking_admin(DELETE_GATE):
            raise AdminRequired(DELETE_GATE)
        if found is not None:
            await delete_slot(ext, ext.workspace_id, found.slot)

    async def _slots(self, ctx: ToolContext) -> tuple[WorkspaceSlot, ...]:
        ext = self._ext(ctx)
        return await read_slots(ext, ext.workspace_id)

    async def _named(self, ctx: ToolContext, name: str) -> WorkspaceSlot | None:
        return next(
            (slot for slot in await self._slots(ctx) if slot_object_name(slot.slot) == name), None
        )

    def _ext(self, ctx: ToolContext) -> ExtensionContext:
        if ctx.ext is None:
            raise RuntimeError("credential slot store dispatched without its ExtensionContext")
        return ctx.ext


SLOT_DESCRIPTION = (
    "A credential slot this workspace declares for a provider no extension covers: the sandbox "
    "variable, the provider host, and the header the secret rides in."
)
SLOT_GUIDANCE = (
    "Declare a credential slot for a provider this deploy has no extension for (workspace admin "
    "only). Apply names it: `slot` is the slot name, `env` the sandbox variable holding its "
    "sentinel, `host` the provider host the egress proxy swaps the real secret in on, `header` "
    "the header it rides in (Authorization by default), and `description` what it is for. The "
    "object's name is `slot` with underscores as hyphens. "
    "A spec never carries the secret: after the declaration lands, run the `credential` "
    "collection's request_credentials action for `slot`, a private handoff the admin fills. The "
    "sandbox then exports `env` holding a sentinel and the proxy sends the real value to `host` "
    "alone, so a curl from the sandbox authenticates and the key never enters it. "
    "Read the `credential` kind to see whether a declared slot is filled. Delete removes the "
    "declaration and the stored value with it, since nothing else declares the slot."
)

SECTION_BODY = "\n".join(
    (
        "## Workspace credential slots",
        "",
        "A provider with no extension and no connector is still reachable: a workspace admin "
        "declares a credential slot for it, and the agent calls the provider's own REST API from "
        "the sandbox.",
        "",
        "1. Apply the `credential_slot` kind with `slot`, `env`, `host` and (where the provider "
        "does not read `Authorization`) `header`. Ask the admin for the host and the header from "
        "the provider's API docs — never guess them.",
        "2. Run the `credential` collection's `request_credentials` action for that slot. The "
        "admin enters the key privately; never ask for a key in chat prose.",
        '3. Call the API as `curl -sS "https://<host>/<path>" -H "<header>: $<env>"`. The '
        "variable holds a sentinel, not the secret — the egress proxy swaps the real key in on "
        "the wire to that host alone, so the sandbox never holds it and it cannot be read, echoed "
        "or written to a file.",
        "",
        "An env var that is absent means the slot is empty. Read the `credential` kind, which "
        "reports each slot filled or empty, then ask the member for what is missing rather than "
        "guessing a key.",
    )
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        objects=(
            ObjectKind(
                name=SLOT_KIND,
                description=SLOT_DESCRIPTION,
                guidance=SLOT_GUIDANCE,
                spec_model=SlotSpec,
                store=SlotObjects(),
                list_fields=frozenset({"slot", "env", "host"}),
            ),
        ),
        workspace_credentials=WorkspaceCredentials(read=declared_credentials),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
