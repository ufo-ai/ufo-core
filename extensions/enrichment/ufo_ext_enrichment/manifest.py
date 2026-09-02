"""Who a member works for, guessed from the sign-up address and the website they confirm.

One producer writes `enrichment_profile`: a per-minute job that enriches the seated members who
agreed to be looked up and still hold no row. The `confirm_website` action on the kind's collection
— the portal's form posts it — is what grants that agreement: it records the consent and the
website and looks nobody up itself, so the job reaches every consenting member, whether they
confirmed in the first run or answered later. Nobody is looked up without the agreement: confirming
a website grants it, clearing the field withholds it, and a member who answered nothing is never
sent anywhere. Two consumers read the rows: the read-only `enrichment_profile` kind the portal
lists, and a best-effort `user_prompt_submit` hook that hands each turn a short walled summary of
the company and the speaker.

Both the action and the job need a provider, so a deploy holding none — `pdl` with no People Data
Labs key — registers the read and the hook alone: no `confirm_website` action, no job. The portal's
first run then offers no website step and the member states the business themselves."""

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import HookContext, HookOutcome, HookSpec, InjectContext, Manifest
from ufo.sdk.o11y import warn
from ufo.sdk.objects import (
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_agent_id,
    object_page,
)
from ufo.sdk.subjects import SHARED_SUBJECT
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    SpeakerRequired,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)
from ufo.sdk.untrusted import wall
from ufo_ext_enrichment.providers import (
    API_KEY_ENV,
    MAX_WEBSITE_CHARS,
    EnrichmentError,
    Provider,
    provider_from_env,
)
from ufo_ext_enrichment.store import (
    PDL_SOURCE,
    RECORDED_SOURCE,
    Backoff,
    Company,
    Consents,
    Person,
    Profile,
    Profiles,
    ProfileStatus,
    StoredProfile,
    agent_is_main,
    due_workspaces,
)

NAME = "enrichment"
VERSION = "0.1.0"
PROFILE_KIND = "enrichment_profile"
CONFIRM_WEBSITE_ACTION = "confirm_website"
CONFIRM_WEBSITE_LABEL = "Confirm website"
ENRICH_JOB = "enrichment_enrich"
ENRICH_SCHEDULE = "0 * * * * *"
TICK_MEMBERS = 5
LIST_MAX = 500
SUMMARY_MAX = 120
VALUE_MAX_CHARS = 40
INJECT_MAX_CHARS = 400
NO_MATCH_SUMMARY = "No match"
SKIPPED_REPLY = "Skipped: nothing was looked up."
BUILDING_REPLY = "Confirmed. The profile is being built and appears within a minute."
# The domains a member signs up from that are their mail provider and never their company. A
# lookup on one of these stores the mail provider as the workspace's company and states it to
# every turn, so the job looks up no company at all for them.
FREE_MAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "googlemail.com",
        "gmx.com",
        "gmx.net",
        "hey.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "yahoo.com",
        "yandex.com",
        "ymail.com",
        "zoho.com",
    }
)
SOURCE_LABELS = {
    PDL_SOURCE: "People Data Labs",
    RECORDED_SOURCE: "a recorded People Data Labs lookup",
}
PREFACE = (
    "Starting guess about who this workspace works for, looked up from the sign-up address; "
    "nobody verified it. Believe the member over it wherever the two differ.\n"
)
WRITE_REFUSAL = (
    "enrichment profiles are looked up from the sign-up address and the confirmed website — "
    "confirm_website is the write path; apply and delete are refused"
)
LIST_FIELDS = frozenset(
    {
        "email",
        "website",
        "status",
        "source",
        "full_name",
        "job_title",
        "job_title_role",
        "job_title_levels",
        "company_name",
        "company_industry",
        "company_size",
        "company_founded",
        "company_location",
        "company_website",
        "company_summary",
    }
)


class ConfirmWebsiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    website: str = Field(
        max_length=MAX_WEBSITE_CHARS,
        description=(
            "The company website the speaking member confirms, as a domain or URL. Confirming one "
            "is how a member agrees to be looked up: the job builds their profile within a "
            "minute. Empty clears it: nothing is looked up, the stored row is dropped, and the "
            "job leaves that member alone."
        ),
    )


def website_host(raw: str) -> str | None:
    """The host a member's website field names — scheme, path, and a leading `www.` dropped — or
    None for an empty field. A value with no dot in its host is not a website and raises."""
    text = raw.strip().lower()
    if not text:
        return None
    parsed = urlsplit(text if "//" in text else f"//{text}")
    host = (parsed.hostname or "").removeprefix("www.")
    if not host or "." not in host:
        raise ValueError(f"{raw!r} is not a website")
    return host


@dataclass(frozen=True)
class Enrichment:
    """The consent the member gives and the job that acts on it, over one provider chosen at
    boot."""

    provider: Provider

    async def confirm_website(self, ctx: ToolContext, args: ConfirmWebsiteInput) -> ToolResult:
        """What the member answered about being looked up, and nothing more: the agreement and the
        website are recorded, any row an earlier answer wrote is dropped, and the job builds the
        profile on its next minute. A website that names a mail provider is recorded as no website,
        so the provider behind `gmail.com` never stands as the workspace's company."""
        ext = _require_ext(ctx.ext)
        if ctx.speaker_member_id is None:
            raise SpeakerRequired("confirming a website requires a speaking member")
        website = website_host(args.website)
        async with ext.transaction() as connection:
            speaker = await Profiles(connection, ext.workspace_id).seated(ctx.speaker_member_id)
        if speaker is None:
            raise ValueError("only a seated member confirms their website")
        granted = website is not None
        async with ext.transaction() as connection:
            await Consents(connection, ext.workspace_id).record(
                speaker.member_id,
                granted=granted,
                website=None if website in FREE_MAIL_DOMAINS else website,
            )
            await Profiles(connection, ext.workspace_id).forget(speaker.member_id)
        return ToolResult(content=(TextContent(text=BUILDING_REPLY if granted else SKIPPED_REPLY),))

    async def tick(self, ctx: ExtensionContext) -> None:
        """The members who agreed and hold no row yet, enriched one at a time. Any refusal the
        provider answers with — a rate limit, a bad key, a spent account, a fault, a body that
        does not parse — pauses this workspace for the delay it asked for or a doubling one, so a
        failing provider costs one attempt a pause rather than a request every minute."""
        async with ctx.transaction() as connection:
            due = await Profiles(connection, ctx.workspace_id).due(TICK_MEMBERS)
        for seated in due:
            try:
                profile = await self._lookup(seated.email, seated.website)
            except EnrichmentError as error:
                async with ctx.transaction() as connection:
                    seconds = await Backoff(connection, ctx.workspace_id).pause(error.retry_after)
                warn("enrichment.paused", pending=len(due), seconds=seconds)
                return
            async with ctx.transaction() as connection:
                await Profiles(connection, ctx.workspace_id).write(seated.member_id, profile)
        if due:
            async with ctx.transaction() as connection:
                await Backoff(connection, ctx.workspace_id).clear()

    async def _lookup(self, email: str, website: str | None) -> Profile:
        """The member by their address, and their company by the website they confirmed or, where
        they confirmed none, by the domain of the address — except where that domain is a mail
        provider, which names nobody's company."""
        person = await self.provider.person(email)
        domain = website or email.rpartition("@")[2]
        company = None if domain in FREE_MAIL_DOMAINS else await self.provider.company(domain)
        return self._profile(email, website, person, company)

    def _profile(
        self, email: str, website: str | None, person: Person | None, company: Company | None
    ) -> Profile:
        status: ProfileStatus = (
            "matched" if person is not None or company is not None else "no_match"
        )
        return Profile(
            email=email,
            website=website,
            status=status,
            source=self.provider.source,
            person=person,
            company=company,
            fetched_at=datetime.now(UTC),
        )


async def inject(ctx: HookContext) -> HookOutcome:
    """Every member turn opens holding the workspace's company and the speaker's title, walled as
    third-party data — nothing when no row matched. It reads the rows alone, so a deploy that lost
    its provider still hands the agent what an earlier one looked up."""
    if ctx.turn is None:
        return None
    async with ctx.ext.transaction() as connection:
        profiles = Profiles(connection, ctx.ext.workspace_id)
        speaker = (
            None if ctx.speaker_member_id is None else await profiles.one(ctx.speaker_member_id)
        )
        rows = await profiles.rows(LIST_MAX)
    ordered: tuple[StoredProfile, ...] = (*(() if speaker is None else (speaker,)), *rows)
    with_company = next((row for row in ordered if row.profile.company is not None), None)
    person = None if speaker is None else speaker.profile.person
    lines = [
        *_company_lines(None if with_company is None else with_company.profile.company),
        *_member_lines(person),
    ]
    if not lines:
        return None
    source = (with_company or speaker or rows[0]).profile.source
    return InjectContext(text=PREFACE + wall(SOURCE_LABELS[source], "\n".join(lines)))


def _company_lines(company: Company | None) -> list[str]:
    if company is None or not (company.display_name or company.name):
        return []
    location = None if company.location is None else company.location.name
    detail = ", ".join(
        _clip(part, VALUE_MAX_CHARS) for part in (company.industry, company.size, location) if part
    )
    name = _clip(company.display_name or company.name or "", VALUE_MAX_CHARS)
    return [f"company: {name} — {detail}" if detail else f"company: {name}"]


def _member_lines(person: Person | None) -> list[str]:
    if person is None or not (person.full_name or person.job_title):
        return []
    parts = [_clip(part, VALUE_MAX_CHARS) for part in (person.full_name, person.job_title) if part]
    return [f"member: {' — '.join(parts)}"]


def _clip(value: str, limit: int) -> str:
    one_line = " ".join(value.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def summary(profile: Profile) -> str:
    """The row's one line: `Founder at Simplecasual (Design)`, whichever parts are known."""
    person, company = profile.person, profile.company
    title = None if person is None else person.job_title
    name = None if company is None else (company.display_name or company.name)
    lead = " at ".join(part for part in (title, name) if part)
    if not lead:
        lead = (person.full_name if person is not None else None) or ""
    if not lead:
        return NO_MATCH_SUMMARY
    industry = None if company is None else company.industry
    line = f"{lead} ({industry[:1].upper()}{industry[1:]})" if industry else lead
    return _clip(line, SUMMARY_MAX)


def _row(profile: Profile) -> ObjectRow:
    person, company = profile.person, profile.company
    fields: dict[str, JsonValue] = {
        "email": profile.email,
        "website": profile.website,
        "status": profile.status,
        "source": profile.source,
        "full_name": None if person is None else person.full_name,
        "job_title": None if person is None else person.job_title,
        "job_title_role": None if person is None else person.job_title_role,
        "job_title_levels": (
            ",".join(person.job_title_levels) if person and person.job_title_levels else None
        ),
        "company_name": None if company is None else (company.display_name or company.name),
        "company_industry": None if company is None else company.industry,
        "company_size": None if company is None else company.size,
        "company_founded": None if company is None else company.founded,
        "company_location": (
            None if company is None or company.location is None else company.location.name
        ),
        "company_website": None if company is None else company.website,
        "company_summary": None if company is None else company.summary,
    }
    return ObjectRow(name=profile.email, summary=summary(profile), fields=fields)


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("enrichment objects dispatched without their ExtensionContext")
    return ext


@dataclass(frozen=True)
class ProfileObjects:
    """Read-only handlers over the workspace's rows, named by member email. A member of the
    workspace reads every row; a foreign room (no shared subject) reads none. Both mutations
    refuse — `confirm_website` is the one write path a member drives."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        if SHARED_SUBJECT not in ctx.read_subjects:
            return object_page((), query)
        return await self._page(_require_ext(ctx.ext), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The workspace's rows off the main agent, nothing off any other — the narrowing the core
        member kind makes. The portal's index fans out over every agent, and these rows belong to
        the workspace rather than to one lane, so without it each row stands once per agent."""
        extension = _require_ext(ext)
        async with extension.transaction() as connection:
            main = await agent_is_main(connection, extension.workspace_id, object_agent_id())
        if not main:
            return object_page((), query)
        return await self._page(extension, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[Profile] | None:
        if SHARED_SUBJECT not in ctx.read_subjects:
            return None
        found = await self._entry(_require_ext(ctx.ext), name)
        return None if found is None else found.detail

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[Profile] | None:
        """One row off the main agent and none off any other — the narrowing `member_page` makes,
        because a row belongs to the workspace rather than to one lane."""
        extension = _require_ext(ext)
        async with extension.transaction() as connection:
            main = await agent_is_main(connection, extension.workspace_id, object_agent_id())
        if not main:
            return None
        return await self._entry(extension, name)

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        return None

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: Profile,
        old: Profile | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(WRITE_REFUSAL)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(WRITE_REFUSAL)

    async def _page(self, ext: ExtensionContext, query: ObjectListQuery) -> ObjectPage:
        async with ext.transaction() as connection:
            rows = await Profiles(connection, ext.workspace_id).rows(LIST_MAX)
        return object_page(tuple(_row(stored.profile) for stored in rows), query)

    async def _entry(self, ext: ExtensionContext, name: str) -> MemberObject[Profile] | None:
        async with ext.transaction() as connection:
            stored = await Profiles(connection, ext.workspace_id).by_email(name)
        if stored is None:
            return None
        return MemberObject(
            row=_row(stored.profile),
            detail=ObjectDetail(
                spec=stored.profile,
                created_at=stored.profile.fetched_at,
                updated_at=stored.profile.fetched_at,
            ),
        )


PROFILE_OBJECT = ObjectKind(
    name=PROFILE_KIND,
    description=(
        "Who each member works for, guessed from their sign-up address and the website they "
        "confirmed. Read-only; confirm_website is the one write."
    ),
    guidance=(
        "One row per seated member, named by their email. Each carries status (matched or "
        "no_match), source (pdl or recorded), the confirmed website, the person's name and title, "
        "and the company's name, industry, size, location, website, and summary. Every value is a "
        "starting guess from an external data source and never a statement of fact — believe the "
        "member over it. Apply and delete are refused; a member confirms their website through the "
        "confirm_website action, and the job builds the row from it within a minute."
    ),
    spec_model=Profile,
    store=ProfileObjects(),
    list_fields=LIST_FIELDS,
)


def manifest() -> Manifest:
    """The read and the hook always; the two producers where the deploy holds a provider. Without
    one the action is not declared — the portal's first run reads the acts this kind presents, so a
    deploy that cannot look a website up never offers the step that asks for one — and the job is
    not scheduled, which would otherwise raise on every workspace every minute."""
    provider = provider_from_env()
    tools: tuple[ToolDef, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    if provider is not None:
        enrichment = Enrichment(provider=provider)
        tools = (
            ToolDef(
                name=CONFIRM_WEBSITE_ACTION,
                description=(
                    "Confirm the speaking member's company website, which agrees to a lookup of "
                    "who they work for; the job builds the profile within a minute. An empty "
                    "website clears it and looks nobody up."
                ),
                input_model=ConfirmWebsiteInput,
                handler=enrichment.confirm_website,
                untrusted=True,
                side_effecting=True,
                bound=ObjectBinding(kind=PROFILE_KIND, binding="collection"),
                presentation=ActionPresentation(label=CONFIRM_WEBSITE_LABEL),
            ),
        )
        jobs = (
            JobSpec(
                name=ENRICH_JOB,
                schedule=ENRICH_SCHEDULE,
                handler=enrichment.tick,
                candidates=owner_candidates(due_workspaces),
            ),
        )
    return Manifest(
        name=NAME,
        version=VERSION,
        deploy_keys=(API_KEY_ENV,),
        tools=tools,
        objects=(PROFILE_OBJECT,),
        jobs=jobs,
        hooks=(HookSpec(event="user_prompt_submit", handler=inject, best_effort=True),),
    )
