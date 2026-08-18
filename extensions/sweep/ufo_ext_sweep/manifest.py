import asyncio
import hashlib
import ipaddress
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, MemberContextRecord
from ufo.sdk.jobs import JobSpec, seated_member_workspaces
from ufo.sdk.manifest import (
    AgentProvision,
    AgentSpec,
    Deny,
    HookContext,
    HookOutcome,
    HookSpec,
    Manifest,
    PreToolUse,
    SkillSpec,
    SubagentProfile,
)
from ufo.sdk.search import FetchRequest
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "sweep"
VERSION = "0.1.0"
AGENT_NAME = "daily-brief"
TOOL_NAME = "sweep_newspaper"
TASK_TOOL_NAME = "update_todo_list"
MEMORY_TOOL_NAME = "memory_update"
HOMEPAGE_TOOL_NAME = "set_homepage"
JOB_NAME = "daily_brief"
JOB_SCHEDULE = "0 0 * * * *"
FINAL_MODEL = "claude-sonnet-5"
SCOUT_MODEL = "gpt-5.6-luna"
SKILL_NAME = "daily-brief"
SKILL_DIR = Path(__file__).parent / "skills" / SKILL_NAME
AGENT_PROMPT = (
    "Load the daily-brief skill for scheduled briefs and member follow-up. Follow it exactly."
)
FIRST_RANGE = timedelta(days=7)
OPEN_REPEAT = timedelta(days=7)
LEDGER_RETENTION = timedelta(days=30)
MAX_CONTEXT_RECORDS = 200
MAX_SCOUT_INPUT_CHARS = 40_000
MAX_FINDINGS = 5
MAX_REFERENCES = 3
MAX_COVERAGE_CHARS = 300
MAX_PUBLIC_SOURCES = 8
MAX_EDITION_ATTEMPTS = 3
REFERENCE_COVERAGE = (
    "Some references or findings were omitted because they were outside the supplied input."
)
URL = re.compile(r"https?://[^\s<>\])}]+")

_metadata = sa.MetaData()
edition = sa.Table(
    "sweep_edition",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("member_id", sa.Uuid, nullable=False),
    sa.Column("local_date", sa.Text, nullable=False),
    sa.Column("timezone", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=True),
    sa.Column("turn_id", sa.Uuid, nullable=True),
    sa.Column("candidate_cursor", sa.DateTime(timezone=True), nullable=True),
    sa.Column("candidate_input_keys", sa.JSON, nullable=True),
    sa.Column("candidate_finding_keys", sa.JSON, nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint("workspace_id", "member_id", "local_date"),
)

turn = sa.table(
    "turn",
    sa.column("id", sa.Uuid),
    sa.column("status", sa.Text),
)


class SweepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_description: str = Field(description="State that you are preparing the daily brief.")


class ContextRecord(BaseModel):
    kind: str
    ref: str
    title: str
    text: str
    information_date: datetime
    stable_subject_key: str


class ScoutInput(BaseModel):
    records: tuple[ContextRecord, ...]
    preload_skills: tuple[str, ...] = (SKILL_NAME,)


class Finding(BaseModel):
    title: str = Field(max_length=120)
    why_it_matters: str = Field(max_length=500)
    information_date: date
    stable_subject_key: str = Field(max_length=240)
    references: tuple[str, ...] = Field(default=(), max_length=MAX_REFERENCES)


class ScoutOutput(BaseModel):
    findings: tuple[Finding, ...] = Field(default=(), max_length=MAX_FINDINGS)
    coverage: str = Field(max_length=MAX_COVERAGE_CHARS)


SCOUTS = ("work", "missed-items", "pages-artifacts", "public-context")


def local_edition_date(now: datetime, timezone: str) -> date | None:
    local = now.astimezone(ZoneInfo(timezone))
    return local.date() if local.timetz().replace(tzinfo=None) >= time(8) else None


def _profile(name: str) -> SubagentProfile:
    return SubagentProfile(
        name=f"sweep-{name}",
        prompt=f"Act as the {name} scout. Follow the preloaded daily-brief skill.",
        tool_names=(),
        input_model=ScoutInput,
        output_model=ScoutOutput,
        max_rounds=1,
        model=SCOUT_MODEL,
        isolated_tools=True,
        untrusted_output=name == "public-context",
    )


def _bounded(records: tuple[MemberContextRecord, ...]) -> tuple[ContextRecord, ...]:
    selected: list[ContextRecord] = []
    used = 0
    for record in records:
        text = record.text[:2_000]
        size = len(record.title) + len(text) + len(record.ref)
        if used + size > MAX_SCOUT_INPUT_CHARS:
            break
        selected.append(
            ContextRecord(
                kind=record.kind,
                ref=record.ref,
                title=record.title,
                text=text,
                information_date=record.information_date,
                stable_subject_key=record.stable_subject_key,
            )
        )
        used += size
    return tuple(selected)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _prior_ledgers(
    ext: ExtensionContext, member_id: UUID, now: datetime
) -> tuple[dict[str, datetime], dict[str, datetime]]:
    async with ext.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    edition.c.candidate_input_keys,
                    edition.c.candidate_finding_keys,
                    edition.c.completed_at,
                )
                .where(
                    edition.c.workspace_id == ext.workspace_id,
                    edition.c.member_id == member_id,
                    edition.c.status == "completed",
                    edition.c.completed_at >= now - LEDGER_RETENTION,
                )
                .order_by(edition.c.completed_at.desc())
            )
        ).all()
    inputs: dict[str, datetime] = {}
    findings: dict[str, datetime] = {}
    for row in rows:
        completed_at = (
            row.completed_at
            if row.completed_at.tzinfo is not None
            else row.completed_at.replace(tzinfo=UTC)
        )
        for key in row.candidate_input_keys or ():
            inputs.setdefault(key, completed_at)
        for key in row.candidate_finding_keys or ():
            if len(findings) == 200:
                break
            findings.setdefault(key, completed_at)
    return inputs, findings


def _changed_records(
    records: tuple[MemberContextRecord, ...], prior: dict[str, datetime], now: datetime
) -> tuple[MemberContextRecord, ...]:
    unique = {record.stable_subject_key: record for record in records}
    changed: list[MemberContextRecord] = []
    for record in unique.values():
        seen = prior.get(record.stable_subject_key)
        wait = OPEN_REPEAT if record.kind in {"task", "objective"} else LEDGER_RETENTION
        if seen is None or now - seen >= wait:
            changed.append(record)
    return tuple(sorted(changed, key=lambda item: item.information_date, reverse=True))


async def _public_records(
    ctx: ToolContext, records: tuple[MemberContextRecord, ...], now: datetime
) -> tuple[MemberContextRecord, ...]:
    urls: list[str] = []
    for record in records:
        for url in URL.findall(f"{record.ref} {record.text}"):
            parsed = urlsplit(url)
            hostname = parsed.hostname
            if hostname is None or parsed.username is not None or parsed.password is not None:
                continue
            try:
                if not ipaddress.ip_address(hostname).is_global:
                    continue
            except ValueError:
                if "." not in hostname or hostname.endswith((".internal", ".local")):
                    continue
            public_url = urlunsplit((parsed.scheme, hostname, "/", "", ""))
            if public_url not in urls:
                urls.append(public_url)
    if ctx.search_provider is None or not ctx.search_provider.supports_fetch:
        return ()
    fetched = await asyncio.gather(
        *(
            ctx.search_provider.fetch(FetchRequest(url=url, max_chars=4_000))
            for url in urls[:MAX_PUBLIC_SOURCES]
        ),
        return_exceptions=True,
    )
    return tuple(
        MemberContextRecord(
            kind="public",
            ref=result.url,
            title=result.url,
            text=(result.summary or result.text)[:4_000],
            information_date=now,
            stable_subject_key=f"public:{_digest(result.url + result.text)}",
        )
        for result in fetched
        if not isinstance(result, BaseException)
    )


async def _sweep(ctx: ToolContext, _args: SweepInput) -> ToolResult:
    if ctx.ext is None or ctx.acting_member_id is None:
        raise RuntimeError("sweep_newspaper requires a scheduled member")
    ext = ctx.ext
    member_id = ctx.acting_member_id
    now = datetime.now(UTC)
    async with ext.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(edition).where(
                    edition.c.workspace_id == ext.workspace_id,
                    edition.c.member_id == member_id,
                    edition.c.turn_id == ctx.turn.id,
                )
            )
        ).one_or_none()
        completed_cursor = (
            await connection.execute(
                sa.select(sa.func.max(edition.c.candidate_cursor)).where(
                    edition.c.workspace_id == ext.workspace_id,
                    edition.c.member_id == member_id,
                    edition.c.status == "completed",
                )
            )
        ).scalar_one()
    if row is None:
        raise RuntimeError("the scheduled edition is not registered")
    since = completed_cursor or now - FIRST_RANGE
    records = await ext.member_context(since=since, limit=MAX_CONTEXT_RECORDS)
    prior_inputs, prior_findings = await _prior_ledgers(ext, member_id, now)
    changed = _changed_records(tuple(records), prior_inputs, now)
    public = await _public_records(ctx, changed, now)
    groups = {
        "work": tuple(record for record in changed if record.kind in {"task", "objective"}),
        "missed-items": tuple(
            record for record in changed if record.kind in {"conversation", "memory"}
        ),
        "pages-artifacts": tuple(
            record for record in changed if record.kind in {"page", "artifact"}
        ),
        "public-context": public,
    }
    bounded_groups = {name: _bounded(records) for name, records in groups.items()}

    async def scout(name: str) -> tuple[ScoutOutput, bool]:
        scout_records = bounded_groups[name]
        result = await ctx.spawn(
            f"profile:sweep-{name}",
            ScoutInput(records=scout_records).model_dump(mode="json"),
            dedup_key=f"daily-brief:{member_id}:{row.local_date}/{name}",
        )
        if result.output is None:
            raise RuntimeError(f"{name} scout returned no output")
        output = ScoutOutput.model_validate(result.output)
        allowed_references = {record.ref for record in scout_records}
        references_removed = any(
            reference not in allowed_references
            for finding in output.findings
            for reference in finding.references
        )
        findings = tuple(
            finding.model_copy(
                update={
                    "references": tuple(
                        reference
                        for reference in finding.references
                        if reference in allowed_references
                    )
                }
            )
            for finding in output.findings
        )
        coverage = output.coverage
        if references_removed:
            prefix = coverage[: MAX_COVERAGE_CHARS - len(REFERENCE_COVERAGE) - 1]
            coverage = f"{prefix} {REFERENCE_COVERAGE}".strip()
        return ScoutOutput(findings=findings, coverage=coverage), not references_removed

    results = await asyncio.gather(*(scout(name) for name in SCOUTS), return_exceptions=True)
    completed = {
        name: result
        for name, result in zip(SCOUTS, results, strict=True)
        if isinstance(result, tuple)
    }
    missing = tuple(name for name in SCOUTS if name not in completed)
    if len(completed) < 3:
        return ToolResult(
            content=(TextContent(text=f"Edition failed. Missing scouts: {', '.join(missing)}."),),
            is_error=True,
        )
    findings: list[dict[str, object]] = []
    seen: set[str] = set()
    emitted: list[str] = []
    for name in SCOUTS:
        result = completed.get(name)
        if result is None:
            continue
        output, _ledger_safe = result
        for finding in output.findings:
            if finding.stable_subject_key in seen:
                continue
            seen.add(finding.stable_subject_key)
            prior = prior_findings.get(finding.stable_subject_key)
            wait = OPEN_REPEAT if name == "work" else LEDGER_RETENTION
            if prior is not None and now - prior < wait:
                continue
            emitted.append(finding.stable_subject_key)
            findings.append({"section": name, **finding.model_dump(mode="json")})
    completed_input_keys = {
        record.stable_subject_key
        for name, (_output, ledger_safe) in completed.items()
        if ledger_safe
        if name != "public-context"
        for record in bounded_groups[name]
    }
    private_input_keys = {
        record.stable_subject_key
        for name in SCOUTS
        if name != "public-context"
        for record in groups[name]
    }
    async with ext.transaction() as connection:
        await connection.execute(
            sa.update(edition)
            .where(
                edition.c.workspace_id == ext.workspace_id,
                edition.c.member_id == member_id,
                edition.c.local_date == row.local_date,
                edition.c.turn_id == ctx.turn.id,
            )
            .values(
                candidate_cursor=(now if private_input_keys <= completed_input_keys else since),
                candidate_input_keys=[
                    record.stable_subject_key
                    for record in changed
                    if record.stable_subject_key in completed_input_keys
                ][:MAX_CONTEXT_RECORDS],
                candidate_finding_keys=emitted[:200],
                updated_at=now,
            )
        )
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "findings": findings,
                        "coverage": {
                            name: output.coverage
                            for name, (output, _ledger_safe) in completed.items()
                        },
                        "missing": missing,
                    },
                    separators=(",", ":"),
                )
            ),
        ),
        untrusted=True,
    )


async def _finalize(ctx: ExtensionContext, now: datetime) -> None:
    async with ctx.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    edition.c.member_id,
                    edition.c.local_date,
                    edition.c.turn_id,
                    edition.c.candidate_cursor,
                    turn.c.status,
                )
                .select_from(edition.join(turn, edition.c.turn_id == turn.c.id))
                .where(
                    edition.c.workspace_id == ctx.workspace_id,
                    edition.c.status == "pending",
                    edition.c.turn_id.is_not(None),
                    turn.c.status.in_(("done", "failed", "cancelled")),
                )
            )
        ).all()
    for row in rows:
        completed = row.status == "done" and row.candidate_cursor is not None
        async with ctx.transaction() as connection:
            await connection.execute(
                sa.update(edition)
                .where(
                    edition.c.workspace_id == ctx.workspace_id,
                    edition.c.member_id == row.member_id,
                    edition.c.local_date == row.local_date,
                    edition.c.turn_id == row.turn_id,
                )
                .values(
                    status="completed" if completed else "failed",
                    completed_at=now if completed else None,
                    updated_at=now,
                )
            )


async def _draft_only(ctx: HookContext) -> HookOutcome:
    if ctx.turn is None or not isinstance(ctx.payload, PreToolUse):
        return None
    async with ctx.ext.transaction() as connection:
        scheduled = (
            await connection.execute(
                sa.select(edition.c.turn_id).where(
                    edition.c.workspace_id == ctx.ext.workspace_id,
                    edition.c.turn_id == ctx.turn.id,
                )
            )
        ).one_or_none()
    if scheduled is None:
        return None
    if ctx.payload.tool_name == HOMEPAGE_TOOL_NAME:
        return Deny(
            reason=(
                "A scheduled daily brief can only propose drafts: binding a homepage is a "
                "member-facing turn."
            )
        )
    return Deny(reason="A scheduled daily brief can only propose drafts for member approval.")


async def _tick(ctx: ExtensionContext, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    await _finalize(ctx, now)
    cursor: UUID | None = None
    while True:
        page = await ctx.seated_members(cursor=cursor)
        for member in page.members:
            local_date = local_edition_date(now, member.timezone)
            if local_date is None:
                continue
            key = f"daily-brief:{member.id}:{local_date.isoformat()}"
            async with ctx.transaction() as connection:
                row = (
                    await connection.execute(
                        sa.select(edition.c.status, edition.c.turn_id, edition.c.attempt).where(
                            edition.c.workspace_id == ctx.workspace_id,
                            edition.c.member_id == member.id,
                            edition.c.local_date == local_date.isoformat(),
                        )
                    )
                ).one_or_none()
                if row is not None and (
                    row.status == "completed"
                    or (row.status == "pending" and row.turn_id is not None)
                    or (row.status == "failed" and row.attempt >= MAX_EDITION_ATTEMPTS)
                ):
                    continue
                values = {
                    "timezone": member.timezone,
                    "status": "pending",
                    "turn_id": None,
                    "candidate_cursor": None,
                    "candidate_input_keys": None,
                    "candidate_finding_keys": None,
                    "completed_at": None,
                    "updated_at": now,
                }
                if row is None:
                    await connection.execute(
                        sa.insert(edition).values(
                            workspace_id=ctx.workspace_id,
                            member_id=member.id,
                            local_date=local_date.isoformat(),
                            attempt=1,
                            conversation_id=None,
                            created_at=now,
                            **values,
                        )
                    )
                else:
                    attempt = row.attempt + 1 if row.status == "failed" else row.attempt
                    await connection.execute(
                        sa.update(edition)
                        .where(
                            edition.c.workspace_id == ctx.workspace_id,
                            edition.c.member_id == member.id,
                            edition.c.local_date == local_date.isoformat(),
                        )
                        .values(attempt=attempt, **values)
                    )
            attempt = 1 if row is None else attempt
            try:
                scheduled = await ctx.invoke_agent_for_member(
                    agent_name=AGENT_NAME,
                    member_id=member.id,
                    conversation_key=key,
                    message="Prepare today's private daily brief.",
                    idempotency_key=f"{key}:{attempt}",
                )
            except (PermissionError, ValueError):
                async with ctx.transaction() as connection:
                    await connection.execute(
                        sa.update(edition)
                        .where(
                            edition.c.workspace_id == ctx.workspace_id,
                            edition.c.member_id == member.id,
                            edition.c.local_date == local_date.isoformat(),
                        )
                        .values(status="failed", updated_at=now)
                    )
                continue
            async with ctx.transaction() as connection:
                await connection.execute(
                    sa.update(edition)
                    .where(
                        edition.c.workspace_id == ctx.workspace_id,
                        edition.c.member_id == member.id,
                        edition.c.local_date == local_date.isoformat(),
                    )
                    .values(
                        conversation_id=scheduled.conversation_id,
                        turn_id=scheduled.turn_id,
                        updated_at=now,
                    )
                )
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(
            AgentProvision(
                name=AGENT_NAME,
                spec=AgentSpec(
                    model=FINAL_MODEL,
                    reasoning="high",
                    internet_access_allowed=False,
                    sandbox_size="small",
                    visibility="workspace",
                    prompt=AGENT_PROMPT,
                ),
                tools=(
                    "load_skill",
                    TOOL_NAME,
                    TASK_TOOL_NAME,
                    MEMORY_TOOL_NAME,
                    HOMEPAGE_TOOL_NAME,
                ),
            ),
        ),
        tools=(
            ToolDef(
                name=TOOL_NAME,
                description=(
                    "Collect the scheduled member's changed private context, run four bounded "
                    "scouts, and return typed findings for the daily brief. Call once."
                ),
                input_model=SweepInput,
                handler=_sweep,
                parallel_safe=False,
            ),
        ),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=JOB_SCHEDULE,
                handler=_tick,
                candidates=seated_member_workspaces(),
            ),
        ),
        hooks=(
            HookSpec(
                event="pre_tool_use",
                handler=_draft_only,
                tools=(TASK_TOOL_NAME, MEMORY_TOOL_NAME, HOMEPAGE_TOOL_NAME),
            ),
        ),
        subagents=tuple(_profile(name) for name in SCOUTS),
        skills=(SkillSpec(path=SKILL_DIR),),
        member_context_read=True,
        requires=("search_providers",),
    )
