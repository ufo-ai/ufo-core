"""Natural app requests, connected source fixtures, and post-turn browser proof."""

import asyncio
import base64
import fcntl
import json
import os
import re
import shlex
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, model_validator
from ufo_ext_eval_env.manifest import (
    ACCOUNT_ID,
    APP_ACTION_FIXTURE_PREFIX,
    APP_ACTION_KEY_PREFIX,
    APP_ACTION_KIND,
    APP_FIXTURE_PREFIX,
    CALENDAR_HOST,
    CALENDAR_PROVIDER,
    DRIVE_HOST,
    DRIVE_PROVIDER,
    EMAIL_HOST,
    EMAIL_PROVIDER,
    GITHUB_HOST,
    GITHUB_PROVIDER,
    GREENHOUSE_HOST,
    GREENHOUSE_PROVIDER,
    HUBSPOT_HOST,
    HUBSPOT_PROVIDER,
    STRIPE_HOST,
    STRIPE_PROVIDER,
    eval_env_email,
    eval_env_event,
)
from ufo_ext_eval_env.manifest import (
    NAME as EVAL_ENV_NAME,
)
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_REQUEST_CONTRACT_KEY,
    APPLICATION_AUDIT_SERVER,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    DESKTOP_WIDTH,
    MIN_CONTROLS,
    MIN_INTERACTIONS,
    SCHEMES,
    ApplicationAuditContract,
    ApplicationAuditFact,
    ApplicationAuditReport,
    audit_application,
)
from ufo_ext_sites.application_audit import (
    MEASURED_VIEWS as APPLICATION_MEASURED_VIEWS,
)
from ufo_ext_sites.application_audit import (
    NARROW_WIDTH as APPLICATION_NARROW_WIDTH,
)
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_DELEGATION_TOOL,
    APPLICATION_BUILDER_DESIGN_TOOL,
    APPLICATION_BUILDER_EDIT_TOOL,
    APPLICATION_BUILDER_NAME,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_READ_TOOL,
    APPLICATION_BUILDER_WRITE_TOOL,
    ApplicationBuilderResult,
)

from evals.driver import EVAL_SURFACE, WorkspaceDriver
from evals.harness.artifact_checks import ArtifactCheck, valid_png
from evals.harness.capability import (
    ArtifactProbeResult,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ProbeCommandResult,
    SharedArtifact,
    WorkspaceFile,
    WorkspaceProbe,
    grading_statement,
)
from evals.harness.harness import EvalMetric, EvalReport, Json, JsonObject
from evals.harness.registry import EvalTask
from evals.harness.scorers import combine, content_words, skill_scorer
from evals.harness.target import CapabilityTarget
from ufo.access.grants import GrantStore
from ufo.agent_scope import agent
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.sdk.context import ScopedStore
from ufo.workspace import ws

HOUSE_STYLE_SKILL = "ufo-style"
SITE_SKILL = "website-building"
REPO_ROOT = Path(__file__).parents[2]
AUDIT_CONTENT = (
    REPO_ROOT / "extensions/sites/ufo_ext_sites/scripts/audit_application.cjs"
).read_bytes()
COPY_CAPTURE_CONTENT = Path(__file__).with_name("ufo_app_copy_capture.cjs").read_bytes()
AUDIT_DIGEST = sha256(AUDIT_CONTENT).hexdigest()
COPY_CAPTURE_DIGEST = sha256(COPY_CAPTURE_CONTENT).hexdigest()
APP_SCAFFOLD_ROOT = REPO_ROOT / "extensions/app_tasks/ufo_ext_app_tasks/skills/app-tasks-home"
APP_WORKSPACE_ROOT = "/workspace/ufo-app"
APP_PREVIEW = rb"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Application preview</title>
<style>html,body,iframe{width:100%;height:100%;margin:0;border:0}body{overflow:hidden}</style>
</head>
<body>
<iframe name="ufo-app" title="Application preview" src="./dist/index.html"></iframe>
<script>
const agent={id:"eval-agent",name:"Assistant",model:"eval",main:true,icon:"user"};
window.__ufoCalls=[];
window.__ufoNavigations=[];
const actionKey="ufo-app-bench-actions";
const actionResult=spec=>({
  "meeting-tasks":"Issue #900 created for priya.",
  "issue-owner":"Issue #521 assigned to alex.",
  "pr-babysitter":"PR #743 babysitter set to Gemini 3.7 Flash."
})[spec.case]||"Prepared action accepted.";
window.addEventListener("message",event=>{
  const message=event.data;
  if(!message||typeof message!=="object")return;
  if(message.ufo==="ready"){
    event.source.postMessage({ufo:"init",member:{email:"evals@localhost",admin:true},agentId:agent.id,place:{},portal:location.origin},"*");
    return;
  }
  if(message.ufo==="navigate"){
    window.__ufoNavigations.push(message.to);
    return;
  }
  if(message.ufo!=="call")return;
  const path=message.path.replace(/^\/+/,"");
  const callBody=typeof message.body==="string"?JSON.parse(message.body):message.body;
  window.__ufoCalls.push({method:message.method,path,body:callBody});
  const actions=JSON.parse(localStorage.getItem(actionKey)||"{}");
  let response={applied:true,message:"Prepared action accepted."};
  if(path==="api/agents"){
    response={agents:[agent],member:{email:"evals@localhost",admin:true}};
  }else if(message.method==="POST"&&path==="objects/eval_app_action"){
    const result=actionResult(callBody.spec);
    actions[callBody.name]={spec:callBody.spec,result};
    localStorage.setItem(actionKey,JSON.stringify(actions));
    response={ok:true,name:callBody.name,detail:result};
  }else if(message.method==="GET"&&path.startsWith("objects/eval_app_action/")){
    const name=decodeURIComponent(path.split("/").pop());
    const stored=actions[name];
    response=stored
      ? {name,spec:stored.spec,status:{state:"applied",result:stored.result}}
      : {error:"not found"};
  }
  const body=JSON.stringify(response);
  event.source.postMessage({ufo:"data",id:message.id,ok:true,status:200,body},"*");
});
</script>
</body>
</html>
"""
APP_PLACEHOLDER = b"""import { mountApp } from "ufo/kit";
mountApp(document.getElementById("root")!, () => <main>Application source is not built.</main>);
"""
APP_WORKSPACE_FILES = (
    WorkspaceFile("ufo-app/index.html", (APP_SCAFFOLD_ROOT / "index.html").read_bytes()),
    WorkspaceFile("ufo-app/app.tsx", APP_PLACEHOLDER),
    WorkspaceFile("ufo-app/preview.html", APP_PREVIEW),
)
APP_DESIGN_PATH = f"{APP_WORKSPACE_ROOT}/application-design.svg"
PROBE_OUTPUT = ".eval-output"
PROBE_PORT = 8137
PROBE_TIMEOUT_SECONDS = 120
DESKTOP_HEIGHT = 900
NARROW_WIDTH = APPLICATION_NARROW_WIDTH
NARROW_HEIGHT = 844
MEASURED_VIEWS = APPLICATION_MEASURED_VIEWS
INTERACTION_MIN_CONTROLS = MIN_CONTROLS
INTERACTION_MIN_SUCCESSES = MIN_INTERACTIONS
WORKFLOW_WAIT_SECONDS = 900.0
SUPPORTED_BACKENDS = ("docker",)
MAX_PREVIEW_SERVER_CALLS = 1
MAX_BROWSER_QA_CALLS = 4
MAX_PRODUCT_QA_CALLS = 3
DEPLOY_TOOLS = ("deploy_website", "deploy_ufo_application", "publish_website")
SOURCE_SENTENCE = re.compile(r"(?<=[.!?])\s+")
SOURCE_COPY_WINDOW_PARTS = 3
SOURCE_COPY_MIN_WORDS = 6
SOURCE_COPY_MIN_SHARED_WORDS = 5
SOURCE_COPY_SHARE_LIMIT = 0.70
BROWSER_PROBE_LOCK = Path(tempfile.gettempdir()) / "ufo-app-browser-probe.lock"
BROWSER_PROBE_LOCK_POLL_SECONDS = 0.05
_BROWSER_PROBE_TASK_LOCK = asyncio.Lock()


def _score_evidence(key: str, passed: int, total: int) -> JsonObject:
    return {f"{key}Passed": passed, f"{key}Total": total}


CONTROL_MEMBER_QUERIES = {
    "kanban-board": "Build your interactive project board homepage for the ops team.",
    "call-notes": "Build your interactive internal notes homepage for one customer call.",
    "daily-brief": "Build your interactive daily brief homepage for the team.",
    "daily-brief-rework": (
        "Build your interactive daily brief homepage for the team. Once it is live, make the "
        "headline row bolder and redeploy the page."
    ),
}


class _ConnectorCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    tool: str


class _Requirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: str
    calls: tuple[_ConnectorCall, ...] = ()
    visible: tuple[str, ...] = ()
    visible_any: tuple[tuple[str, ...], ...] = ()
    rewrite_sources: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _has_proof(self) -> "_Requirement":
        if not (
            self.calls or self.visible or self.visible_any or self.rewrite_sources or self.absent
        ):
            raise ValueError(f"requirement {self.prompt!r} has no proof")
        if any(not alternatives for alternatives in self.visible_any):
            raise ValueError(f"requirement {self.prompt!r} has an empty visible alternative group")
        return self


class _EmailFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    folder: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    body: str
    sent_at: datetime


class _EventFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    start: datetime
    end: datetime
    attendees: tuple[str, ...]
    status: str


class _ConnectedApp(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source_apps: tuple[str, ...]
    request: str
    emails: tuple[_EmailFixture, ...]
    events: tuple[_EventFixture, ...]
    tools: dict[str, dict[str, JsonObject]]
    requirements: tuple[_Requirement, ...]

    @model_validator(mode="after")
    def _rewrite_sources_exist(self) -> "_ConnectedApp":
        for requirement in self.requirements:
            for path in requirement.rewrite_sources:
                source = self._source_text(path)
                segments = tuple(
                    segment
                    for segment in _source_segments(source)
                    if len(content_words(segment)) >= SOURCE_COPY_MIN_WORDS
                    and any(
                        marker.casefold() in segment.casefold() for marker in requirement.absent
                    )
                )
                if not segments:
                    raise ValueError(
                        f"rewrite source {path!r} has no graded sentence for {requirement.absent!r}"
                    )
        return self

    def _source_text(self, path: str) -> str:
        value: Json = self.model_dump(mode="json")
        for part in path.removeprefix("/").split("/"):
            match value:
                case dict():
                    value = value[part]
                case list():
                    value = value[int(part)]
                case _:
                    raise ValueError(f"rewrite source {path!r} stops at {part!r}")
        match value:
            case str():
                return value
            case _:
                raise ValueError(f"rewrite source {path!r} is not text")


class _ConnectedApps(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases: tuple[_ConnectedApp, ...]


class _ActionContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    source_case: str
    label: str
    fixture_tool: str
    kind: str = APP_ACTION_KIND
    spec: JsonObject
    expected_result: str
    expected_fixture: JsonObject

    def connector_value(self) -> JsonObject:
        return {
            "label": self.label,
            "write": {
                "function": "ufoWrite",
                "arguments": [self.kind, self.name, self.spec],
            },
            "read": {
                "function": "ufoRead",
                "arguments": [f"objects/{self.kind}/{self.name}"],
            },
            "success_text": self.expected_result,
            "render": {"component": "ApplicationAction", "prop": "action"},
        }


class _SetupContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_case: str
    fixture_tool: str
    connectors: tuple[str, ...]
    notifications: tuple[str, ...] = ("Chat", "Slack", "iMessage")
    review_label: str = "Review setup in chat"
    navigation: str = "#/new/eval-agent"

    def connector_value(self) -> JsonObject:
        return {
            "application": self.source_case,
            "connectors": [
                {"label": connector, "state": "connected"} for connector in self.connectors
            ],
            "notifications": [
                {"surface": surface, "state": state}
                for surface, state in zip(
                    self.notifications,
                    ("selected", "available", "not connected"),
                    strict=True,
                )
            ],
            "review": {
                "label": self.review_label,
                "navigate": {"function": "ufoNavigate", "arguments": [self.navigation]},
            },
        }


def _source_segments(source: str) -> tuple[str, ...]:
    return tuple(segment.strip() for segment in SOURCE_SENTENCE.split(source) if segment.strip())


APP_DATA_CONTENT = Path(__file__).with_name("ufo_app_bench_data.json").read_bytes()
APP_DATA_DIGEST = sha256(APP_DATA_CONTENT).hexdigest()
CONNECTED_APPS = _ConnectedApps.model_validate_json(APP_DATA_CONTENT).cases
CONNECTED_APP_BY_NAME = {case.name: case for case in CONNECTED_APPS}
ACTION_CONTRACTS = (
    _ActionContract(
        name="create-support-runbook-issue",
        source_case="meeting-tasks",
        label="Create support runbook issue",
        fixture_tool="list_issues",
        spec={
            "case": "meeting-tasks",
            "action": "create_issue",
            "target": "support-runbook",
            "value": "priya",
        },
        expected_result="Issue #900 created for priya.",
        expected_fixture={"issues": [{"number": 900, "owner": "priya"}]},
    ),
    _ActionContract(
        name="assign-issue-521",
        source_case="issue-owner",
        label="Assign issue 521 to Alex",
        fixture_tool="list_issues",
        spec={
            "case": "issue-owner",
            "action": "assign_issue",
            "target": "521",
            "value": "alex",
        },
        expected_result="Issue #521 assigned to alex.",
        expected_fixture={
            "issues": [{"number": 521, "owner": "alex", "project_status": "Assigned"}]
        },
    ),
    _ActionContract(
        name="set-pr-743-babysitter",
        source_case="pr-babysitter",
        label="Use Gemini 3.7 Flash for PR 743",
        fixture_tool="list_pull_requests",
        spec={
            "case": "pr-babysitter",
            "action": "set_babysitter",
            "target": "743",
            "value": "Gemini 3.7 Flash",
        },
        expected_result="PR #743 babysitter set to Gemini 3.7 Flash.",
        expected_fixture={
            "pull_requests": [
                {
                    "number": 743,
                    "babysitter": {
                        "enabled": True,
                        "model": "Gemini 3.7 Flash",
                        "state": "watching",
                    },
                }
            ]
        },
    ),
)
SETUP_CONTRACTS = (
    _SetupContract(
        source_case="meeting-tasks",
        fixture_tool="list_issues",
        connectors=("Google Drive", "GitHub"),
    ),
    _SetupContract(
        source_case="issue-owner",
        fixture_tool="list_issues",
        connectors=("GitHub",),
    ),
    _SetupContract(
        source_case="pr-babysitter",
        fixture_tool="list_pull_requests",
        connectors=("GitHub",),
    ),
)
ACTION_CASE_NAMES = frozenset(
    f"{prefix}-{contract.source_case}"
    for prefix, contracts in (("action", ACTION_CONTRACTS), ("setup", SETUP_CONTRACTS))
    for contract in contracts
)
SETUP_CASE_NAMES = frozenset(f"setup-{contract.source_case}" for contract in SETUP_CONTRACTS)


def _merge_app_tools(cases: tuple[_ConnectedApp, ...]) -> dict[str, dict[str, JsonObject]]:
    merged: dict[str, dict[str, JsonObject]] = {}
    for spec in cases:
        for provider, tools in spec.tools.items():
            provider_tools = merged.setdefault(provider, {})
            for tool, response in tools.items():
                combined = provider_tools.setdefault(tool, {})
                for name, value in response.items():
                    if name not in combined:
                        combined[name] = value
                        continue
                    existing: Json = combined[name]
                    if isinstance(existing, list) and isinstance(value, list):
                        combined[name] = [*existing, *value]
                        continue
                    if existing != value:
                        raise ValueError(
                            f"app fixture conflict at {provider}.{tool}.{name}: "
                            f"{existing!r} != {value!r}"
                        )
    return merged


APP_UNIVERSE_EMAILS = tuple(item for spec in CONNECTED_APPS for item in spec.emails)
APP_UNIVERSE_EVENTS = tuple(item for spec in CONNECTED_APPS for item in spec.events)
APP_UNIVERSE_TOOLS = _merge_app_tools(CONNECTED_APPS)
CONNECTED_MEMBER_QUERIES = {
    case.name: " ".join((case.request, *(item.prompt for item in case.requirements)))
    for case in CONNECTED_APPS
}
ACTION_MEMBER_QUERIES = {
    f"action-{contract.source_case}": (
        f"{CONNECTED_MEMBER_QUERIES[contract.source_case]} "
        "Use the application action contract in the connected GitHub data. Its named control "
        "must apply the exact prepared action, show the returned result, and keep that result "
        "visible after a page reload."
    )
    for contract in ACTION_CONTRACTS
}
MEMBER_QUERIES = {
    **CONTROL_MEMBER_QUERIES,
    **CONNECTED_MEMBER_QUERIES,
    **ACTION_MEMBER_QUERIES,
    **{
        f"setup-{contract.source_case}": (
            f"{ACTION_MEMBER_QUERIES[f'action-{contract.source_case}']} "
            "Include a setup and status view with required connector states and notification "
            "surfaces from connected data. Open chat when I review setup changes."
        )
        for contract in SETUP_CONTRACTS
    },
}
PROVIDER_HOSTS = {
    EMAIL_PROVIDER: EMAIL_HOST,
    CALENDAR_PROVIDER: CALENDAR_HOST,
    DRIVE_PROVIDER: DRIVE_HOST,
    GITHUB_PROVIDER: GITHUB_HOST,
    STRIPE_PROVIDER: STRIPE_HOST,
    HUBSPOT_PROVIDER: HUBSPOT_HOST,
    GREENHOUSE_PROVIDER: GREENHOUSE_HOST,
}


@dataclass(frozen=True)
class _ConnectedAppSeed:
    spec: _ConnectedApp
    request: str

    async def __call__(self, workspace_id: UUID, agent_id: UUID, _blob: WorkspaceBlobStore) -> None:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(eval_env_email).where(eval_env_email.c.workspace_id == workspace_id)
            )
            await connection.execute(
                sa.delete(eval_env_event).where(eval_env_event.c.workspace_id == workspace_id)
            )
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(tables.member.c.workspace_id == workspace_id)
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    member_id=member_id,
                    surface=EVAL_SURFACE,
                    queue_key=f"{EVAL_SURFACE}-app-bench-seed:{conversation_id}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            if APP_UNIVERSE_EMAILS:
                await connection.execute(
                    sa.insert(eval_env_email),
                    [
                        {
                            "id": uuid4(),
                            "workspace_id": workspace_id,
                            "folder": item.folder,
                            "sender": item.sender,
                            "recipients": list(item.recipients),
                            "subject": item.subject,
                            "body": item.body,
                            "sent_at": item.sent_at,
                        }
                        for item in APP_UNIVERSE_EMAILS
                    ],
                )
            if APP_UNIVERSE_EVENTS:
                await connection.execute(
                    sa.insert(eval_env_event),
                    [
                        {
                            "id": uuid4(),
                            "workspace_id": workspace_id,
                            "title": item.title,
                            "start_at": item.start,
                            "end_at": item.end,
                            "attendees": list(item.attendees),
                            "status": item.status,
                        }
                        for item in APP_UNIVERSE_EVENTS
                    ],
                )
        providers = {
            call.provider for requirement in self.spec.requirements for call in requirement.calls
        }
        store = ScopedStore(extension=EVAL_ENV_NAME)
        await ScopedStore(extension="sites").put(
            APPLICATION_AUDIT_REQUEST_CONTRACT_KEY.format(
                request_sha256=sha256(self.request.encode()).hexdigest()
            ),
            _application_audit_contract(self.spec).model_dump(mode="json"),
        )
        for provider in sorted(providers & APP_UNIVERSE_TOOLS.keys()):
            tools = APP_UNIVERSE_TOOLS[provider]
            for tool, response in tools.items():
                seeded = json.loads(json.dumps(response))
                actions = tuple(
                    contract.connector_value()
                    for contract in ACTION_CONTRACTS
                    if provider == GITHUB_PROVIDER and tool == contract.fixture_tool
                )
                if actions:
                    seeded["application_actions"] = actions
                setups = tuple(
                    contract.connector_value()
                    for contract in SETUP_CONTRACTS
                    if provider == GITHUB_PROVIDER and tool == contract.fixture_tool
                )
                if setups:
                    seeded["application_setups"] = setups
                await store.put(f"{APP_FIXTURE_PREFIX}{provider}:{tool}", seeded)
        with agent(agent_id):
            grants = GrantStore()
            for provider in sorted(providers):
                await grants.record(
                    provider=provider,
                    account_id=ACCOUNT_ID,
                    host=PROVIDER_HOSTS[provider],
                    grantor_member_id=member_id,
                    conversation_id=conversation_id,
                    shared=True,
                )


def _rendered_parts(output: CapabilityOutput) -> tuple[str, tuple[str, ...]] | None:
    reports = tuple(
        artifact for artifact in output.artifacts if artifact.name.endswith("-audit.json")
    )
    if len(reports) != 1:
        return None
    try:
        views = json.loads(reports[0].content)["views"]
        view = next(
            item
            for item in views
            if item["scheme"] == "light" and int(item["width"]) == DESKTOP_WIDTH
        )
        text = view["renderedText"]
        parts = tuple(view["renderedParts"])
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(text, str) or not all(isinstance(part, str) for part in parts):
        return None
    return text, parts


def _source_copy(
    source: str, markers: tuple[str, ...], passages: tuple[str, ...]
) -> tuple[str, int, int] | None:
    for segment in _source_segments(source):
        source_words = content_words(segment)
        if len(source_words) < SOURCE_COPY_MIN_WORDS or not any(
            marker.casefold() in segment.casefold() for marker in markers
        ):
            continue
        for passage in passages:
            shared = len(source_words & content_words(passage))
            if (
                shared >= SOURCE_COPY_MIN_SHARED_WORDS
                and shared / len(source_words) >= SOURCE_COPY_SHARE_LIMIT
            ):
                return segment, shared, len(source_words)
    return None


def _rewrite_source_call(path: str) -> _ConnectorCall:
    match path.removeprefix("/").split("/"):
        case ["emails", *_]:
            return _ConnectorCall(provider=EMAIL_PROVIDER, tool="list_emails")
        case ["events", *_]:
            return _ConnectorCall(provider=CALENDAR_PROVIDER, tool="list_events")
        case ["tools", provider, tool, *_]:
            return _ConnectorCall(provider=provider, tool=tool)
        case _:
            raise ValueError(f"rewrite source {path!r} has no connector call")


def _requirement_scorer(spec: _ConnectedApp) -> Grader:
    total = 1 + sum(
        len(requirement.calls)
        + len(requirement.visible)
        + len(requirement.visible_any)
        + (
            len(requirement.rewrite_sources)
            if requirement.rewrite_sources
            else len(requirement.absent)
        )
        for requirement in spec.requirements
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        rendered = _rendered_parts(output)
        if rendered is None:
            return CapabilityVerdict(
                False,
                "app audit report has no rendered desktop text",
                _score_evidence("appSource", 0, total),
            )
        rendered_text, parts = rendered
        text = rendered_text.casefold()
        passages = tuple(
            " ".join(parts[start : start + width])
            for start in range(len(parts))
            for width in range(1, SOURCE_COPY_WINDOW_PARTS + 1)
            if start + width <= len(parts)
        )
        successful = tuple(call for call in output.calls if call.succeeded)
        failures = []
        passed = 0
        if any(call.name == "list_external_tools" for call in successful):
            passed += 1
        else:
            failures.append("did not discover the connected source catalog")
        for requirement in spec.requirements:
            for expected in requirement.calls:
                described = any(
                    call.name == "describe_external_tools"
                    and call.input.get("source_id") == expected.provider
                    for call in successful
                )
                called = any(
                    call.name == "call_external_tool"
                    and call.input.get("source_id") == expected.provider
                    and call.input.get("tool_name") == expected.tool
                    for call in successful
                )
                if not described or not called:
                    failures.append(
                        f"{requirement.prompt} lacks {expected.provider}.{expected.tool} proof",
                    )
                else:
                    passed += 1
            missing = tuple(item for item in requirement.visible if item.casefold() not in text)
            passed += len(requirement.visible) - len(missing)
            if missing:
                failures.append(
                    f"{requirement.prompt} lacks rendered facts: {', '.join(missing[:4])}",
                )
            missing_any = tuple(
                alternatives
                for alternatives in requirement.visible_any
                if not any(item.casefold() in text for item in alternatives)
            )
            passed += len(requirement.visible_any) - len(missing_any)
            if missing_any:
                missing_labels = tuple(" or ".join(items) for items in missing_any[:4])
                failures.append(
                    f"{requirement.prompt} lacks rendered facts: {', '.join(missing_labels)}",
                )
            copied = tuple(item for item in requirement.absent if item.casefold() in text)
            if copied:
                failures.append(
                    f"{requirement.prompt} copies source text: {', '.join(copied[:2])}",
                )
            if not requirement.rewrite_sources:
                passed += len(requirement.absent) - len(copied)
            for path in requirement.rewrite_sources:
                source_copy = _source_copy(spec._source_text(path), requirement.absent, passages)
                if not copied and source_copy is None:
                    passed += 1
                    continue
                if copied:
                    continue
                if source_copy is None:
                    raise RuntimeError(f"rewrite source {path!r} has no copy verdict")
                source, shared, source_words = source_copy
                failures.append(
                    f"{requirement.prompt} keeps {shared}/{source_words} source words: "
                    f"{source[:120]}"
                )
        evidence = _score_evidence("appSource", passed, total)
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(
            True,
            f"{len(spec.requirements)} prompt requirements have connector and page proof",
            evidence,
        )

    return DescribedGrader(
        "each connected-app prompt requirement has its mapped connector, rendered fact, or copy "
        "proof",
        grade,
    )


def _above_fold_scorer(spec: _ConnectedApp) -> Grader:
    groups = tuple(
        (fact, (fact,)) for requirement in spec.requirements for fact in requirement.visible
    ) + tuple(
        (" or ".join(alternatives), alternatives)
        for requirement in spec.requirements
        for alternatives in requirement.visible_any
    )
    total = len(groups) * len(SCHEMES)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        reports = tuple(
            artifact for artifact in output.artifacts if artifact.name.endswith("-audit.json")
        )
        if len(reports) != 1:
            return CapabilityVerdict(
                False,
                f"captured {len(reports)} app audit reports",
                _score_evidence("appDensity", 0, total),
            )
        try:
            views = {
                (str(view["scheme"]), int(view["width"])): view
                for view in json.loads(reports[0].content)["views"]
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return CapabilityVerdict(
                False,
                "app audit report has no valid views",
                _score_evidence("appDensity", 0, total),
            )
        missing = [scheme for scheme in SCHEMES if (scheme, DESKTOP_WIDTH) not in views]
        if missing:
            return CapabilityVerdict(
                False,
                f"app audit has no desktop view for {', '.join(missing)}",
                _score_evidence("appDensity", 0, total),
            )
        passed = 0
        failures = []
        for scheme in SCHEMES:
            text = str(views[(scheme, DESKTOP_WIDTH)].get("aboveFoldText", "")).casefold()
            for label, alternatives in groups:
                if any(alternative.casefold() in text for alternative in alternatives):
                    passed += 1
                else:
                    failures.append(f"{scheme} desktop lacks {label}")
        evidence = _score_evidence("appDensity", passed, total)
        if failures:
            return CapabilityVerdict(False, "; ".join(failures[:6]), evidence)
        return CapabilityVerdict(
            True, f"{len(groups)} required fact(s) are above the fold in both schemes", evidence
        )

    return DescribedGrader(
        "every exact connected-app fact is above the fold in both desktop schemes", grade
    )


def _application_audit_contract(spec: _ConnectedApp) -> ApplicationAuditContract:
    return ApplicationAuditContract(
        facts=tuple(
            ApplicationAuditFact(label=fact, alternatives=(fact,))
            for requirement in spec.requirements
            for fact in requirement.visible
        )
        + tuple(
            ApplicationAuditFact(label=" or ".join(alternatives), alternatives=alternatives)
            for requirement in spec.requirements
            for alternatives in requirement.visible_any
        )
    )


def _copy_scorer(spec: _ConnectedApp) -> Grader:
    rewrite_requirements = tuple(
        requirement for requirement in spec.requirements if requirement.rewrite_sources
    )
    if not rewrite_requirements:
        raise ValueError(f"copy case {spec.name!r} has no rewrite source")

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        rendered = _rendered_parts(output)
        if rendered is None:
            return CapabilityVerdict(False, "app audit report has no rendered desktop text")
        rendered_text, parts = rendered
        text = rendered_text.casefold()
        passages = tuple(
            " ".join(parts[start : start + width])
            for start in range(len(parts))
            for width in range(1, SOURCE_COPY_WINDOW_PARTS + 1)
            if start + width <= len(parts)
        )
        successful = tuple(call for call in output.calls if call.succeeded)
        failures = []
        if not any(call.name == "list_external_tools" for call in successful):
            failures.append("did not discover the connected source catalog")
        for requirement in rewrite_requirements:
            for path in requirement.rewrite_sources:
                expected = _rewrite_source_call(path)
                described = any(
                    call.name == "describe_external_tools"
                    and call.input.get("source_id") == expected.provider
                    for call in successful
                )
                called = any(
                    call.name == "call_external_tool"
                    and call.input.get("source_id") == expected.provider
                    and call.input.get("tool_name") == expected.tool
                    for call in successful
                )
                if not described or not called:
                    failures.append(
                        f"{requirement.prompt} lacks {expected.provider}.{expected.tool} proof"
                    )
                source_copy = _source_copy(spec._source_text(path), requirement.absent, passages)
                if source_copy is not None:
                    source, shared, total = source_copy
                    failures.append(
                        f"{requirement.prompt} keeps {shared}/{total} source words: {source[:120]}"
                    )
            missing = tuple(item for item in requirement.visible if item.casefold() not in text)
            if missing:
                failures.append(
                    f"{requirement.prompt} lacks rendered source facts: {', '.join(missing[:4])}"
                )
            missing_any = tuple(
                alternatives
                for alternatives in requirement.visible_any
                if not any(item.casefold() in text for item in alternatives)
            )
            if missing_any:
                missing_labels = tuple(" or ".join(items) for items in missing_any[:4])
                failures.append(
                    f"{requirement.prompt} lacks rendered source facts: {', '.join(missing_labels)}"
                )
            copied = tuple(item for item in requirement.absent if item.casefold() in text)
            if copied:
                failures.append(f"{requirement.prompt} copies source text: {', '.join(copied[:2])}")
        if failures:
            return CapabilityVerdict(False, "; ".join(failures))
        return CapabilityVerdict(
            True,
            f"{len(rewrite_requirements)} source requirement(s) use connector facts and "
            "reader copy",
        )

    return DescribedGrader(
        "the app uses its connected prose source and rewrites it for the member",
        grade,
    )


@dataclass(frozen=True)
class AppBenchWorkspaceProbe(WorkspaceProbe):
    """Run a bounded app-bench command in a local Docker conversation sandbox."""

    conversation_id: UUID
    driver: WorkspaceDriver | None = None

    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
        async with _browser_probe_slot():
            process = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                f"ufo-sbx-{self.conversation_id}",
                "bash",
                "-lc",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
            except TimeoutError:
                process.kill()
                await process.wait()
                return ProbeCommandResult(124, "", "probe timed out", timeout_s)
            return ProbeCommandResult(
                process.returncode or 0,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            )

    async def apply_object_intent(
        self,
        kind: str,
        name: str,
        spec: JsonObject,
        idempotency_key: str,
    ) -> tuple[UUID, TerminalFrame]:
        if self.driver is None:
            raise RuntimeError("app action probe has no workspace driver")
        scoped_name = f"{(await self.contract_identity()).hex}-{name}"
        return await self.driver.apply_object_intent(
            self.conversation_id,
            kind,
            scoped_name,
            spec,
            idempotency_key,
        )

    async def contract_identity(self) -> UUID:
        async with workspace_tx() as connection:
            turn_id = (
                await connection.execute(
                    sa.select(tables.turn.c.id)
                    .where(
                        tables.turn.c.conversation_id == self.conversation_id,
                        tables.turn.c.parent_turn_id.is_(None),
                    )
                    .order_by(tables.turn.c.seq.desc())
                    .limit(1)
                )
            ).scalar_one()
        contract = await ScopedStore(extension="sites").get(
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(turn_id=turn_id)
        )
        if contract is None:
            raise RuntimeError("app action probe has no bound audit contract")
        return turn_id


@asynccontextmanager
async def _browser_probe_slot() -> AsyncIterator[None]:
    async with _BROWSER_PROBE_TASK_LOCK:
        descriptor = await asyncio.to_thread(
            os.open,
            BROWSER_PROBE_LOCK,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(BROWSER_PROBE_LOCK_POLL_SECONDS)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            await asyncio.to_thread(os.close, descriptor)


@dataclass(frozen=True)
class _AppBenchProbe:
    name: str

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="app probe has no workspace")
        directory = output.workspace_dir / PROBE_OUTPUT / self.name
        result = await probe.run(self._command(), PROBE_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            return ArtifactProbeResult(error=f"app probe failed: {detail[:500]}")
        paths = (
            directory / f"{self.name}-design.html",
            directory / f"{self.name}-design.svg",
            directory / f"{self.name}-interactive.html",
            directory / f"{self.name}-static.html",
            directory / f"{self.name}-audit.json",
            *(directory / f"{self.name}-{scheme}.png" for scheme in SCHEMES),
        )
        missing = [path.name for path in paths if not path.is_file()]
        if missing:
            return ArtifactProbeResult(error=f"app probe produced no {', '.join(missing)}")
        contents = await asyncio.gather(*(asyncio.to_thread(path.read_bytes) for path in paths))
        return ArtifactProbeResult(
            artifacts=tuple(
                SharedArtifact(path.name, content)
                for path, content in zip(paths, contents, strict=True)
            )
        )

    def _command(self) -> str:
        directory = f"/workspace/{PROBE_OUTPUT}/{self.name}"
        audit = base64.b64encode(AUDIT_CONTENT).decode()
        server = base64.b64encode(APPLICATION_AUDIT_SERVER).decode()
        readiness = f"""python3 - <<'PY'
import socket
import time

deadline = time.time() + 15
while time.time() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', {PROBE_PORT}), timeout=1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.2)
raise SystemExit(1)
PY"""
        return (
            "set -eu\n"
            f"capture={shlex.quote(directory)}\n"
            'rm -rf "$capture"\n'
            'mkdir -p "$capture"\n'
            f"printf %s {shlex.quote(audit)} | base64 -d > /tmp/ufo-app-bench-audit.cjs\n"
            f"printf %s {shlex.quote(server)} | base64 -d > /tmp/ufo-app-bench-server.py\n"
            f"test -s {APP_WORKSPACE_ROOT}/app.tsx\n"
            f"test -s {APP_DESIGN_PATH}\n"
            f'cp {APP_DESIGN_PATH} "$capture/{self.name}-design.svg"\n'
            'printf \'%s\' \'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<style>html,body{margin:0;min-height:100%;background:#f5f5f5}main{padding:24px}"
            "svg{display:block;width:100%;height:auto;background:white}</style></head>"
            "<body><main>' "
            f'> "$capture/{self.name}-design.html"\n'
            f'cat {APP_DESIGN_PATH} >> "$capture/{self.name}-design.html"\n'
            "printf '%s' '</main></body></html>' "
            f'>> "$capture/{self.name}-design.html"\n'
            f"(fuser -k {PROBE_PORT}/tcp 2>/dev/null || true)\n"
            f"nohup python3 /tmp/ufo-app-bench-server.py {APP_WORKSPACE_ROOT} {PROBE_PORT} "
            ">/tmp/ufo-app-bench-server.log 2>&1 &\n"
            f"{readiness}\n"
            "node /tmp/ufo-app-bench-audit.cjs "
            f"http://localhost:{PROBE_PORT}/preview.html "
            f'"$capture/{self.name}-audit.json" "$capture/{self.name}-light.png" '
            f'"$capture/{self.name}-dark.png" "$capture/{self.name}-interactive.html" '
            f'"$capture/{self.name}-static.html"'
        )


def _json_contains(value: Json, expected: Json) -> bool:
    match expected:
        case dict():
            return isinstance(value, dict) and all(
                key in value and _json_contains(value[key], item) for key, item in expected.items()
            )
        case list():
            return isinstance(value, list) and all(
                any(_json_contains(candidate, item) for candidate in value) for item in expected
            )
        case _:
            return value == expected


def _missing_setup_terms(state_text: str, setup: _SetupContract) -> tuple[str, ...]:
    groups = (
        *((connector,) for connector in setup.connectors),
        ("connected",),
        *((surface,) for surface in setup.notifications),
        ("selected", "active"),
        ("available",),
        ("not connected", "disconnected"),
        (setup.review_label,),
    )
    return tuple(
        " or ".join(group)
        for group in groups
        if not any(term.casefold() in state_text for term in group)
    )


@dataclass(frozen=True)
class _AppActionProbe:
    name: str
    contract: _ActionContract

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        captured = await _AppBenchProbe(self.name)(output, probe)
        if captured.error:
            return captured
        audit_artifact = next(
            artifact for artifact in captured.artifacts if artifact.name.endswith("-audit.json")
        )
        try:
            interaction = json.loads(audit_artifact.content)["interaction"]
            calls = interaction["calls"]
            reload_states = interaction["reloadStates"]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            return replace(captured, error="app action audit has no browser calls or reload states")
        expected_path = f"objects/{self.contract.kind}"
        matched = [
            call
            for call in calls
            if isinstance(call, dict)
            and call.get("method") == "POST"
            and call.get("path") == expected_path
            and isinstance(call.get("body"), dict)
            and call["body"].get("name") == self.contract.name
            and call["body"].get("spec") == self.contract.spec
        ]
        reloaded_text = " ".join(
            str(part)
            for state in reload_states
            if isinstance(state, dict)
            for part in state.get("parts", [])
        )
        reload_visible = self.contract.expected_result.casefold() in reloaded_text.casefold()
        if not isinstance(probe, AppBenchWorkspaceProbe):
            return replace(captured, error="app action probe cannot admit prepared intents")
        idempotency_key = f"ufo-app-bench:{self.name}:{self.contract.name}"
        try:
            contract_identity = await probe.contract_identity()
            scoped_name = f"{contract_identity.hex}-{self.contract.name}"
            first_turn, first = await probe.apply_object_intent(
                self.contract.kind,
                self.contract.name,
                self.contract.spec,
                idempotency_key,
            )
            second_turn, second = await probe.apply_object_intent(
                self.contract.kind,
                self.contract.name,
                self.contract.spec,
                idempotency_key,
            )
            refused_name = "refused"
            refused_spec = {**self.contract.spec, "value": "refused-value"}
            refused_turn, refused = await probe.apply_object_intent(
                self.contract.kind,
                refused_name,
                refused_spec,
                f"{idempotency_key}:refused",
            )
            store = ScopedStore(extension=EVAL_ENV_NAME)
            action = await store.get(APP_ACTION_KEY_PREFIX + scoped_name)
            refused_action = await store.get(
                APP_ACTION_KEY_PREFIX + f"{contract_identity.hex}-{refused_name}"
            )
            fixture = await store.get(APP_ACTION_FIXTURE_PREFIX + scoped_name)
            other_workspace = uuid4()
            with ws(other_workspace):
                leaked_action = await ScopedStore(extension=EVAL_ENV_NAME).get(
                    APP_ACTION_KEY_PREFIX + self.contract.name
                )
        except Exception as error:
            return replace(captured, error=f"prepared app action failed: {str(error)[:300]}")
        proof = {
            "browser_call": matched[0] if matched else None,
            "reload_text": reloaded_text,
            "first": {"turn_id": str(first_turn), "terminal": first.model_dump(mode="json")},
            "redelivery": {
                "turn_id": str(second_turn),
                "terminal": second.model_dump(mode="json"),
            },
            "refused": {
                "turn_id": str(refused_turn),
                "terminal": refused.model_dump(mode="json"),
                "stored": refused_action,
            },
            "action": action,
            "fixture": fixture,
            "other_workspace_action": leaked_action,
            "checks": {
                "browser": bool(matched),
                "reload": reload_visible,
                "applied": first.status == "done" and second.status == "done",
                "idempotent": first_turn == second_turn,
                "refused": refused.status == "failed"
                and refused_action is None
                and "invalid application action"
                in (refused.error_message or refused.text).casefold(),
                "scoped": leaked_action is None,
                "result": isinstance(action, dict)
                and action.get("result") == self.contract.expected_result,
                "fixture": _json_contains(fixture, self.contract.expected_fixture),
            },
        }
        artifact = SharedArtifact(
            f"{self.name}-action-proof.json",
            json.dumps(proof, indent=2, sort_keys=True).encode(),
        )
        return replace(captured, artifacts=(*captured.artifacts, artifact))


@dataclass(frozen=True)
class _AppSetupProbe:
    name: str
    action: _ActionContract
    setup: _SetupContract

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        captured = await _AppActionProbe(self.name, self.action)(output, probe)
        if captured.error:
            return captured
        audit_artifact = next(
            artifact for artifact in captured.artifacts if artifact.name.endswith("-audit.json")
        )
        try:
            interaction = json.loads(audit_artifact.content)["interaction"]
            states = interaction["states"]
            calls = interaction["calls"]
            navigations = interaction["navigations"]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            return replace(captured, error="app setup audit has no browser state or navigation")
        state_text = " ".join(
            str(part)
            for state in states
            if isinstance(state, list)
            for part in state
            if isinstance(part, str)
        ).casefold()
        missing = _missing_setup_terms(state_text, self.setup)
        matched_navigation = next(
            (
                item
                for item in navigations
                if isinstance(item, dict)
                and "setup" in str(item.get("control", "")).casefold()
                and "chat" in str(item.get("control", "")).casefold()
                and item.get("to") == self.setup.navigation
            ),
            None,
        )
        forbidden = tuple(
            call
            for call in calls
            if isinstance(call, dict)
            and call.get("method") == "POST"
            and call.get("path") != f"objects/{self.action.kind}"
        )
        proof = {
            "required": self.setup.connector_value(),
            "missing": missing,
            "navigation": matched_navigation,
            "forbidden_calls": forbidden,
            "checks": {
                "visible": not missing,
                "chat": matched_navigation is not None,
                "no_direct_setup_write": not forbidden,
            },
        }
        artifact = SharedArtifact(
            f"{self.name}-setup-proof.json",
            json.dumps(proof, indent=2, sort_keys=True).encode(),
        )
        return replace(captured, artifacts=(*captured.artifacts, artifact))


@dataclass(frozen=True)
class _AppCopyProbe:
    name: str

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="app copy probe has no workspace")
        directory = output.workspace_dir / PROBE_OUTPUT / self.name
        result = await probe.run(self._command(), PROBE_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            return ArtifactProbeResult(error=f"app copy probe failed: {detail[:500]}")
        paths = (
            directory / f"{self.name}-static.html",
            directory / f"{self.name}-audit.json",
        )
        missing = [path.name for path in paths if not path.is_file()]
        if missing:
            return ArtifactProbeResult(error=f"app copy probe produced no {', '.join(missing)}")
        contents = await asyncio.gather(*(asyncio.to_thread(path.read_bytes) for path in paths))
        return ArtifactProbeResult(
            artifacts=tuple(
                SharedArtifact(path.name, content)
                for path, content in zip(paths, contents, strict=True)
            )
        )

    def _command(self) -> str:
        directory = f"/workspace/{PROBE_OUTPUT}/{self.name}"
        capture = base64.b64encode(COPY_CAPTURE_CONTENT).decode()
        find_page = """python3 - <<'PY'
from pathlib import Path

root = Path('/workspace')
excluded = {'.skills', '.eval-output', 'node_modules'}
pages = [
    path
    for path in root.rglob('*.html')
    if not excluded.intersection(path.parts) and path.is_file()
]
pages.sort(key=lambda path: (path.name == 'index.html', path.stat().st_mtime), reverse=True)
if pages:
    print(pages[0])
PY
"""
        readiness = f"""python3 - <<'PY'
import socket
import time

deadline = time.time() + 15
while time.time() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', {PROBE_PORT}), timeout=1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.2)
raise SystemExit(1)
PY"""
        return (
            "set -eu\n"
            f"capture={shlex.quote(directory)}\n"
            'rm -rf "$capture"\n'
            'mkdir -p "$capture"\n'
            f"printf %s {shlex.quote(capture)} | base64 -d > /tmp/ufo-app-copy-capture.cjs\n"
            f"page=$({find_page})\n"
            'if [ -z "$page" ]; then printf %s "no generated HTML page" >&2; exit 2; fi\n'
            f"(fuser -k {PROBE_PORT}/tcp 2>/dev/null || true)\n"
            f"nohup python3 -m http.server {PROBE_PORT} --bind 127.0.0.1 "
            ' --directory "$(dirname "$page")" >/tmp/ufo-app-copy-server.log 2>&1 &\n'
            f"{readiness}\n"
            "node /tmp/ufo-app-copy-capture.cjs "
            f'http://localhost:{PROBE_PORT}/$(basename "$page") '
            f'"$capture/{self.name}-static.html" "$capture/{self.name}-audit.json"'
        )


HOUSE_CRITERIA = (
    "The two screenshots intentionally show different light and dark schemes. Judge each image "
    "independently: one screenshot must not split or mix schemes within itself. Surfaces and text "
    "are neutral, blue is the primary accent, and orange marks attention. Green, red, or purple "
    "status and decorative hues fail this criterion.",
    "The screen is neutral with the accent carried by a few small elements: an accent is a fill, a "
    "marker or a link, never the colour of body text or of a whole pane, and the second accent "
    "appears only where something wants attention.",
    "Text uses one neutral grotesque face for prose and a monospace face for identifiers, dates, "
    "counts, and measurements. A serif can appear only in the screen title. Decorative, novelty, "
    "or visibly mismatched faces fail this criterion.",
    "Type uses a small number of deliberate, readable steps. The compact screen title can be the "
    "largest text once; metrics, cards, and section headings do not compete with it.",
    "Panels, cards, controls, and menus have consistent near-square corners. Full pills appear "
    "only on compact rows, tags, filters, or statuses. Do not fail minor radius differences that "
    "are not visibly inconsistent.",
    "Gaps, padding and alignment are even and deliberate: elements sit on a shared grid, "
    "comparable gaps match, columns and baselines line up, and related controls use consistent "
    "interior padding.",
)

TASTE_CRITERIA = (
    "The first scan has a clear order: urgent exceptions and decisions lead, current work follows, "
    "and stable reference information stays available without competing for attention.",
    "Components fit their information and action: use rows for comparable records, tables for "
    "repeated fields, charts for real comparisons, and controls for actions. Do not turn every "
    "fact into a card, chip, badge, or decorative metric.",
    "The page is dense but calm. It uses grouping, whitespace, type, and alignment to separate "
    "meaning, with no empty hero area, repeated heading, decorative illustration, or filler copy.",
    "The screen reads as one intentional product surface. Navigation, filters, detail, status, and "
    "prepared actions use one consistent visual and interaction language.",
)


def _measured_screen(content: bytes) -> ArtifactCheck:
    """The measured half: every view `app-audit.cjs` shot, with contrast reported first because it
    is the check a first bench run showed a static token check cannot make. Contrast fails the
    screen, then a document wider than its viewport, then clipped text, then a console error."""
    try:
        report = ApplicationAuditReport.model_validate_json(content)
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        return ArtifactCheck(False, f"is not a bench audit report: {error}")
    issues = tuple(
        issue
        for issue in audit_application(report).issues
        if issue.code
        in {
            "missing_view",
            "empty_view",
            "contrast",
            "overflow",
            "clipping",
            "console",
        }
    )
    if issues:
        return ArtifactCheck(False, issues[0].message)
    return ArtifactCheck(
        True, "every string clears AA in both schemes, and nothing clips or overflows horizontally"
    )


def _interaction_screen(name: str, content: bytes) -> ArtifactCheck:
    try:
        report = ApplicationAuditReport.model_validate_json(content)
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        return ArtifactCheck(False, f"is not an interaction audit: {error}")
    issues = tuple(
        issue
        for issue in audit_application(report).issues
        if issue.code in {"console", "controls", "interaction"}
    )
    if issues:
        return ArtifactCheck(False, issues[0].message.replace("Application", name, 1))
    selectors = {success.selector for success in report.interaction.successes}
    names = [success.name for success in report.interaction.successes]
    return ArtifactCheck(
        True,
        f"{name} has {len(selectors)} browser-proved visible state changes: "
        f"{', '.join(names[:INTERACTION_MIN_SUCCESSES])}",
    )


def _captured_artifact_scorer(
    suffix: str, validate: Callable[[bytes], ArtifactCheck] | None = None
) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.artifact_error:
            return CapabilityVerdict(False, f"artifact inspection failed: {output.artifact_error}")
        artifacts = [
            artifact for artifact in output.artifacts if artifact.name.lower().endswith(suffix)
        ]
        if len(artifacts) != 1:
            return CapabilityVerdict(False, f"probe captured {len(artifacts)} {suffix} artifacts")
        if validate is None:
            return CapabilityVerdict(True, f"captured {artifacts[0].name}")
        checked = validate(artifacts[0].content)
        return CapabilityVerdict(checked.passed, f"{artifacts[0].name}: {checked.reason}")

    return DescribedGrader(f"the harness probe captures one valid {suffix} artifact", grade)


def _screen_images_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        images = [artifact for artifact in output.artifacts if artifact.name.endswith(".png")]
        if len(images) != len(SCHEMES):
            return CapabilityVerdict(
                False, f"probe captured {len(images)} screen image(s), need {len(SCHEMES)}"
            )
        invalid = next((image for image in images if not valid_png(image.content).passed), None)
        if invalid is not None:
            return CapabilityVerdict(False, f"{invalid.name} is not a valid PNG")
        return CapabilityVerdict(True, f"captured {len(images)} screen image(s)")

    return DescribedGrader("the harness probe captures both valid screen images", grade)


def _interaction_scorer(name: str) -> Grader:
    base = _captured_artifact_scorer(
        "-audit.json", lambda content: _interaction_screen(name, content)
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await base(output)
        return replace(
            verdict,
            evidence={
                **_score_evidence("appInteraction", 1 if verdict.passed else 0, 1),
            },
        )

    return DescribedGrader(grading_statement(base), grade)


def _action_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        proofs = tuple(
            artifact
            for artifact in output.artifacts
            if artifact.name.endswith("-action-proof.json")
        )
        if len(proofs) != 1:
            return CapabilityVerdict(
                False,
                f"probe captured {len(proofs)} action proof artifact(s)",
                _score_evidence("appAction", 0, 1),
            )
        try:
            checks = json.loads(proofs[0].content)["checks"]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            return CapabilityVerdict(
                False,
                "action proof is invalid",
                _score_evidence("appAction", 0, 1),
            )
        failed = tuple(name for name, passed in checks.items() if passed is not True)
        if failed:
            return CapabilityVerdict(
                False,
                f"prepared action failed: {', '.join(failed)}",
                _score_evidence("appAction", 0, 1),
            )
        return CapabilityVerdict(
            True,
            "browser call applied once, refused invalid input, and stayed workspace-scoped",
            _score_evidence("appAction", 1, 1),
        )

    return DescribedGrader(
        "the browser action reaches the prepared-intent lane and passes durable acceptance",
        grade,
    )


def _setup_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        proofs = tuple(
            artifact for artifact in output.artifacts if artifact.name.endswith("-setup-proof.json")
        )
        if len(proofs) != 1:
            return CapabilityVerdict(
                False,
                f"probe captured {len(proofs)} setup proof artifact(s)",
                _score_evidence("appSetup", 0, 1),
            )
        try:
            checks = json.loads(proofs[0].content)["checks"]
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            return CapabilityVerdict(
                False,
                "setup proof is invalid",
                _score_evidence("appSetup", 0, 1),
            )
        failed = tuple(name for name, passed in checks.items() if passed is not True)
        if failed:
            return CapabilityVerdict(
                False,
                f"application setup failed: {', '.join(failed)}",
                _score_evidence("appSetup", 0, 1),
            )
        return CapabilityVerdict(
            True,
            "connector status stayed visible and setup opened chat without a direct write",
            _score_evidence("appSetup", 1, 1),
        )

    return DescribedGrader(
        "the setup view shows connection state and routes changes through chat",
        grade,
    )


def _page_scorer() -> Grader:
    graders = (
        _captured_artifact_scorer("-interactive.html"),
        _captured_artifact_scorer("-static.html"),
        _captured_artifact_scorer("-audit.json", _measured_screen),
        _screen_images_scorer(),
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdicts = [await grader(output) for grader in graders]
        passed = sum(1 for verdict in verdicts if verdict.passed)
        return CapabilityVerdict(
            passed == len(verdicts),
            "; ".join(verdict.reason for verdict in verdicts),
            _score_evidence("appPage", passed, len(verdicts)),
        )

    return DescribedGrader(
        "; ".join(grading_statement(grader) for grader in graders),
        grade,
    )


def _application_builder_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        failed = _score_evidence("processBuilder", 0, 1)
        own = output.own_calls
        delegations = tuple(
            call for call in own if call.name == APPLICATION_BUILDER_DELEGATION_TOOL
        )
        if not delegations:
            return CapabilityVerdict(
                False,
                f"did not call {APPLICATION_BUILDER_DELEGATION_TOOL}",
                failed,
            )
        if len(delegations) != 1:
            return CapabilityVerdict(
                False,
                f"the parent delegated {len(delegations)} times, expected one worker call",
                failed,
            )
        if not delegations[0].succeeded:
            return CapabilityVerdict(False, "the worker delegation failed", failed)
        try:
            result = ApplicationBuilderResult.model_validate_json(delegations[0].result)
        except ValueError:
            return CapabilityVerdict(False, "the worker returned no structured result", failed)
        if result.status != "deployed":
            return CapabilityVerdict(
                False,
                f"the deterministic acceptance result was {result.status}: {result.blocker}",
                failed,
            )
        parent_forbidden = {
            "spawn",
            "list_external_tools",
            "describe_external_tools",
            "search_connector_tools",
            "call_external_tool",
            "read",
            "bash",
            "start_server",
            "js_repl",
            APPLICATION_BUILDER_DESIGN_TOOL,
            APPLICATION_BUILDER_QA_TOOL,
            APPLICATION_BUILDER_READ_TOOL,
            APPLICATION_BUILDER_EDIT_TOOL,
            APPLICATION_BUILDER_WRITE_TOOL,
            *DEPLOY_TOOLS,
            "set_homepage",
            "build_website",
            "write",
            "edit",
        }
        parent_work = tuple(call.name for call in own if call.name in parent_forbidden)
        if parent_work:
            return CapabilityVerdict(
                False,
                f"the parent entered the worker loop: {', '.join(parent_work)}",
                failed,
            )
        required_worker_tools = {
            APPLICATION_BUILDER_DESIGN_TOOL,
            APPLICATION_BUILDER_QA_TOOL,
            "deploy_ufo_application",
        }
        if APPLICATION_BUILDER_QA_TOOL == "js_repl":
            required_worker_tools.add("start_server")
        completed = frozenset(call.name for call in output.calls if call.succeeded)
        missing = required_worker_tools - completed
        if missing:
            return CapabilityVerdict(
                False,
                f"the worker did not complete: {', '.join(sorted(missing))}",
                failed,
            )
        design_calls = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == APPLICATION_BUILDER_DESIGN_TOOL
        )
        source_writes = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == APPLICATION_BUILDER_WRITE_TOOL
        )
        if not source_writes:
            return CapabilityVerdict(
                False,
                f"the worker did not call {APPLICATION_BUILDER_WRITE_TOOL}",
                failed,
            )
        if len(design_calls) != 1 or design_calls[0] > source_writes[0]:
            return CapabilityVerdict(
                False,
                "the worker must write one SVG design before app.tsx",
                failed,
            )
        if any(call.name == "set_homepage" for call in output.calls):
            return CapabilityVerdict(
                False,
                "the worker tried to certify its own homepage",
                failed,
            )
        successful_qa = sum(
            1
            for call in output.calls
            if call.name == APPLICATION_BUILDER_QA_TOOL and call.succeeded
        )
        needed_qa = 2 if APPLICATION_BUILDER_QA_TOOL == "js_repl" else 1
        if successful_qa < needed_qa:
            return CapabilityVerdict(
                False,
                f"the worker completed {successful_qa} successful QA call(s), expected at least "
                f"{needed_qa}",
                failed,
            )
        return CapabilityVerdict(
            True,
            f"the parent delegated once and {APPLICATION_BUILDER_NAME} completed the worker loop",
            _score_evidence("processBuilder", 1, 1),
        )

    return DescribedGrader(
        f"the parent calls {APPLICATION_BUILDER_DELEGATION_TOOL} once; {APPLICATION_BUILDER_NAME} "
        "owns connector inspection, source, QA, and deployment; deterministic acceptance binds",
        grade,
    )


def _skill_scorer() -> Grader:
    base = skill_scorer(SITE_SKILL, HOUSE_STYLE_SKILL)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await base(output)
        delegated = any(
            call.name == APPLICATION_BUILDER_DELEGATION_TOOL and call.succeeded
            for call in output.own_calls
        )
        if delegated:
            return CapabilityVerdict(
                True,
                f"{APPLICATION_BUILDER_NAME} preloads '{SITE_SKILL}'",
                _score_evidence("processSkill", 1, 1),
            )
        return replace(
            verdict,
            evidence={
                **verdict.evidence,
                **_score_evidence("processSkill", 1 if verdict.passed else 0, 1),
            },
        )

    return DescribedGrader(grading_statement(base), grade)


def _delivery_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        successful = tuple(
            (index, call) for index, call in enumerate(output.calls) if call.succeeded
        )
        deployments = tuple(
            (index, call) for index, call in successful if call.name in DEPLOY_TOOLS
        )
        delegations = tuple(
            call
            for call in output.own_calls
            if call.name == APPLICATION_BUILDER_DELEGATION_TOOL and call.succeeded
        )
        result = None
        if len(delegations) == 1:
            try:
                result = ApplicationBuilderResult.model_validate_json(delegations[0].result)
            except ValueError:
                pass
        accepted = bool(
            result is not None
            and result.status == "deployed"
            and result.site_name
            and result.site_url
        )
        evidence = _score_evidence(
            "appDelivery", (1 if deployments else 0) + (1 if accepted else 0), 2
        )
        if not deployments:
            return CapabilityVerdict(
                False, "did not complete deploy_website or publish_website", evidence
            )
        if not accepted:
            return CapabilityVerdict(
                False, "deterministic acceptance did not bind the deployed application", evidence
            )
        return CapabilityVerdict(
            True,
            f"{deployments[0][1].name} completed before deterministic acceptance bound the page",
            evidence,
        )

    return DescribedGrader(
        "deployment completes before deterministic acceptance binds the application homepage",
        grade,
    )


def _qa_efficiency_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        failed = _score_evidence("processQa", 0, 1)
        qa_calls = tuple(call for call in output.calls if call.name == APPLICATION_BUILDER_QA_TOOL)
        if not qa_calls:
            return CapabilityVerdict(False, "used no application QA call", failed)
        max_calls = (
            MAX_BROWSER_QA_CALLS
            if APPLICATION_BUILDER_QA_TOOL == "js_repl"
            else MAX_PRODUCT_QA_CALLS
        )
        if len(qa_calls) > max_calls:
            return CapabilityVerdict(
                False,
                f"used {len(qa_calls)} QA calls, needs at most {max_calls}",
                failed,
            )
        successful = tuple(call for call in qa_calls if call.succeeded)
        needed = 2 if APPLICATION_BUILDER_QA_TOOL == "js_repl" else 1
        if len(successful) < needed:
            return CapabilityVerdict(
                False,
                f"used {len(successful)} successful QA call(s), needs at least {needed}",
                failed,
            )
        if not qa_calls[-1].succeeded:
            return CapabilityVerdict(False, "the final QA call failed", failed)
        deployments = tuple(
            (index, call) for index, call in enumerate(output.calls) if call.name in DEPLOY_TOOLS
        )
        if not deployments:
            return CapabilityVerdict(
                False,
                "QA must precede the application deployment",
                failed,
            )
        if not deployments[-1][1].succeeded:
            return CapabilityVerdict(False, "the final application deployment failed", failed)
        names = tuple(call.name for call in output.calls)
        qa_indexes = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == APPLICATION_BUILDER_QA_TOOL
        )
        if APPLICATION_BUILDER_QA_TOOL == "js_repl":
            starts = tuple(call for call in output.calls if call.name == "start_server")
            if len(starts) != MAX_PREVIEW_SERVER_CALLS:
                return CapabilityVerdict(
                    False,
                    f"used start_server {len(starts)} time(s), needs {MAX_PREVIEW_SERVER_CALLS}",
                    failed,
                )
            if not starts[0].succeeded:
                return CapabilityVerdict(
                    False, "the preview server did not start successfully", failed
                )
            if not names.index("start_server") < min(qa_indexes):
                return CapabilityVerdict(False, "QA must run after start_server", failed)
        if not any(max(qa_indexes) < index for index, _call in deployments):
            deployment_name = deployments[0][1].name
            return CapabilityVerdict(False, f"QA must finish before {deployment_name}", failed)
        return CapabilityVerdict(
            True,
            f"used {len(qa_calls)} bounded application QA call(s)",
            _score_evidence("processQa", 1, 1),
        )

    return DescribedGrader(
        "one bounded application QA protocol ending in success before deployment", grade
    )


APP_TIERS = {
    **{case: 1 for case in CONTROL_MEMBER_QUERIES},
    **{
        case.name: 3
        if len({call.provider for item in case.requirements for call in item.calls}) > 1
        else 2
        for case in CONNECTED_APPS
    },
    **{name: 3 for name in ACTION_CASE_NAMES},
}


def _fraction(evidence: JsonObject, key: str, default: float = 0.0) -> float:
    passed = evidence.get(f"{key}Passed")
    total = evidence.get(f"{key}Total")
    if not isinstance(passed, int) or not isinstance(total, int) or total <= 0:
        return default
    return passed / total


def _score_app_report(report: EvalReport) -> EvalReport:
    cases = []
    layer_values: dict[str, list[float]] = {
        "delivery": [],
        "source": [],
        "density": [],
        "page": [],
        "interaction": [],
        "action": [],
        "visual": [],
    }
    process_values = []
    app_values = []
    for case in report.cases:
        selected = case.evidence.get("selectedAttempt")
        attempts = case.evidence.get("attempts")
        attempt: JsonObject = {}
        if isinstance(selected, int) and isinstance(attempts, list) and selected < len(attempts):
            candidate = attempts[selected]
            if isinstance(candidate, dict):
                attempt = candidate
        grader = attempt.get("grader")
        scored: JsonObject = grader if isinstance(grader, dict) else {}
        judge = attempt.get("judge")
        rubric = case.evidence.get("visualRubric")
        visual_total = len(rubric) if isinstance(rubric, list) else 0
        visual_passed = (
            sum(1 for item in judge if isinstance(item, dict) and item.get("passed") is True)
            if isinstance(judge, list)
            else 0
        )
        action_passed = scored.get("appActionPassed", 0)
        action_total = scored.get("appActionTotal", 0)
        if not isinstance(action_passed, int):
            action_passed = 0
        if not isinstance(action_total, int):
            action_total = 0
        if case.name in SETUP_CASE_NAMES:
            setup_passed = scored.get("appSetupPassed", 0)
            setup_total = scored.get("appSetupTotal", 0)
            action_passed += setup_passed if isinstance(setup_passed, int) else 0
            action_total += setup_total if isinstance(setup_total, int) else 0
        layers = {
            "delivery": _fraction(scored, "appDelivery"),
            "source": _fraction(
                scored, "appSource", 1.0 if case.name in CONTROL_MEMBER_QUERIES else 0.0
            ),
            "density": _fraction(
                scored, "appDensity", 1.0 if case.name in CONTROL_MEMBER_QUERIES else 0.0
            ),
            "page": _fraction(scored, "appPage"),
            "interaction": _fraction(scored, "appInteraction"),
            "action": (
                action_passed / action_total
                if action_total > 0
                else 0.0
                if case.name in ACTION_CASE_NAMES
                else 1.0
            ),
            "visual": visual_passed / visual_total if visual_total else 0.0,
        }
        process_layers = {
            "skill": _fraction(scored, "processSkill"),
            "builder": _fraction(scored, "processBuilder"),
            "qa": _fraction(scored, "processQa"),
        }
        app_score = sum(layers.values()) / len(layers)
        process_score = sum(process_layers.values()) / len(process_layers)
        evidence = {
            **case.evidence,
            "appScore": app_score,
            "appScoreLayers": layers,
            "processScore": process_score,
            "processScoreLayers": process_layers,
        }
        cases.append(case.model_copy(update={"evidence": evidence, "tier": APP_TIERS[case.name]}))
        if case.excluded:
            continue
        app_values.append(app_score)
        process_values.append(process_score)
        for name, value in layers.items():
            layer_values[name].append(value)
    metrics = (
        (
            EvalMetric(name="app_score", value=sum(app_values) / len(app_values)),
            *(
                EvalMetric(name=f"{name}_score", value=sum(values) / len(values))
                for name, values in layer_values.items()
            ),
            EvalMetric(name="process_score", value=sum(process_values) / len(process_values)),
        )
        if app_values
        else ()
    )
    return report.model_copy(update={"cases": tuple(cases), "metrics": (*report.metrics, *metrics)})


def _scored_task(task: EvalTask) -> EvalTask:
    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        return _score_app_report(await task.run(target, slots))

    return replace(task, run=run)


def _pull_before_redeploy_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = tuple(call.name for call in output.calls)
        deploys = tuple(index for index, name in enumerate(names) if name in DEPLOY_TOOLS)
        if len(deploys) < 2:
            return CapabilityVerdict(
                False, f"used deployment {len(deploys)} time(s); the rework needs a second"
            )
        pulls = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == "object_get" and call.succeeded
        )
        if not any(deploys[0] < pull < deploys[-1] for pull in pulls):
            return CapabilityVerdict(
                False, "the rework must object_get the site between the deploys, before editing"
            )
        return CapabilityVerdict(True, "pulled the deployed source before the rework's deploy")

    return DescribedGrader("the rework pulls the deployed source before deploying again", grade)


def _screen(
    name: str,
    information_inventory: str,
    *,
    extra_graders: tuple[Grader, ...] = (),
    extra_visual: tuple[str, ...] = (),
    seed: _ConnectedAppSeed | None = None,
    action: _ActionContract | None = None,
    setup: _SetupContract | None = None,
    data_digest: str = "",
) -> CapabilityCase:
    return CapabilityCase(
        name,
        MEMBER_QUERIES[name],
        combine(
            _skill_scorer(),
            _delivery_scorer(),
            _application_builder_scorer(),
            _qa_efficiency_scorer(),
            _page_scorer(),
            _interaction_scorer(name),
            *extra_graders,
        ),
        visual_rubric=(
            *HOUSE_CRITERIA,
            *extra_visual,
            f"The first {DESKTOP_WIDTH} x {DESKTOP_HEIGHT} view gives useful operational density "
            f"above the fold: {information_inventory}. The working area uses compact grouped "
            "content, and no oversized title, empty decorative panel, or unused region takes "
            "space from requested information. Judge visible density and hierarchy, not an exact "
            "fact count.",
        ),
        judge_on_deterministic_failure=True,
        digest_tag=(
            f"ufo-app-bench:{name}:interactive-homepage:qa-bounded-product:"
            f"audit-{AUDIT_DIGEST[:12]}:wait-{WORKFLOW_WAIT_SECONDS:g}{data_digest}"
        ),
        artifact_probe=(
            _AppBenchProbe(name)
            if action is None
            else _AppActionProbe(name, action)
            if setup is None
            else _AppSetupProbe(name, action, setup)
        ),
        seed=seed,
        workspace_files=APP_WORKSPACE_FILES,
    )


CONTROL_CASES = (
    _screen(
        "kanban-board",
        "the board context, useful workflow groups, enough work items to scan, and ownership or "
        "current state on each item; the exact layout, labels, and item count are the builder's "
        "choice",
    ),
    _screen(
        "call-notes",
        "the call identity and context, participants, the outcome or summary, decisions or key "
        "points, and follow-up work with responsibility or timing where useful; the exact layout "
        "and labels are the builder's choice",
    ),
    _screen(
        "daily-brief",
        "the brief's date or scope, current status or metrics, material changes, priorities or "
        "follow-ups, and source context; the exact layout, labels, and item count are the "
        "builder's choice",
    ),
    _screen(
        "daily-brief-rework",
        "the brief's date or scope, current status or metrics, material changes, priorities or "
        "follow-ups, and source context; the exact layout, labels, and item count are the "
        "builder's choice",
        extra_graders=(_pull_before_redeploy_scorer(),),
        data_digest=":pull-before-redeploy",
    ),
)


CONNECTED_CASES = tuple(
    _screen(
        spec.name,
        " ".join(requirement.prompt for requirement in spec.requirements),
        extra_graders=(_requirement_scorer(spec), _above_fold_scorer(spec)),
        extra_visual=TASTE_CRITERIA,
        seed=_ConnectedAppSeed(spec, MEMBER_QUERIES[spec.name]),
        data_digest=f":data-{APP_DATA_DIGEST[:12]}",
    )
    for spec in CONNECTED_APPS
)

ACTION_CASES = tuple(
    _screen(
        f"action-{contract.source_case}",
        " ".join(
            requirement.prompt
            for requirement in CONNECTED_APP_BY_NAME[contract.source_case].requirements
        ),
        extra_graders=(
            _requirement_scorer(CONNECTED_APP_BY_NAME[contract.source_case]),
            _above_fold_scorer(CONNECTED_APP_BY_NAME[contract.source_case]),
            _action_scorer(),
        ),
        extra_visual=TASTE_CRITERIA,
        seed=_ConnectedAppSeed(
            CONNECTED_APP_BY_NAME[contract.source_case],
            MEMBER_QUERIES[f"action-{contract.source_case}"],
        ),
        action=contract,
        data_digest=(
            f":data-{APP_DATA_DIGEST[:12]}:action-"
            f"{sha256(contract.model_dump_json().encode()).hexdigest()[:12]}"
        ),
    )
    for contract in ACTION_CONTRACTS
)

SETUP_CASES = tuple(
    _screen(
        f"setup-{action.source_case}",
        " ".join(
            requirement.prompt
            for requirement in CONNECTED_APP_BY_NAME[action.source_case].requirements
        ),
        extra_graders=(
            _requirement_scorer(CONNECTED_APP_BY_NAME[action.source_case]),
            _above_fold_scorer(CONNECTED_APP_BY_NAME[action.source_case]),
            _action_scorer(),
            _setup_scorer(),
        ),
        extra_visual=TASTE_CRITERIA,
        seed=_ConnectedAppSeed(
            CONNECTED_APP_BY_NAME[action.source_case],
            MEMBER_QUERIES[f"setup-{action.source_case}"],
        ),
        action=action,
        setup=setup,
        data_digest=(
            f":data-{APP_DATA_DIGEST[:12]}:action-"
            f"{sha256(action.model_dump_json().encode()).hexdigest()[:12]}:setup-"
            f"{sha256(setup.model_dump_json().encode()).hexdigest()[:12]}"
        ),
    )
    for action, setup in zip(ACTION_CONTRACTS, SETUP_CONTRACTS, strict=True)
)

COPY_CASES = tuple(
    CapabilityCase(
        f"copy-{spec.name}",
        MEMBER_QUERIES[spec.name],
        combine(
            _skill_scorer(),
            _delivery_scorer(),
            _application_builder_scorer(),
            _captured_artifact_scorer("-static.html"),
            _copy_scorer(spec),
        ),
        digest_tag=(
            f"ufo-app-copy:{spec.name}:source-use-and-reader-copy:"
            f"wait-{WORKFLOW_WAIT_SECONDS:g}:capture-{COPY_CAPTURE_DIGEST[:12]}:"
            f"data-{APP_DATA_DIGEST[:12]}"
        ),
        artifact_probe=_AppCopyProbe(spec.name),
        seed=_ConnectedAppSeed(spec, MEMBER_QUERIES[spec.name]),
        workspace_files=APP_WORKSPACE_FILES,
    )
    for spec in CONNECTED_APPS
    if any(requirement.rewrite_sources for requirement in spec.requirements)
)

CASES = (*CONTROL_CASES, *CONNECTED_CASES, *ACTION_CASES, *SETUP_CASES)
