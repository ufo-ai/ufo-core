"""Assemble one turn's environment: the prompt, tool offer, and skills composed from the
discovered manifests' contributions under the runtime's access policy, then reshaped by the
turn's pinned environment document. The runtime resolves the turn's model and hands over the
facts; this layer owns what the model reads and may call.

A document narrows the platform's grants; it cannot widen them. Rewritten text touches only what
the model reads, a removal only shrinks the offer, and a scoped name the offer does not hold fails
the turn loud — so every surviving tool keeps the handler, capability context, and authorization
the platform bound. A `run` tool is the one addition a document makes: its implementation is a
command inside the turn's own sandbox, which grants nothing the sandbox's shell does not already
grant."""

import asyncio
import copy
import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, create_model

from ufo.blob import WorkspaceBlobStore
from ufo.flags import flag_enabled
from ufo.harness.o11y import span
from ufo.host.environment import (
    EnvironmentDocument,
    EnvironmentOverrides,
    PromptOverride,
    TextEdit,
    ToolInput,
    ToolOverride,
    load_environment_document,
    load_environment_file,
)
from ufo.host.ext.loader import (
    connector_clis,
    turn_hooks,
    turn_member_skills,
    turn_tools,
    turn_workspace_facts,
    workspace_slot_source,
)
from ufo.host.spawn_catalog import (
    SpawnTarget,
    spawn_catalog_skill,
    spawn_payload_description,
    spawn_targets,
)
from ufo.host.tools.builtins import SPAWN_TOOL
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.email import EmailSends
from ufo.runtime.ext.context import ConversationProbes, ExtensionContext, TurnInvoker
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.ext.surface import TurnTailer
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.objects import BoundAction, ObjectVerbs
from ufo.runtime.prompts.render import (
    OBJECT_KINDS_SECTION,
    WORKSPACE_FACTS_SECTION,
    RenderedPrompt,
    render_object_kinds,
    render_system_prompt,
    render_workspace_facts,
    rendered_prompt,
)
from ufo.runtime.queue import (
    AssembledTurn,
    AssembleRequest,
    EnvironmentFile,
    _agent_actions,
    _agent_tools,
    _member_skill_block,
    _prompt_skill_index,
    _subagent_actions,
    _subagent_tools,
    _with_action_verbs,
    _without_workspace_skills,
)
from ufo.runtime.search import SearchProvider
from ufo.runtime.skills.runtime import (
    SKILL_MD,
    LoadedSkill,
    SkillCard,
    SkillMaterializer,
    SkillRegistry,
    parse_skill_content,
)
from ufo.runtime.skills.selection import MemberVisibility, member_visibility
from ufo.runtime.subagents import FINISH_CONTRACT, subagent_system_prompt
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ToolDef, ToolRegistry
from ufo.runtime.tools.tasks import run_task
from ufo.runtime.turns.audience import Audience
from ufo.runtime.workspace import ws_current

RUN_INPUT_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


@dataclass(frozen=True)
class HostEnvironment:
    """The default `TurnEnvironment`: compose each turn's prompt, tools, and skills from the
    discovered manifests' contributions and the agent's grants. A composition root constructs one
    from the deploy's discovered manifests and hands it to the runtime, which never reaches back
    into this layer."""

    manifests: tuple[Manifest, ...]
    credentials: CredentialStore | None
    index: IndexBackend | None = None
    embed: EmbedClient | None = None
    search: SearchProvider | None = None
    blob: WorkspaceBlobStore | None = None
    tailer: TurnTailer | None = None
    public_base_url: str | None = None
    home_surface: str | None = None
    artifact_token_secret: str = ""
    email: EmailSends | None = None
    """The deploy's one-message send seam, which a tool recording what a member asked to stop
    hearing reaches through its context. None where no control service is configured, and such a
    tool then fails loud rather than telling a member their preference was kept."""
    invoker_for: Callable[[UUID], TurnInvoker] | None = None
    """The internal turn seam an extension tool reaches through its context — the same factory the
    jobs role and the delivery sweep hold, bound to the turn's workspace at assembly. A tool that
    admits a turn elsewhere (a notification delivered into a member's own conversation) needs it;
    a deploy that wires none leaves every tool context without one, and such a tool fails loud."""
    probes: ConversationProbes | None = None

    async def assemble(self, request: AssembleRequest) -> AssembledTurn:
        turn, agent, profile = request.turn, request.agent, request.profile
        document: EnvironmentDocument | None = None
        if request.environment is not None:
            document = await load_environment_document(self._document_blob(), request.environment)
        all_tools, tool_ext, verbs = self.tools(audience=request.audience)
        withheld = await flags_reading_off(all_tools, verbs.actions)
        all_tools = tuple(tool for tool in all_tools if tool.flag not in withheld)
        hooks = self.hooks(audience=request.audience)
        member_cards: tuple[SkillCard, ...] = ()
        materialize_member: SkillMaterializer = _without_workspace_skills
        if agent.use_workspace_skills:
            member_cards, materialize_member = await self.member_skills(agent_name=agent.name)
        targets = await spawn_targets(request.subagents, request.audience)
        skills = request.skills.merged_with((spawn_catalog_skill(targets),)).with_member(
            member_cards, materialize_member
        )
        sections = tuple(
            (section.name, section.body)
            for manifest in self.manifests
            for section in manifest.prompt_sections
        )
        held = render_workspace_facts(
            await turn_workspace_facts(self.manifests, audience=request.audience)
        )
        if held:
            sections = (*sections, (WORKSPACE_FACTS_SECTION, held))
        preload: tuple[LoadedSkill, ...] = ()
        view: MemberVisibility | None = None
        if profile is None:
            granted_actions = granted_without_flagged(
                _agent_actions(
                    verbs.actions, agent.tools, turn.admission_source, turn.speaker_member_id
                ),
                verbs.actions,
                withheld,
            )
            selected = _with_action_verbs(
                _agent_tools(all_tools, agent.tools, turn.admission_source, turn.speaker_member_id),
                all_tools,
                granted_actions,
            )
            kinds = render_object_kinds(_object_kind_index(verbs, granted_actions))
            if kinds:
                sections = (*sections, (OBJECT_KINDS_SECTION, kinds))
            skills = _skills_with_document(skills, document)
            cards = tuple(skills.member_cards.values())
            view = member_visibility(turn.inbound, cards)
            member_skill_block = _member_skill_block(turn, view, request.member_block)
            prompt = render_system_prompt(
                agent.prompt,
                sections,
                skills=_prompt_skill_index(skills, request.member_block),
                knowledge_cutoff=request.knowledge_cutoff,
                context_window=request.context_window,
            )
        else:
            skills = _skills_with_document(skills, document)
            profile_grants = request.subagent_grants.get(profile.name, frozenset())
            preload = await skills.materialize(skills.closure(*request.preload_names))
            cards = tuple(skills.member_cards.values())
            member_skill_block = _member_skill_block(
                turn, member_visibility(turn.inbound, cards), request.member_block
            )
            prompt = rendered_prompt(
                subagent_system_prompt(
                    profile,
                    skills=_prompt_skill_index(skills, request.member_block),
                    preload=preload,
                    context_window=request.context_window,
                )
            )
            granted_actions = granted_without_flagged(
                _subagent_actions(verbs.actions, profile, profile_grants), verbs.actions, withheld
            )
            selected = _with_action_verbs(
                _subagent_tools(all_tools, profile, profile_grants),
                all_tools,
                granted_actions,
                discovery=not profile.isolated_tools,
            )
        tools = ToolRegistry(_with_spawn_payload(selected, targets))
        prompt_replaced = False
        files: tuple[EnvironmentFile, ...] = ()
        if document is not None:
            with span("environment.overrides"):
                scoped = document.main if profile is None else document.profiles.get(profile.name)
                prompt, tools, prompt_replaced = _applied_document(
                    prompt, tools, scoped, document.tools
                )
                seeded: list[EnvironmentFile] = []
                for path, digest in document.files.items():
                    content = await load_environment_file(self._document_blob(), digest)
                    seeded.append(EnvironmentFile(path=path, content=content))
                files = tuple(seeded)
        if turn.spawned and (profile is None or prompt_replaced):
            prompt = rendered_prompt(f"{prompt.content}\n\n{FINISH_CONTRACT}")
        return AssembledTurn(
            system_prompt=prompt,
            tools=tools,
            tool_ext=tool_ext,
            verbs=verbs,
            granted_actions=granted_actions,
            hooks=hooks,
            skills=skills,
            preload=preload,
            files=files,
            member_skill_block=member_skill_block,
            cards=cards,
            view=view,
        )

    def tools(
        self, *, audience: Audience
    ) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]:
        return turn_tools(
            self.manifests,
            self.credentials,
            self.index,
            self.embed,
            audience=audience,
            public_base_url=self.public_base_url,
            home_surface=self.home_surface,
            artifact_token_secret=self.artifact_token_secret,
            member_context_blob=self.blob,
            email=self.email,
            invoker=(
                None if self.invoker_for is None else self.invoker_for(ws_current().workspace_id)
            ),
            probes=self.probes,
        )

    def hooks(self, *, audience: Audience) -> HookChain:
        return turn_hooks(
            self.manifests,
            self.credentials,
            self.index,
            self.embed,
            self.tailer,
            audience=audience,
            public_base_url=self.public_base_url,
            search=self.search,
        )

    async def member_skills(
        self, *, agent_name: str
    ) -> tuple[tuple[SkillCard, ...], SkillMaterializer]:
        return await turn_member_skills(
            self.manifests,
            self.credentials,
            self.index,
            self.embed,
            agent_name=agent_name,
        )

    async def environment_model(self, environment: str, profile: str | None) -> str | None:
        """The document's model choice for one target, None when its block names none."""
        document = await load_environment_document(self._document_blob(), environment)
        scoped = document.main if profile is None else document.profiles.get(profile)
        return None if scoped is None else scoped.model

    def clis(self) -> dict[str, CliCredential]:
        return connector_clis(self.manifests)

    def slots(self) -> WorkspaceSlots:
        return workspace_slot_source(self.manifests)

    def _document_blob(self) -> WorkspaceBlobStore:
        if self.blob is None:
            raise RuntimeError("the host holds no blob store to load environment documents")
        return self.blob


def _object_kind_index(
    verbs: ObjectVerbs, granted_actions: frozenset[str]
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Every registered kind, lexical by name, with its description and the actions this turn
    holds — each action named with the binding it acts on, so the prompt says whether a call takes
    an instance or the collection."""
    return tuple(
        (
            name,
            bound.kind.description,
            tuple(
                f"{short_name} ({entry.action.bound.binding})"
                for short_name, entry in sorted(verbs.actions.get(name, {}).items())
                if entry.action.bound is not None and entry.action.canonical_id in granted_actions
            ),
        )
        for name, bound in sorted(verbs.registry.items())
    )


def _skills_with_document(
    skills: SkillRegistry, document: EnvironmentDocument | None
) -> SkillRegistry:
    """The deploy skill tier with the document's `skills` applied: a known name is replaced by the
    given `SKILL.md` (or has `replace` edits applied to its existing one), keeping the skill's
    bundled files; an unknown name is added whole, and edits against one fail loud. A replaced or
    added name leaves the bundled set so the sandbox mounts its live content instead of the
    image's copy. A member-authored skill is the member's, never a document's to change."""
    if document is None or not document.skills:
        return skills
    by_name = dict(skills.by_name)
    for name, entry in document.skills.items():
        if name in skills.member_cards and name not in by_name:
            raise ValueError(f"environment skills cannot override member skill {name!r}")
        existing = by_name.get(name)
        if isinstance(entry, str):
            text = entry
        elif existing is None:
            raise ValueError(f"environment skills edit a skill the deploy does not hold: {name!r}")
        else:
            text = _edited(existing.raw_skill_md, entry.replace, f"skill {name!r}")
        files = {SKILL_MD: text.encode(), **(dict(existing.files) if existing else {})}
        by_name[name] = parse_skill_content(
            name.rsplit("/", 1)[-1],
            files,
            registry_name=name,
            parent=existing.parent if existing else None,
        )
    bundled = (skills.bundled_names or frozenset()) - set(document.skills)
    return replace(skills, by_name=by_name, bundled_names=bundled)


def _applied_document(
    prompt: RenderedPrompt,
    tools: ToolRegistry,
    scoped: EnvironmentOverrides | None,
    global_tools: dict[str, ToolOverride],
) -> tuple[RenderedPrompt, ToolRegistry, bool]:
    """One target's assembled prompt and offer, reshaped by its document block. Scoped entries fail
    loud on a name the offer does not hold; top-level entries apply wherever the name is offered,
    and a top-level `run` tool joins every offer."""
    by_name = {tool.name: tool for tool in tools.tools}
    if scoped is not None:
        unknown = sorted(
            name
            for name, override in scoped.tools.items()
            if name not in by_name and override.run is None
        )
        if unknown:
            raise ValueError(
                f"environment overrides name tools the turn does not offer: {', '.join(unknown)}"
            )
    merged = {
        name: override
        for name, override in global_tools.items()
        if override.run is not None or name in by_name
    }
    if scoped is not None:
        merged.update(scoped.tools)
    for name, override in merged.items():
        if not override.enabled:
            by_name.pop(name, None)
            continue
        if override.run is not None:
            existing = by_name.get(name)
            description = override.description or (
                existing.description if existing is not None else None
            )
            if description is None:
                raise ValueError(
                    f"environment tool {name!r} adds a command and needs a description"
                )
            by_name[name] = _run_tool(name, description, override.input, override.run)
            continue
        current = by_name[name]
        by_name[name] = replace(
            current,
            description=override.description or current.description,
            input_model=(
                _described_model(current.input_model, name, override.parameters)
                if override.parameters
                else current.input_model
            ),
        )
    prompt_replaced = False
    if scoped is not None and scoped.prompt is not None:
        prompt = _applied_prompt(prompt.content, scoped.prompt)
        prompt_replaced = scoped.prompt.text is not None
    return prompt, ToolRegistry(tuple(by_name.values())), prompt_replaced


def _applied_prompt(content: str, override: PromptOverride) -> RenderedPrompt:
    if override.text is not None:
        return rendered_prompt(override.text)
    return rendered_prompt(_edited(content, override.replace, "prompt"))


def _edited(content: str, edits: tuple[TextEdit, ...], subject: str) -> str:
    for edit in edits:
        occurrences = content.count(edit.old)
        if occurrences != 1:
            raise ValueError(
                f"{subject} edit must match exactly once, found {occurrences}: {edit.old[:80]!r}"
            )
        content = content.replace(edit.old, edit.new)
    return content


def _with_spawn_payload(
    selected: tuple[ToolDef, ...], targets: tuple[SpawnTarget, ...]
) -> tuple[ToolDef, ...]:
    """`spawn` with this turn's targets and their payload keys in the field that takes them. The
    keys are workspace state, so they cannot be written into a static schema; they are read for
    the catalog skill anyway, and the description is where a caller writing the call is already
    looking."""
    return tuple(
        replace(
            tool,
            input_model=_described_model(
                tool.input_model, tool.name, {"payload": spawn_payload_description(targets)}
            ),
        )
        if tool.name == SPAWN_TOOL
        else tool
        for tool in selected
    )


def _described_model(
    model: type[BaseModel], tool: str, parameters: dict[str, str]
) -> type[BaseModel]:
    unknown = sorted(set(parameters) - set(model.model_fields))
    if unknown:
        raise ValueError(
            f"environment overrides name parameters {tool!r} does not take: {', '.join(unknown)}"
        )
    fields: dict[str, Any] = {}
    for name, text in parameters.items():
        info = copy.deepcopy(model.model_fields[name])
        info.description = text
        fields[name] = (info.annotation, info)
    return create_model(model.__name__, __base__=model, **fields)


def _run_tool(name: str, description: str, inputs: dict[str, ToolInput], run: str) -> ToolDef:
    fields: dict[str, Any] = {
        field: (
            RUN_INPUT_TYPES[spec.type] if spec.required else RUN_INPUT_TYPES[spec.type] | None,
            Field(description=spec.description)
            if spec.required
            else Field(default=None, description=spec.description),
        )
        for field, spec in inputs.items()
    }
    input_model = create_model(f"EnvironmentRun_{name}", **fields)

    async def handler(ctx: ToolContext, payload: BaseModel) -> ToolResult:
        started = await run_task(ctx, _run_command(run, payload), None, model_authored=False)
        result = started.result
        output = result.stdout + result.stderr
        if result.timed_out_after_s is not None:
            return ToolResult(
                content=(TextContent(text=f"timed out after {result.timed_out_after_s}s"),),
                is_error=True,
            )
        if result.exit_code == 0:
            return ToolResult(content=(TextContent(text=output),))
        notice = f"exit code: {result.exit_code}"
        return ToolResult(
            content=(TextContent(text=f"{output}\n{notice}" if output else notice),),
            is_error=True,
        )

    return ToolDef(
        name=name,
        description=description,
        input_model=input_model,
        handler=handler,
        side_effecting=True,
    )


def _run_command(run: str, payload: BaseModel) -> str:
    pairs = []
    for name, value in payload.model_dump(mode="json").items():
        if value is None:
            continue
        text = json.dumps(value) if isinstance(value, bool) else str(value)
        pairs.append(f"INPUT_{name.upper()}={shlex.quote(text)}")
    quoted = shlex.quote(run)
    return f"env {' '.join(pairs)} sh -c {quoted}" if pairs else f"sh -c {quoted}"


async def flags_reading_off(
    tools: tuple[ToolDef, ...], actions: Mapping[str, Mapping[str, BoundAction]]
) -> frozenset[str]:
    """The flags the catalog's tools and actions name that read off for the bound workspace. A
    flagged tool is offered by its flag, so a read of off — or no answer — takes it out of the
    catalog the model sees and out of the grants the turn holds, the way a flag hides a screen: the
    model never meets a verb it cannot use, and nothing is refused."""
    declared = sorted(
        {
            flag
            for flag in (
                *(tool.flag for tool in tools),
                *(bound.action.flag for held in actions.values() for bound in held.values()),
            )
            if flag is not None
        }
    )
    reads = await asyncio.gather(*(flag_enabled(flag, default=False) for flag in declared))
    return frozenset(flag for flag, on in zip(declared, reads, strict=True) if not on)


def granted_without_flagged(
    granted: frozenset[str],
    actions: Mapping[str, Mapping[str, BoundAction]],
    withheld: frozenset[str],
) -> frozenset[str]:
    flag_of = {
        bound.action.canonical_id: bound.action.flag
        for held in actions.values()
        for bound in held.values()
    }
    return frozenset(action for action in granted if flag_of.get(action) not in withheld)
