"""The credential slots one workspace sees: this deploy's own declarations, and the ones an
extension resolves per workspace.

A keyed provider an extension knows about arrives as a `CredentialSlot` in a manifest — the sandbox
variable, the host the secret rides to, the header it rides in — and that declaration is the same
for every workspace on the deploy. An extension that holds rows of its own answers the same shape
per workspace instead, through `Manifest.workspace_credentials`. This module is where the two meet,
so the session policy's binds and the sandbox's environment read one source and neither knows which
half a slot came from.

A per-workspace host is written inside the workspace, not by this deploy's code; the proxy service
resolves every host a session names and refuses one that answers a private address, so such a host
opens no tunnel to its own network."""

from dataclasses import dataclass, field
from uuid import UUID

from ufo.runtime.access.credentials import DeclaredSlot, HostChoice
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.manifest import (
    CredentialSlot,
    WorkspaceCredentials,
    declared_slot,
)


@dataclass(frozen=True)
class SlotProvider:
    """One extension's per-workspace slots, bound to the context its reads run under — the same
    workspace-scoped handle its tools and jobs receive."""

    extension: str
    ctx: ExtensionContext
    provider: WorkspaceCredentials

    async def read(self, workspace_id: UUID) -> tuple[CredentialSlot, ...]:
        return await self.provider.read(self.ctx, workspace_id)


@dataclass(frozen=True)
class _ResolvedClaims:
    names: set[str]
    deploy_env: set[str]
    exported: dict[str, tuple[str, object]] = field(default_factory=dict)

    def add_name(self, extension: str, slot: CredentialSlot) -> None:
        if slot.name in self.names:
            raise RuntimeError(
                f"{extension!r} resolves credential slot {slot.name!r}, which another installed "
                "declaration already claims"
            )
        self.names.add(slot.name)

    def add_injection(self, extension: str, slot: CredentialSlot) -> None:
        target = slot.injection
        if target is None:
            return
        self._claim_env(extension, slot.name, target.env, slot.name)
        if isinstance(target.host, HostChoice):
            if target.host.slot not in self.names:
                raise RuntimeError(
                    f"credential slot {slot.name!r} selects its host through undeclared slot "
                    f"{target.host.slot!r}"
                )
            if target.host.env is not None:
                self._claim_env(extension, slot.name, target.host.env, target.host)

    def _claim_env(self, extension: str, slot: str, name: str, carries: object) -> None:
        if name in self.deploy_env:
            raise RuntimeError(
                f"{extension!r} resolves credential slot {slot!r} exporting env {name!r}, "
                "which this deploy already exports"
            )
        holder, held = self.exported.setdefault(name, (slot, carries))
        if held != carries:
            raise RuntimeError(
                f"credential slots {holder!r} and {slot!r} export env {name!r} carrying "
                "different values"
            )


@dataclass(frozen=True)
class WorkspaceSlots:
    """Every injecting slot a workspace holds. `deploy` is what the installed manifests declare,
    read once at boot; `providers` are resolved per workspace on each derivation, so a slot declared
    inside a live turn reaches the next one."""

    deploy: tuple[CredentialSlot, ...] = ()
    providers: tuple[SlotProvider, ...] = field(default_factory=tuple)
    claimed_slots: frozenset[str] = frozenset()
    claimed_env: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        """Whether any slot could resolve at all — no declaration and no provider means the
        derivation has nothing to read, so the caller skips it without opening a workspace read."""
        return bool(self.deploy or self.providers)

    async def workspace(self, workspace_id: UUID) -> tuple[CredentialSlot, ...]:
        """Only the per-workspace slots, in provider order."""
        return tuple(slot for _extension, slot in await self._resolved(workspace_id))

    async def all(self, workspace_id: UUID) -> tuple[CredentialSlot, ...]:
        """The deploy's slots and this workspace's own, as one set — what a session binds and what
        the sandbox exports a host for."""
        return (*self.deploy, *await self.workspace(workspace_id))

    async def declared(self, workspace_id: UUID) -> tuple[DeclaredSlot, ...]:
        """This workspace's own slots as the `credential` object kind projects them, each
        attributed to the extension that resolved it."""
        return tuple(
            declared_slot(slot, extension) for extension, slot in await self._resolved(workspace_id)
        )

    async def _resolved(self, workspace_id: UUID) -> tuple[tuple[str, CredentialSlot], ...]:
        resolved: list[tuple[str, CredentialSlot]] = []
        for provider in self.providers:
            resolved.extend(
                (provider.extension, slot) for slot in await provider.read(workspace_id)
            )

        claims = self._claims()
        for extension, slot in resolved:
            claims.add_name(extension, slot)
        for extension, slot in resolved:
            claims.add_injection(extension, slot)
        return tuple(resolved)

    def _claims(self) -> _ResolvedClaims:
        names = set(self.claimed_slots) | {slot.name for slot in self.deploy}
        deploy_env = set(self.claimed_env)
        for slot in self.deploy:
            target = slot.injection
            if target is None:
                continue
            deploy_env.add(target.env)
            if isinstance(target.host, HostChoice) and target.host.env is not None:
                deploy_env.add(target.host.env)
        return _ResolvedClaims(names=names, deploy_env=deploy_env)
