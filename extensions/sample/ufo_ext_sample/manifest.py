"""The conformance sample: a real installed extension that exercises the whole public seam.

It imports only `ufo.sdk` — the surface a CI gate pins — and its entry point returns a Manifest
declaring exactly the landed points: one tool, one job, one route, one credential slot carrying a
wire-injection target, one onboarding step, one typed subagent profile, one hub backend, one
terminal transport, and one contributed skill (a `SKILL.md` plus a bundled script under `skills/`).
Each handler records the
call it received through its own `ExtensionContext.store` (durable `ext_store` rows, never a mock
log), so the tests read those rows back through the same public surfaces core writes them by.
`UNDECLARED_SLOT` names a slot the Manifest never declares — the probe that a handler asking for an
undeclared slot is refused."""

from pathlib import Path

import sqlalchemy as sa

from ufo.sdk.authproxy import AuthProxySpec
from ufo.sdk.connectors import ConnectorBroker
from ufo.sdk.deploy import CommandSpec, DeployRouteSpec
from ufo.sdk.hub import InProcessHub
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import (
    SCHEDULE_KIND,
    SETUP_TOOLS,
    AgentProvision,
    AgentSetup,
    AgentSpec,
    CdpProviderSpec,
    CensusSpec,
    ConnectorProvider,
    ContextBoundarySpec,
    ConversationSlotProvider,
    CredentialSlot,
    EmbedBackendSpec,
    FlagProviderSpec,
    FlagSpec,
    HookSpec,
    HubSpec,
    IndexBackendSpec,
    InjectionTarget,
    Manifest,
    MemorySearchProviderSpec,
    MetricSpec,
    OnboardingStep,
    PromptSection,
    RouteSpec,
    SearchProviderSpec,
    SetupCadence,
    SetupCredential,
    SetupSchedule,
    SkillSpec,
    SourceProvider,
    SubagentProfile,
    SubagentToolGrant,
    TerminalTransportSpec,
    WorkspaceChanges,
    WorkspaceFact,
)
from ufo.sdk.models import ModelSpec, ReasoningSupport
from ufo.sdk.objects import WORKSPACE_KIND, ObjectKind
from ufo.sdk.operator import OperatorRuleSpec
from ufo.sdk.sandbox import CarrierSpec
from ufo.sdk.spend import SpendGateSpec
from ufo.sdk.surfaces import AskUserInput, SurfaceRoute, SurfaceSpec
from ufo.sdk.terminal import Terminals
from ufo.sdk.tools import ActionPresentation, ObjectBinding, ToolDef
from ufo.sdk.workspaces import WorkspaceFoundedSpec
from ufo_ext_sample.auth_proxies import AUTH_PROXY_BACKEND, SampleAuthProxy
from ufo_ext_sample.carriers import CARRIER_NAME, SampleCarrier
from ufo_ext_sample.cdp import CDP_PROVIDER, SampleCdpProvider
from ufo_ext_sample.census import (
    NOTED_STAGE,
    NOTED_STEP,
    NOTER_CHATTED_STEP,
    first_noted,
    first_noter_turn,
)
from ufo_ext_sample.connectors import (
    CONNECTOR_EXECUTE_TOOL_NAME,
    CONNECTOR_LABEL,
    CONNECTOR_PROVIDER,
    ConnectorExecuteInput,
    SampleBroker,
    SampleConnectorOAuth,
    connector_execute,
)
from ufo_ext_sample.context_boundaries import (
    CONTEXT_STRATEGY,
    CONTEXT_WINDOW_PROMPT,
    build_sample_boundary,
)
from ufo_ext_sample.conversation_slots import (
    CONVERSATION_SLOT,
    conversation_slot_read,
    conversation_slot_summary,
)
from ufo_ext_sample.deploy import (
    FLAGS_COMMAND,
    FLEET_PATH,
    MEMBERSHIPS_COMMAND,
    SEAT_PATH,
    FlagsParams,
    MembershipsParams,
    deploy_flags,
    fleet,
    memberships,
    seat,
)
from ufo_ext_sample.embeds import EMBED_BACKEND, SampleEmbed
from ufo_ext_sample.flags import FLAG_BACKEND, PROBE_FLAG, PROBE_FLAG_WHAT, build_flag_provider
from ufo_ext_sample.hooks import (
    _record_page_change,
    bless_failure,
    bless_fold,
    bless_replace,
    deny_echo,
    record_post,
    record_post_compact,
    record_post_failure,
    record_pre_compact,
    record_stop,
)
from ufo_ext_sample.indexes import INDEX_BACKEND, SampleIndex
from ufo_ext_sample.jobs import JOB_NAME, tick
from ufo_ext_sample.members import MEMBER_ADDED
from ufo_ext_sample.memory_search import MEMORY_SEARCH_PROVIDER, SampleMemorySearch
from ufo_ext_sample.metrics import (
    CALL_ACTIVE_METRIC,
    CALL_DIMENSION,
    CALL_LATENCY_METRIC,
    CALL_METRIC,
)
from ufo_ext_sample.models import (
    MODEL_PROVIDER_NAME,
    SAMPLE_MODEL,
    SAMPLE_MODEL_PRICE,
    SampleModelClient,
)
from ufo_ext_sample.objects import (
    AUDIT_ACTION,
    BESEECH_ACTION,
    BLESS_ACTION,
    BLESS_CANONICAL_ID,
    CALIBRATE_ACTION,
    DIVINE_ACTION,
    ENGRAVE_ACTION,
    ENGRAVE_PRESENTATION_CONFIRM,
    ENGRAVE_PRESENTATION_LABEL,
    POLISH_ACTION,
    RELIC_GUIDANCE,
    RELIC_KIND,
    WIDGET_GUIDANCE,
    WIDGET_KIND,
    AuditInput,
    BeseechInput,
    BlessInput,
    CalibrateInput,
    DivineInput,
    EngraveInput,
    PolishInput,
    RelicSpec,
    RelicStore,
    WidgetSpec,
    WidgetStore,
    audit,
    beseech,
    bless,
    calibrate,
    divine,
    engrave,
    polish,
)
from ufo_ext_sample.onboarding import ONBOARDING_NAME, setup
from ufo_ext_sample.operator import OPERATOR_RULE, SampleOperatorRule
from ufo_ext_sample.provisioning import record_founding
from ufo_ext_sample.routes import ROUTE_PATH, hook, resolve_workspace
from ufo_ext_sample.search import SEARCH_PROVIDER, SampleSearchProvider
from ufo_ext_sample.sources import SOURCE_BACKEND, SampleSource
from ufo_ext_sample.spend import SPEND_GATE, SampleGate
from ufo_ext_sample.subagents import SUBAGENT_NAME, ProbeFinding, ProbeTask
from ufo_ext_sample.surfaces import (
    SURFACE_LIVE_NAME,
    SURFACE_LIVE_PATH,
    SURFACE_LIVE_STREAM_PATH,
    SURFACE_MODEL_PATH,
    SURFACE_NAME,
    resolve_surface_workspace,
    surface_attach,
    surface_ingest,
    surface_live_admit,
    surface_live_stream,
    surface_post,
    surface_served_model,
)
from ufo_ext_sample.tools import (
    NOTE_TABLE,
    NOTE_TOOL_NAME,
    TOOL_NAME,
    EchoInput,
    NoteInput,
    echo,
    note,
)
from ufo_ext_sample.workspace_facts import (
    WORKSPACE_FACT_LINE,
    WORKSPACE_FACT_NAME,
    workspace_fact_held,
)

NAME = "sample"
VERSION = "0.1.0"
PROVISIONED_AGENT_NAME = "sample-probe-agent"
PROVISIONED_AGENT_PROMPT = "Probe agent: answer from the workspace's own records."
PROVISIONED_AGENT_PURPOSE = "Answers questions about what this workspace has recorded."
API_CREDENTIAL_LABEL = "Sample API key"
PROVISIONED_AGENT_SCHEDULE = SetupSchedule(
    name="sample-sweep",
    prompt="Read what arrived since the last sweep and note anything new.",
    cadences=(SetupCadence(), SetupCadence(hour=9), SetupCadence(hour=9, weekdays=(1, 2, 3, 4, 5))),
)
PROVISIONED_AGENT_SETUP = "Connect the sample account, then add its source for this agent."
API_SLOT = "sample_api"
DEPLOY_TOKEN_ENV = "UFO_SAMPLE_DEPLOY_TOKEN"
UNDECLARED_SLOT = "sample_unset"
INJECTION_HOST = "api.sample.test"
INJECTION_HEADER = "authorization"
INJECTION_SENTINEL = "Bearer sentinel-sample-key"
INJECTION_DIMENSION = "requests"
HUB_BACKEND = "sample_hub"
TERMINAL_BACKEND = "sample_terminal"
SECTION_NAME = "sample_capability"
SECTION_BODY = (Path(__file__).parent / "prompts" / "sample_capability.md").read_text().strip()
SKILL_NAME = "sample_skill"
SKILL_SCRIPT = "probe.py"
SKILL_SCRIPT_MARKER = "sample-skill-probe-ok"
SKILL_DIR = Path(__file__).parent / "skills" / SKILL_NAME


def manifest() -> Manifest:
    broker: ConnectorBroker = SampleBroker()
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name=TOOL_NAME,
                description="Echo a message, recording it through the extension's scoped store.",
                input_model=EchoInput,
                handler=echo,
                binds_member_authority=False,
            ),
            ToolDef(
                name=NOTE_TOOL_NAME,
                description="Write and read a note in the sample's own migration-created table.",
                input_model=NoteInput,
                handler=note,
                binds_member_authority=False,
            ),
            ToolDef(
                name=AUDIT_ACTION,
                description="Audit the workspace's probe records.",
                input_model=AuditInput,
                handler=audit,
                bound=ObjectBinding(kind=WORKSPACE_KIND, binding="collection"),
                parallel_safe=True,
                binds_member_authority=False,
            ),
            ToolDef(
                name=POLISH_ACTION,
                description="Polish one probe widget — the agent-targetable action.",
                input_model=PolishInput,
                handler=polish,
                bound=ObjectBinding(kind=WIDGET_KIND, binding="instance"),
                agent_targetable=True,
            ),
            ToolDef(
                name=ENGRAVE_ACTION,
                description="Engrave one probe widget — an external write that dedups on its key.",
                input_model=EngraveInput,
                handler=engrave,
                bound=ObjectBinding(kind=WIDGET_KIND, binding="instance"),
                side_effecting=True,
                binds_member_authority=False,
                presentation=ActionPresentation(
                    label=ENGRAVE_PRESENTATION_LABEL,
                    confirm=ENGRAVE_PRESENTATION_CONFIRM,
                    frame=True,
                ),
            ),
            ToolDef(
                name=DIVINE_ACTION,
                description="Read the widgets' divination — third-party text, walled as data.",
                input_model=DivineInput,
                handler=divine,
                bound=ObjectBinding(kind=WIDGET_KIND, binding="collection"),
                untrusted=True,
                parallel_safe=True,
                binds_member_authority=False,
            ),
            ToolDef(
                name=CALIBRATE_ACTION,
                description="Calibrate the probe widgets — a profile-held primitive.",
                input_model=CalibrateInput,
                handler=calibrate,
                bound=ObjectBinding(kind=WIDGET_KIND, binding="collection"),
                profile_only=True,
                binds_member_authority=False,
            ),
            ToolDef(
                name=BLESS_ACTION,
                description="Bless one probe widget — the hook-targeted action.",
                input_model=BlessInput,
                handler=bless,
                bound=ObjectBinding(kind=WIDGET_KIND, binding="instance"),
                binds_member_authority=False,
            ),
            ToolDef(
                name=BESEECH_ACTION,
                description="Ask the member a question about the probe widgets — a final act.",
                input_model=BeseechInput,
                handler=beseech,
                bound=ObjectBinding(kind=WIDGET_KIND, binding="collection"),
                final_act_model=AskUserInput,
                binds_member_authority=False,
            ),
        ),
        objects=(
            ObjectKind(
                name=WIDGET_KIND,
                description=(
                    "Probe widgets: full CRUD through the object verbs; delete is admin-only."
                ),
                guidance=WIDGET_GUIDANCE,
                spec_model=WidgetSpec,
                store=WidgetStore(),
                list_fields=frozenset({"color", "size"}),
                agent_target_verbs=frozenset({"get"}),
            ),
            ObjectKind(
                name=RELIC_KIND,
                description="Probe relics: read-only; every mutation is refused.",
                guidance=RELIC_GUIDANCE,
                spec_model=RelicSpec,
                store=RelicStore(),
            ),
        ),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=None,
                handler=tick,
                candidates=owner_candidates(
                    lambda: sa.select(NOTE_TABLE.c.workspace_id).distinct()
                ),
            ),
        ),
        routes=(
            RouteSpec(method="POST", path=ROUTE_PATH, handler=hook, identify=resolve_workspace),
        ),
        onboarding_steps=(OnboardingStep(name=ONBOARDING_NAME, handler=setup),),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        workspace_facts=(
            WorkspaceFact(
                name=WORKSPACE_FACT_NAME,
                line=WORKSPACE_FACT_LINE,
                holds=workspace_fact_held,
            ),
        ),
        agents=(
            AgentProvision(
                name=PROVISIONED_AGENT_NAME,
                spec=AgentSpec(
                    model="auto",
                    reasoning="auto",
                    internet_access_allowed=False,
                    prompt=PROVISIONED_AGENT_PROMPT,
                    purpose=PROVISIONED_AGENT_PURPOSE,
                ),
                tools=(TOOL_NAME, *SETUP_TOOLS),
                setup=AgentSetup(
                    connectors=(CONNECTOR_PROVIDER,),
                    credentials=(SetupCredential(label=API_CREDENTIAL_LABEL, slots=(API_SLOT,)),),
                    standing=(SCHEDULE_KIND,),
                    schedule=PROVISIONED_AGENT_SCHEDULE,
                    instructions=PROVISIONED_AGENT_SETUP,
                ),
            ),
        ),
        subagents=(
            SubagentProfile(
                name=SUBAGENT_NAME,
                prompt="Probe subagent: restate the task as a finding.",
                tool_names=(TOOL_NAME,),
                input_model=ProbeTask,
                output_model=ProbeFinding,
            ),
        ),
        subagent_tool_grants=(
            SubagentToolGrant(profile=SUBAGENT_NAME, tool_names=(NOTE_TOOL_NAME,)),
        ),
        credentials=(
            CredentialSlot(
                name=API_SLOT,
                description="BYOK key the egress proxy swaps onto the sample host.",
                injection=InjectionTarget(
                    host=INJECTION_HOST,
                    header=INJECTION_HEADER,
                    sentinel=INJECTION_SENTINEL,
                    dimension=INJECTION_DIMENSION,
                ),
            ),
        ),
        connectors=(
            ConnectorProvider(
                oauth=SampleConnectorOAuth(),
                label=CONNECTOR_LABEL,
                broker=broker,
                tools=(
                    ToolDef(
                        name=CONNECTOR_EXECUTE_TOOL_NAME,
                        description="Resolve the bound connected-account id for server-side exec.",
                        input_model=ConnectorExecuteInput,
                        handler=connector_execute,
                        side_effecting=True,
                        subagent_default=True,
                    ),
                ),
            ),
        ),
        hooks=(
            HookSpec(event="pre_tool_use", handler=deny_echo, tools=(TOOL_NAME,)),
            HookSpec(event="pre_tool_use", handler=bless_fold, tools=(BLESS_CANONICAL_ID,)),
            HookSpec(event="post_tool_use", handler=bless_replace, tools=(BLESS_CANONICAL_ID,)),
            HookSpec(
                event="post_tool_use_failure",
                handler=bless_failure,
                tools=(BLESS_CANONICAL_ID,),
            ),
            HookSpec(event="post_tool_use", handler=record_post),
            HookSpec(event="post_tool_use_failure", handler=record_post_failure),
            HookSpec(event="stop", handler=record_stop),
            HookSpec(event="pre_compact", handler=record_pre_compact),
            HookSpec(event="post_compact", handler=record_post_compact),
            HookSpec(event="page_change", handler=_record_page_change),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_NAME,
                routes=(
                    SurfaceRoute(method="POST", path="", handler=surface_ingest),
                    SurfaceRoute(
                        method="GET", path=SURFACE_MODEL_PATH, handler=surface_served_model
                    ),
                ),
                post=surface_post,
                attach=surface_attach,
                identify=resolve_surface_workspace,
            ),
            SurfaceSpec(
                name=SURFACE_LIVE_NAME,
                routes=(
                    SurfaceRoute(method="POST", path=SURFACE_LIVE_PATH, handler=surface_live_admit),
                    SurfaceRoute(
                        method="GET",
                        path=SURFACE_LIVE_STREAM_PATH,
                        handler=surface_live_stream,
                    ),
                ),
                identify=resolve_surface_workspace,
            ),
        ),
        sources=(
            SourceProvider(backend=SOURCE_BACKEND, build=lambda _credentials: SampleSource()),
        ),
        indexes=(IndexBackendSpec(name=INDEX_BACKEND, factory=lambda ctx: SampleIndex()),),
        embeds=(EmbedBackendSpec(name=EMBED_BACKEND, factory=lambda ctx: SampleEmbed()),),
        models=(
            ModelSpec(
                id=SAMPLE_MODEL,
                provider=MODEL_PROVIDER_NAME,
                client=lambda spec, key: SampleModelClient(model=spec.id),
                price=SAMPLE_MODEL_PRICE,
                knowledge_cutoff="2026-01",
                context_window=200_000,
                reasoning=ReasoningSupport(supported=True, tools_with_reasoning=True),
                api_surface="chat",
            ),
        ),
        context_boundaries=(
            ContextBoundarySpec(
                strategy=CONTEXT_STRATEGY,
                build=build_sample_boundary,
                tools=("get_context_remaining",),
                prompt=CONTEXT_WINDOW_PROMPT,
            ),
        ),
        hubs=(HubSpec(backend=HUB_BACKEND, build=lambda _url: InProcessHub()),),
        terminal_transports=(
            TerminalTransportSpec(backend=TERMINAL_BACKEND, build=lambda _url, _blob: Terminals()),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
        cdp_providers=(
            CdpProviderSpec(backend=CDP_PROVIDER, build=lambda credentials: SampleCdpProvider()),
        ),
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=SampleCarrier),),
        auth_proxies=(
            AuthProxySpec(backend=AUTH_PROXY_BACKEND, build=lambda credentials: SampleAuthProxy()),
        ),
        search_providers=(
            SearchProviderSpec(
                backend=SEARCH_PROVIDER, build=lambda credentials: SampleSearchProvider()
            ),
        ),
        flag_providers=(FlagProviderSpec(backend=FLAG_BACKEND, build=build_flag_provider),),
        flags=(FlagSpec(key=PROBE_FLAG, what=PROBE_FLAG_WHAT),),
        memory_search=(
            MemorySearchProviderSpec(name=MEMORY_SEARCH_PROVIDER, build=SampleMemorySearch),
        ),
        conversation_slots=(
            ConversationSlotProvider(
                id=CONVERSATION_SLOT,
                label="Sample changes",
                icon="diff",
                content=WorkspaceChanges,
                summarize=conversation_slot_summary,
                read=conversation_slot_read,
            ),
        ),
        workspace_founded=(WorkspaceFoundedSpec(handler=record_founding),),
        deploy_routes=(
            DeployRouteSpec(method="POST", path=SEAT_PATH, handler=seat),
            DeployRouteSpec(method="GET", path=FLEET_PATH, handler=fleet),
        ),
        deploy_bearer_env=DEPLOY_TOKEN_ENV,
        deploy_keys=(DEPLOY_TOKEN_ENV,),
        commands=(
            CommandSpec(
                name=MEMBERSHIPS_COMMAND,
                help="Name the workspaces an address is a member of.",
                params=MembershipsParams,
                run=memberships,
            ),
            CommandSpec(
                name=FLAGS_COMMAND,
                help="Name the flag backend this deploy selects and every flag it declares.",
                params=FlagsParams,
                run=deploy_flags,
            ),
        ),
        metrics=(
            MetricSpec(name=CALL_METRIC, kind="counter", dimensions=(CALL_DIMENSION,)),
            MetricSpec(name=CALL_LATENCY_METRIC, kind="histogram", dimensions=(CALL_DIMENSION,)),
            MetricSpec(name=CALL_ACTIVE_METRIC, kind="up_down", dimensions=(CALL_DIMENSION,)),
        ),
        census=(
            CensusSpec(stage=NOTED_STAGE, step=NOTED_STEP, first_at=first_noted),
            CensusSpec(stage=None, step=NOTER_CHATTED_STEP, first_at=first_noter_turn),
        ),
        operator_rules=(OperatorRuleSpec(name=OPERATOR_RULE, build=SampleOperatorRule),),
        member_added=MEMBER_ADDED,
        spend_gates=(SpendGateSpec(name=SPEND_GATE, build=SampleGate),),
    )
