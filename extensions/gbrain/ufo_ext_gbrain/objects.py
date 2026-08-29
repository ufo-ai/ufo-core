"""The `gbrain_source` object kind: registered markdown-page sources managed through the object
verbs.

A gbrain source is one synced origin — a GitHub repository (optionally pinned to a branch) or a
serve-local directory — carried as one core `source` row the sync driver polls. Identity IS the
origin, so names derive from it (`gbrain-<8-hex digest>`): apply with the wrong name refuses and
hands back the exact one, changing repo, branch, or root is a different source under its own name,
and re-applying the identical spec is a no-op that grants the calling agent the settled row.

A source is private to its registering member by default; the model decides `shared` at
registration, and only the registrar may later flip a private source to shared — the reverse is
delete-and-recreate. Delete is registrar-or-admin and its pages follow through the page-tombstone
pipeline. A directory root reads the serving host's own filesystem, so it is operator authority:
rows arrive from the deploy's `[[sources]]` config at boot, the kind lists them, and an apply
naming `root` is refused."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ufo.sdk.context import ExtensionContext
from ufo.sdk.objects import (
    AdminRequired,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectOwner,
    OwnedRow,
    UnknownObject,
    VerbNotSupported,
)
from ufo.sdk.subjects import SHARED_SUBJECT, member_subject, subject_shared
from ufo.sdk.tools import SpeakerRequired, ToolContext
from ufo_ext_gbrain.folder import FOLDER_BACKEND, GbrainFolderConfig
from ufo_ext_gbrain.git import GIT_BACKEND, GbrainGitConfig

GBRAIN_KIND = "gbrain_source"
SUMMARY_MAX = 120
NAME_DIGEST_HEX = 8
SHARE_GATE = (
    "only the registering member may change a gbrain source; workspace admins may inspect or "
    "remove it"
)
DELETE_GATE = "only the registering member or a workspace admin may remove a gbrain source"
RESYNC_GATE = "only the registering member or a workspace admin may resync a gbrain source"
ROOT_REFUSAL = (
    "a directory root is operator config — a [[sources]] entry with backend "
    f"{FOLDER_BACKEND!r} in the deploy's ufo.toml registers it at boot"
)


def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str:
    """The kind's one identity rule: a source's object name derives from the origin it syncs —
    repository, branch, or directory root — so the same origin answers to the same name wherever
    it is named."""
    digest = hashlib.sha256(
        json.dumps({"branch": branch, "repo": repo, "root": root}, sort_keys=True).encode()
    ).hexdigest()[:NAME_DIGEST_HEX]
    return f"gbrain-{digest}"


class GbrainSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str | None = Field(
        default=None,
        description="GitHub repository as owner/name whose markdown files sync as pages. "
        "Set exactly one of repo or root.",
    )
    branch: str | None = Field(
        default=None,
        description="Branch to track, only with repo; unset tracks the repository's default "
        "branch.",
    )
    root: str | None = Field(
        default=None,
        description="Serve-local directory whose markdown files sync as pages. Read-only: a root "
        "arrives from the deploy's [[sources]] config at boot and cannot be applied in chat.",
    )
    shared: bool = Field(
        default=False,
        description="Sync into the whole workspace's shared memory rather than privately to the "
        "registering member. Set it only when the member's words say the source is for the team.",
    )
    resync: bool = Field(
        default=False,
        description="Set true to schedule an immediate sync — an act, not state: it changes "
        "nothing else, always reads back false, and is the registering member's or a workspace "
        "admin's.",
    )

    @model_validator(mode="after")
    def validate_origin(self) -> "GbrainSpec":
        if (self.repo is None) == (self.root is None):
            raise ValueError("set exactly one of repo or root")
        if self.branch is not None and self.repo is None:
            raise ValueError("branch names a repository branch — set it only with repo")
        return self


@dataclass(frozen=True)
class _Origin:
    backend: str
    config: GbrainGitConfig | GbrainFolderConfig
    name: str


def _origin(spec: GbrainSpec) -> _Origin:
    if spec.root is not None:
        return _Origin(
            backend=FOLDER_BACKEND,
            config=GbrainFolderConfig(root=spec.root),
            name=gbrain_source_name(None, None, spec.root),
        )
    if spec.repo is None:
        raise ValueError("set exactly one of repo or root")
    config = GbrainGitConfig(repo=spec.repo, branch=spec.branch)
    return _Origin(
        backend=GIT_BACKEND,
        config=config,
        name=gbrain_source_name(config.repo, config.branch, None),
    )


def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]:
    return (spec.repo, spec.branch, spec.root, spec.shared)


@dataclass(frozen=True)
class _Registered:
    source_id: UUID
    repo: str | None
    branch: str | None
    root: str | None
    subject: str
    owner_member_id: UUID | None
    next_sync_at: datetime
    consecutive_errors: int
    created_at: datetime
    updated_at: datetime

    @property
    def name(self) -> str:
        return gbrain_source_name(self.repo, self.branch, self.root)

    def spec(self) -> GbrainSpec:
        return GbrainSpec(
            repo=self.repo,
            branch=self.branch,
            root=self.root,
            shared=subject_shared(self.subject),
        )

    def summary(self) -> str:
        if self.root is not None:
            return f"server directory {self.root}"[:SUMMARY_MAX]
        origin = self.repo if self.branch is None else f"{self.repo}@{self.branch}"
        return f"github repository {origin}"[:SUMMARY_MAX]


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("gbrain source objects dispatched without their ExtensionContext")
    return ext


async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]:
    registered: list[_Registered] = []
    for record in await ext.sources():
        if record.backend == GIT_BACKEND:
            git = GbrainGitConfig.model_validate(record.config)
            repo, branch, root = git.repo, git.branch, None
        elif record.backend == FOLDER_BACKEND:
            folder = GbrainFolderConfig.model_validate(record.config)
            repo, branch, root = None, None, folder.root
        else:
            continue
        registered.append(
            _Registered(
                source_id=record.id,
                repo=repo,
                branch=branch,
                root=root,
                subject=record.subject,
                owner_member_id=record.owner_member_id,
                next_sync_at=record.next_sync_at,
                consecutive_errors=record.consecutive_errors,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
    return tuple(registered)


async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None:
    return next(
        (row for row in await _registered_from_ext(_require_ext(ext)) if row.name == name),
        None,
    )


@dataclass(frozen=True)
class GbrainObjects(MemberReadableObjects[GbrainSpec, ObjectOwner]):
    """The kind's handlers over the workspace's gbrain source rows: get/list read the rows
    registered under the two gbrain backends; apply validates the origin, then registers it as one
    row (the first sync is scheduled immediately) — private to the registering member unless the
    model asks for `shared`; delete removes the row and its synced pages follow through the
    page-tombstone pipeline. The per-member visibility and registrar-or-admin gate is the base's,
    in a turn and in the portal alike. A directory root is operator authority: its rows arrive
    from the deploy's `[[sources]]` config at boot, and an apply naming `root` is refused."""

    kind_name: ClassVar[str] = GBRAIN_KIND
    mutate_gate: ClassVar[str] = SHARE_GATE
    delete_gate: ClassVar[str] = DELETE_GATE
    delete_requires_speaker: ClassVar[bool] = True

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: GbrainSpec,
        old: GbrainSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        """A resync is the registering member's or an admin's and changes nothing else. Re-applying
        the identical spec of a source the caller can already see (`old` is non-None only for a
        visible source, since the base `get` hides the rest) registers nothing and stays outside
        the base's gate: all it does is grant the calling agent the source it names. Every other
        apply — register, share-flip — goes through the base's member/admin gate."""
        if spec.resync:
            await self._resync(ctx, name, spec, old)
            return
        if old is not None and _identity(spec) == _identity(old):
            await self._grant_settled(ctx, name)
            return
        if old is None:
            taken = await _registered_named(ctx.ext, _origin(spec).name)
            if taken is not None and not (
                subject_shared(taken.subject)
                or taken.owner_member_id == ctx.acting_member_id
                or await ctx.speaker_is_admin()
            ):
                raise VerbNotSupported(
                    f"{taken.summary()} is already registered privately by another member — "
                    "its registrar or a workspace admin can share it"
                )
        await super().apply(ctx, name, spec, old, expected_generation=expected_generation)

    async def _grant_settled(self, ctx: ToolContext, name: str) -> None:
        """Grant the calling agent the source its identical submit settles on: registration is
        what grants an agent a feed, and a submit that names what the workspace already holds
        registers nothing, so without the grant the agent reads back `updated`, holds no feed, and
        sees no error. The grant is the registering member's own or a shared source's, and a
        speakerless turn grants nothing — a granting act takes a live member."""
        speaker = ctx.speaker_member_id
        owner = await self._owner(ctx, name)
        if speaker is None or owner is None or not (owner.shared or self._owned(owner, speaker)):
            return
        registered = await _registered_named(ctx.ext, name)
        if registered is None:
            return
        await _require_ext(ctx.ext).grant_source(
            registered.source_id, agent_id=ctx.turn.agent_id, actor_member_id=speaker
        )

    async def _resync(
        self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None
    ) -> None:
        """Schedule an immediate sync: the act rides `apply` with `resync` set and the source's
        current spec, so a submit that also edits the origin or disclosure is refused whole rather
        than half-applied. The gate is the delete gate's population — a resync drives traffic on
        the registrar's authority, so seeing a shared source is not enough to spend it."""
        if old is None or _identity(spec) != _identity(old):
            raise VerbNotSupported(
                "a resync changes nothing else — apply the source's current spec with resync set"
            )
        owner = await self._owner(ctx, name)
        is_admin = await ctx.speaker_is_admin()
        if owner is None or not self._visible(owner, ctx.acting_member_id, is_admin):
            raise UnknownObject(f"no {GBRAIN_KIND} object named {name!r}")
        if not self._owned(owner, ctx.acting_member_id) and not is_admin:
            raise AdminRequired(RESYNC_GATE)
        registered = await _registered_named(ctx.ext, name)
        if registered is None:
            raise UnknownObject(f"no {GBRAIN_KIND} object named {name!r}")
        await _require_ext(ctx.ext).schedule_source_sync((registered.source_id,))

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[ObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=registered.name,
                summary=registered.summary(),
                owner=ObjectOwner(
                    member_id=registered.owner_member_id,
                    shared=subject_shared(registered.subject),
                ),
            )
            for registered in await _registered_from_ext(_require_ext(ext))
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: ObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[GbrainSpec] | None:
        registered = await _registered_named(ext, name)
        if registered is None:
            return None
        return ObjectDetail(
            spec=registered.spec(),
            created_at=registered.created_at,
            updated_at=registered.updated_at,
        )

    async def _status(
        self, ctx: ToolContext, name: str, _owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        registered = await _registered_named(ctx.ext, name)
        if registered is None:
            return None
        shared = subject_shared(registered.subject)
        status: dict[str, JsonValue] = {
            "shared": shared,
            "next_sync_at": registered.next_sync_at.isoformat(),
            "consecutive_errors": registered.consecutive_errors,
        }
        if not shared and registered.owner_member_id is not None:
            status["owner_member_id"] = str(registered.owner_member_id)
        return status

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: GbrainSpec,
        old: GbrainSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        ext = _require_ext(ctx.ext)
        speaker = ctx.speaker_member_id
        if speaker is None:
            raise SpeakerRequired("registering a gbrain source requires a speaking member")
        if spec.root is not None:
            raise VerbNotSupported(ROOT_REFUSAL)
        origin = _origin(spec)
        if name != origin.name:
            raise ValueError(
                f"gbrain source names derive from the origin — apply this spec as name "
                f"{origin.name!r}"
            )
        registered = await _registered_named(ctx.ext, name)
        if registered is None:
            await ext.register_source(
                origin.backend,
                origin.config,
                subject=SHARED_SUBJECT if spec.shared else member_subject(speaker),
                owner_member_id=speaker,
                agent_id=ctx.turn.agent_id,
            )
            return
        if subject_shared(registered.subject) and not spec.shared:
            raise VerbNotSupported(
                "a shared gbrain source stays shared — delete it and recreate it privately"
            )
        if spec.shared and not subject_shared(registered.subject):
            await ext.set_source_subject((registered.source_id,), SHARED_SUBJECT)
        await ext.grant_source(
            registered.source_id, agent_id=ctx.turn.agent_id, actor_member_id=speaker
        )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        registered = await _registered_named(ctx.ext, name)
        if registered is None:
            raise UnknownObject(f"no {GBRAIN_KIND} object named {name!r}")
        await _require_ext(ctx.ext).remove_source(registered.source_id)


GBRAIN_OBJECT = ObjectKind(
    name=GBRAIN_KIND,
    description=(
        "A markdown page source: one GitHub repository or one serve-local directory, synced "
        "privately to its registering member unless shared. Only the registrar may share and an "
        "admin may inspect or remove; a directory arrives from the deploy's [[sources]] config, "
        "never from apply."
    ),
    guidance=(
        "Apply a manifest whose spec names a GitHub `repo` (owner/name); `branch` only refines "
        "`repo`, and unset tracks the repository's default branch. Names derive from the origin — "
        "a wrong name is refused with the exact derived name to re-apply, and changing repo or "
        "branch is a different source under its own name. Synced markdown lands in memory_search "
        "within about a minute of each sync; a file deleted from the repository or directory "
        "tombstones its page, and deleting the source tombstones them all. A private repository "
        "needs the workspace `github_token` credential; public repositories sync without it. A "
        "directory `root` is read-only here: it arrives from the deploy's [[sources]] config at "
        "boot, and an apply naming one is refused. An origin already registered privately by "
        "another member is refused whole; its registrar or an admin can share it. A source syncs "
        "privately to its registering member by default; set `shared: true` at apply — or in a "
        "later re-apply by the registrar — only when the member's words say the source is for "
        "the team. Unsharing is delete-and-recreate. To sync immediately, re-apply the current "
        "spec with `resync: true` (registrar or admin; it always reads back false). Delete is "
        "registrar-or-admin."
    ),
    spec_model=GbrainSpec,
    store=GbrainObjects(),
)
