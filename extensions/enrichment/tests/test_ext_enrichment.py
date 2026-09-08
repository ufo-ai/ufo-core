import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_enrichment.manifest as enrichment
import ufo_ext_enrichment.providers as providers
from ufo_ext_enrichment.providers import (
    API_KEY_ENV,
    COMPANY_FIELDS,
    COMPANY_PATH,
    PDL_API_KEY_HEADER,
    PERSON_FIELDS,
    PERSON_PATH,
    PROVIDER_ENV,
    RECORDINGS_ENV,
    EnrichmentError,
    PdlProvider,
    RecordedProvider,
    Recordings,
    provider_from_env,
)
from ufo_ext_enrichment.store import (
    MATCHED,
    NO_MATCH,
    PDL_SOURCE,
    RECORDED_SOURCE,
    Profiles,
    StoredProfile,
    enrichment_backoff,
    enrichment_consent,
    enrichment_profile,
)

from ufo.db import workspace_tx
from ufo.harness.untrusted import wall
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.object_views import ActionView, presented_action_views
from ufo.runtime.objects import BoundAction, BoundKind, action_registry, object_registry
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.manifest import HookContext, InjectContext, Manifest, UserPromptSubmit
from ufo.sdk.objects import ObjectListQuery, VerbNotSupported
from ufo.sdk.tools import SpeakerRequired

PDL_KEY = "pdl-test-key"
ALEX = "alex@simplecasual.com"
SEED = Path(providers.__file__).with_name("recordings.json")
NOT_FOUND = {
    "status": 404,
    "error": {"type": "not_found", "message": "No records were found matching your request"},
}
SCALARS = (str, int, float, bool, type(None))


class _Recorder:
    def __init__(self, responses: list[tuple[object, int]]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload, status = self._responses[len(self.requests) - 1]
        return httpx.Response(status, json=payload)


def _person_payload() -> dict[str, object]:
    return {
        "status": 200,
        "likelihood": 9,
        "data": {
            "id": "ignored",
            "full_name": "alex baldwin",
            "first_name": "alex",
            "last_name": "baldwin",
            "job_title": "founder",
            "job_title_role": "operations",
            "job_title_levels": ["owner"],
            "job_company_name": "simple casual",
            "job_company_website": "simplecasual.com",
            "linkedin_url": "linkedin.com/in/alexbaldwin",
            "location_name": "san francisco, california, united states",
        },
    }


def _company_payload() -> dict[str, object]:
    return {
        "status": 200,
        "likelihood": 10,
        "id": "ignored",
        "name": "simple casual",
        "display_name": "Simple Casual",
        "website": "simplecasual.com",
        "industry": "design",
        "size": "1-10",
        "employee_count": 4,
        "founded": 2019,
        "summary": "A design studio.",
        "location": {
            "name": "san francisco, california, united states",
            "locality": "san francisco",
            "region": "california",
            "country": "united states",
            "continent": "north america",
        },
        "linkedin_url": "linkedin.com/company/simplecasual",
        "tags": ["design"],
    }


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member(
    workspace_id: UUID, email: str, *, seated: bool = True, consented: bool = True
) -> UUID:
    """A seated member who agreed to be looked up, which is what the job asks of everyone it
    enriches. `consented=False` is the member who answered nothing."""
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=True,
                seated_at=sa.func.now() if seated else None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if consented:
            await connection.execute(
                sa.insert(enrichment_consent).values(
                    workspace_id=workspace_id,
                    member_id=member_id,
                    granted=True,
                    decided_at=datetime.now(UTC),
                )
            )
    return member_id


async def _agent(workspace_id: UUID, *, main: bool = True) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant" if main else f"app-{agent_id.hex[:8]}",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=main,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


def _ext() -> ExtensionContext:
    return context_for(enrichment.NAME, frozenset())


def _pdl(recorder: _Recorder) -> enrichment.Enrichment:
    return enrichment.Enrichment(
        provider=PdlProvider(transport=httpx.MockTransport(recorder.handle)),
    )


def _recorded(path: Path = SEED) -> enrichment.Enrichment:
    return enrichment.Enrichment(provider=RecordedProvider(recordings=Recordings(path=path)))


def _copy(tmp_path: Path, extra: dict[str, object]) -> Path:
    recorded = json.loads(SEED.read_text())
    recorded.update(extra)
    path = tmp_path / "recordings.json"
    path.write_text(json.dumps(recorded))
    return path


async def _unavailable_spawn(
    profile: str,
    payload: dict[str, object],
    background: bool = False,
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the enrichment tests")


def _turn(workspace_id: UUID, member_id: UUID | None) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        admission_source="member",
        speaker_member_id=member_id,
    )


def _tool_ctx(ext: ExtensionContext, workspace_id: UUID, member_id: UUID | None) -> ToolContext:
    audience = conversation_audience(member_id) if member_id is not None else ext.audience
    return ToolContext(
        sandbox=None,  # type: ignore[arg-type]
        blob=None,  # type: ignore[arg-type]
        turn=_turn(workspace_id, member_id),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=audience,
        artifact_token_secret="",
        ext=ext,
    )


def _hook_ctx(ext: ExtensionContext, workspace_id: UUID, member_id: UUID | None) -> HookContext:
    return HookContext(
        ext=ext,
        payload=UserPromptSubmit(text="hi"),
        turn=_turn(workspace_id, member_id),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        speaker_member_id=member_id,
    )


async def _stored(workspace_id: UUID, member_id: UUID) -> StoredProfile | None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            return await Profiles(connection, workspace_id).one(member_id)


async def _paused_for(workspace_id: UUID) -> tuple[int, bool] | None:
    """The workspace's backoff as (attempts, still waiting), or None where none stands."""
    with ws(workspace_id):
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(enrichment_backoff.c.attempts, enrichment_backoff.c.retry_after)
                )
            ).one_or_none()
    if row is None:
        return None
    retry_after = row.retry_after
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=UTC)
    return row.attempts, retry_after > datetime.now(UTC)


async def _seconds_paused(workspace_id: UUID) -> float:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            retry_after = (
                await connection.execute(sa.select(enrichment_backoff.c.retry_after))
            ).scalar_one()
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=UTC)
    return (retry_after - datetime.now(UTC)).total_seconds()


async def _lapse(workspace_id: UUID) -> None:
    """Wind the workspace's backoff back into the past, the way the wait itself ends."""
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(enrichment_backoff).values(
                    retry_after=datetime.now(UTC) - timedelta(seconds=1)
                )
            )


async def _candidates() -> set[UUID]:
    (job,) = enrichment.manifest().jobs
    return set(await job.candidates())


def _query() -> ObjectListQuery:
    return ObjectListQuery(supported_fields=enrichment.LIST_FIELDS)


def _projected(manifest: Manifest) -> tuple[ActionView, ...]:
    kinds = object_registry(
        tuple(
            BoundKind(kind=kind, extension=enrichment.NAME, context=None)
            for kind in manifest.objects
        )
    )
    actions = action_registry(
        tuple(
            BoundAction(action=tool, extension=enrichment.NAME, context=None)
            for tool in manifest.tools
        ),
        kinds,
    )
    return presented_action_views(actions, enrichment.PROFILE_KIND, "collection")


def test_manifest_declares_the_kind_the_action_the_job_and_the_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    manifest = enrichment.manifest()
    assert manifest.name == "enrichment"
    assert manifest.deploy_keys == ("PEOPLE_DATA_LABS_API_KEY",)
    (kind,) = manifest.objects
    assert kind.name == "enrichment_profile"
    assert kind.list_fields == enrichment.LIST_FIELDS
    (action,) = manifest.tools
    assert action.name == "confirm_website"
    assert action.bound is not None
    assert (action.bound.kind, action.bound.binding) == ("enrichment_profile", "collection")
    assert action.canonical_id == "action:enrichment_profile:confirm_website"
    assert action.presentation is not None
    assert action.presentation.label == "Confirm website"
    assert action.untrusted
    assert action.side_effecting
    action_schema = action.input_model.model_json_schema()
    assert action_schema["properties"]["website"]["type"] == "string"
    assert action_schema["required"] == ["website"]
    (job,) = manifest.jobs
    assert (job.name, job.schedule) == ("enrichment_enrich", "0 * * * * *")
    (hook,) = manifest.hooks
    assert (hook.event, hook.best_effort) == ("user_prompt_submit", True)
    assert not manifest.onboarding_steps
    registered = object_registry((BoundKind(kind=kind, extension=enrichment.NAME, context=None),))
    assert set(registered) == {"enrichment_profile"}
    (view,) = _projected(manifest)
    assert (view.name, view.label) == ("confirm_website", "Confirm website")


def test_a_deploy_with_no_key_registers_the_read_without_the_action_or_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    monkeypatch.delenv(RECORDINGS_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(f"UFO_{API_KEY_ENV}", raising=False)

    manifest = enrichment.manifest()

    assert provider_from_env() is None
    assert manifest.deploy_keys == (API_KEY_ENV,)
    assert (manifest.tools, manifest.jobs) == ((), ())
    assert _projected(manifest) == ()
    (kind,) = manifest.objects
    assert kind.name == "enrichment_profile"
    (hook,) = manifest.hooks
    assert (hook.event, hook.best_effort) == ("user_prompt_submit", True)


def test_provider_from_env_reads_the_mode_and_the_recordings_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    monkeypatch.delenv(RECORDINGS_ENV, raising=False)
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    plain = provider_from_env()
    assert isinstance(plain, PdlProvider)
    assert plain.recordings is None

    monkeypatch.setenv(PROVIDER_ENV, " Recorded ")
    with pytest.raises(RuntimeError, match=RECORDINGS_ENV):
        provider_from_env()
    monkeypatch.setenv(RECORDINGS_ENV, str(tmp_path / "absent.json"))
    with pytest.raises(RuntimeError, match="is not a file"):
        provider_from_env()
    monkeypatch.setenv(RECORDINGS_ENV, str(SEED))
    replaying = provider_from_env()
    assert isinstance(replaying, RecordedProvider)
    assert replaying.recordings.path == SEED

    monkeypatch.setenv(PROVIDER_ENV, "pdl")
    with pytest.raises(RuntimeError, match="inside the extension package"):
        provider_from_env()
    outside = _copy(tmp_path, {})
    monkeypatch.setenv(RECORDINGS_ENV, str(outside))
    reading_through = provider_from_env()
    assert isinstance(reading_through, PdlProvider)
    assert reading_through.recordings is not None
    assert reading_through.recordings.path == outside

    monkeypatch.setenv(PROVIDER_ENV, "clearbit")
    with pytest.raises(RuntimeError, match=r"pdl\|recorded"):
        provider_from_env()

    monkeypatch.setenv(PROVIDER_ENV, "pdl")
    monkeypatch.delenv(API_KEY_ENV)
    assert provider_from_env() is None


async def test_recorded_replays_the_seeded_bodies_and_answers_no_match_for_an_unknown_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the recorded provider must not open a network client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    provider = RecordedProvider(recordings=Recordings(path=SEED))
    assert provider.source == RECORDED_SOURCE
    assert await provider.person(ALEX) is None

    company = await provider.company("simplecasual.com")
    assert company is not None
    assert (company.name, company.display_name, company.website) == (
        "simple casual",
        "Simple Casual",
        "simplecasual.com",
    )
    assert (company.industry, company.size, company.founded) == ("internet", "1-10", 2013)
    assert (company.summary, company.likelihood) == ("keep it simple, keep it casual.", 6)
    assert company.location is None
    assert company.employee_count is None

    assert await provider.person("sam@simplecasual.com") is None
    assert await provider.company("acme.io") is None


async def test_pdl_reads_through_the_recordings_and_appends_what_it_fetches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    path = _copy(tmp_path, {})
    unauthorized = {"status": 401, "error": {"type": "authentication_error", "message": "bad key"}}
    recorder = _Recorder([(_person_payload(), 200), (NOT_FOUND, 404), (unauthorized, 401)])
    provider = PdlProvider(
        transport=httpx.MockTransport(recorder.handle), recordings=Recordings(path=path)
    )

    company = await provider.company("simplecasual.com")
    assert company is not None
    assert company.display_name == "Simple Casual"
    assert await provider.person(ALEX) is None
    assert recorder.requests == []

    person = await provider.person("sam@simplecasual.com")
    assert person is not None
    assert person.full_name == "alex baldwin"
    assert len(recorder.requests) == 1
    assert await provider.person("sam@simplecasual.com") == person
    assert len(recorder.requests) == 1

    assert await provider.company("acme.io") is None
    assert len(recorder.requests) == 2
    assert await provider.company("acme.io") is None
    assert len(recorder.requests) == 2

    with pytest.raises(EnrichmentError, match="401"):
        await provider.company("nope.io")
    recorded = json.loads(path.read_text())
    assert recorded["person:sam@simplecasual.com"] == _person_payload()
    assert recorded["company:acme.io"] == NOT_FOUND
    assert "company:nope.io" not in recorded


async def test_pdl_job_requests_the_person_then_the_company_by_the_email_domain(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"UFO_{API_KEY_ENV}", PDL_KEY)
    recorder = _Recorder([(_person_payload(), 200), (_company_payload(), 200)])
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX)

    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())

    person_request, company_request = recorder.requests
    assert person_request.url.host == "api.peopledatalabs.com"
    assert person_request.url.path == PERSON_PATH
    assert person_request.url.params["email"] == ALEX
    assert person_request.url.params["data_include"] == ",".join(PERSON_FIELDS)
    assert person_request.headers[PDL_API_KEY_HEADER] == PDL_KEY
    assert company_request.url.path == COMPANY_PATH
    assert company_request.url.params["website"] == "simplecasual.com"
    assert company_request.url.params["data_include"] == ",".join(COMPANY_FIELDS)
    assert company_request.headers[PDL_API_KEY_HEADER] == PDL_KEY

    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    profile = stored.profile
    assert (profile.email, profile.website, profile.status, profile.source) == (
        ALEX,
        None,
        MATCHED,
        PDL_SOURCE,
    )
    assert profile.person is not None
    assert profile.person.full_name == "alex baldwin"
    assert profile.person.job_title_levels == ("owner",)
    assert profile.person.likelihood == 9
    assert profile.company is not None
    assert profile.company.display_name == "Simple Casual"
    assert profile.company.employee_count == 4
    assert profile.company.founded == 2019
    assert profile.company.likelihood == 10
    assert profile.company.location is not None
    assert profile.company.location.locality == "san francisco"
    assert profile.fetched_at.tzinfo is not None
    assert enrichment.summary(profile) == "founder at Simple Casual (Design)"


async def test_the_job_looks_up_no_company_for_a_free_mail_domain(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member who signed up from their mail provider works for nobody the domain names, so the
    job asks for the person alone: the company that answers `gmail.com` would otherwise stand as
    the workspace's own and be stated to every turn."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    recorder = _Recorder([(_person_payload(), 200)])
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, "alex@gmail.com")

    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())

    (person_request,) = recorder.requests
    assert person_request.url.path == PERSON_PATH
    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert stored.profile.company is None
    assert stored.profile.status == MATCHED


async def test_the_job_enriches_nobody_who_agreed_to_nothing(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seat is not the agreement: a member who never answered the website step is no candidate
    for the job and no request is made about them, so nobody's address reaches the provider until
    they say so."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    workspace_id = await _workspace()
    silent = await _member(workspace_id, ALEX, consented=False)
    recorder = _Recorder([])

    assert await _candidates() == set()
    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())

    assert recorder.requests == []
    assert await _stored(workspace_id, silent) is None


async def test_pdl_job_looks_the_company_up_by_the_email_domain_without_a_person(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    recorder = _Recorder([(NOT_FOUND, 404), (_company_payload(), 200)])
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX)

    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())

    assert recorder.requests[1].url.params["website"] == "simplecasual.com"
    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert (stored.profile.status, stored.profile.person) == (MATCHED, None)
    assert stored.profile.company is not None
    assert enrichment.summary(stored.profile) == "Simple Casual (Design)"


async def test_pdl_no_match_is_stored_and_never_retried(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    recorder = _Recorder([(NOT_FOUND, 404), (NOT_FOUND, 404)])
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX)
    assert await _candidates() == {workspace_id}

    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())

    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert (stored.profile.status, stored.profile.person, stored.profile.company) == (
        NO_MATCH,
        None,
        None,
    )
    assert enrichment.summary(stored.profile) == "No match"
    assert await _candidates() == set()
    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())
    assert len(recorder.requests) == 2


async def test_pdl_rate_limit_pauses_the_workspace_and_leaves_the_members_due(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal stops this tick and holds the workspace out of the job's candidates until the
    backoff lapses; the members stay due, so the pause costs the enrichment time and never the
    work."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    rate_limited = {"status": 429, "error": {"type": "rate_limit_error", "message": "slow down"}}
    recorder = _Recorder([(_person_payload(), 200), (rate_limited, 429)])
    workspace_id = await _workspace()
    first = await _member(workspace_id, ALEX)
    second = await _member(workspace_id, "sam@simplecasual.com")

    with ws(workspace_id):
        await _pdl(recorder).tick(_ext())

    assert len(recorder.requests) == 2
    assert await _stored(workspace_id, first) is None
    assert await _stored(workspace_id, second) is None
    assert await _candidates() == set()
    assert await _paused_for(workspace_id) == (1, True)
    await _lapse(workspace_id)
    assert await _candidates() == {workspace_id}

    recovered = _Recorder(
        [
            (_person_payload(), 200),
            (_company_payload(), 200),
            (NOT_FOUND, 404),
            (NOT_FOUND, 404),
        ]
    )
    with ws(workspace_id):
        await _pdl(recovered).tick(_ext())
    assert len(recovered.requests) == 4
    assert await _stored(workspace_id, first) is not None
    assert await _stored(workspace_id, second) is not None
    assert await _candidates() == set()
    assert await _paused_for(workspace_id) is None


async def test_the_job_waits_the_delay_the_provider_names(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate limit that names its own delay is honoured over the doubling one, so the job comes
    back when the provider said to and not an hour later."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    workspace_id = await _workspace()
    await _member(workspace_id, ALEX)

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"status": 429}, headers={"Retry-After": "5"})

    with ws(workspace_id):
        await enrichment.Enrichment(
            provider=PdlProvider(transport=httpx.MockTransport(rate_limited))
        ).tick(_ext())

    assert await _paused_for(workspace_id) == (1, True)
    assert await _seconds_paused(workspace_id) <= 5


async def test_pdl_refusals_raise_and_the_job_pauses_on_every_one_of_them(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every refusal the provider can answer with reaches the caller as an `EnrichmentError`, and
    the job answers each one the same way: it writes no row, pauses the workspace, and doubles the
    wait — a bad key or a spent account costs one attempt a minute of waiting rather than a
    request every minute, and enrichment resumes by itself once the fault is repaired."""
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX)
    monkeypatch.delenv(f"UFO_{API_KEY_ENV}", raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    unkeyed = _Recorder([])
    with ws(workspace_id), pytest.raises(EnrichmentError, match=API_KEY_ENV):
        await PdlProvider(transport=httpx.MockTransport(unkeyed.handle)).person(ALEX)
    assert not unkeyed.requests

    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    unauthorized = {"status": 401, "error": {"type": "authentication_error", "message": "bad key"}}
    payment = {"status": 402, "error": {"type": "payment_required", "message": "no credits"}}
    refusals = (
        (unauthorized, 401, r"401.*bad key"),
        (payment, 402, "402"),
        ({"status": 503}, 503, "503"),
        ({"status": 200, "data": "wrong"}, 200, "invalid response"),
    )
    for attempt, (payload, status, message) in enumerate(refusals, start=1):
        with ws(workspace_id), pytest.raises(EnrichmentError, match=message):
            await PdlProvider(
                transport=httpx.MockTransport(_Recorder([(payload, status)]).handle)
            ).person(ALEX)
        with ws(workspace_id):
            await _pdl(_Recorder([(payload, status)])).tick(_ext())
        assert await _stored(workspace_id, member_id) is None
        assert await _paused_for(workspace_id) == (attempt, True)
        assert await _candidates() == set()
        await _lapse(workspace_id)
        assert await _candidates() == {workspace_id}


async def test_job_selects_only_workspaces_with_an_unenriched_seated_member(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    due = await _workspace()
    await _member(due, ALEX)
    unseated = await _workspace()
    await _member(unseated, "pat@unseated.test", seated=False)
    enriched = await _workspace()
    await _member(enriched, "kim@enriched.test")
    with ws(enriched):
        await _recorded().tick(_ext())
    crowded = await _workspace()
    crowd = [
        await _member(crowded, f"member{index}@crowded.test")
        for index in range(enrichment.TICK_MEMBERS + 2)
    ]

    assert await _candidates() == {due, crowded}

    with ws(due):
        await _recorded().tick(_ext())
    with ws(crowded):
        await _recorded().tick(_ext())
    written = [await _stored(crowded, member_id) for member_id in crowd]
    assert sum(row is not None for row in written) == enrichment.TICK_MEMBERS
    assert await _candidates() == {crowded}

    with ws(crowded):
        await _recorded().tick(_ext())
    rows = [await _stored(crowded, member_id) for member_id in crowd]
    assert all(row is not None for row in rows)
    assert await _candidates() == set()


async def test_confirm_website_records_the_agreement_and_the_job_builds_the_row(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirming is the agreement and never the lookup: no request leaves the process while the
    member waits on the form, and the job that runs the minute after builds the row by the website
    they confirmed. A re-confirm drops the row and makes the member due again, so the second
    website is looked up in place of the first."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX, consented=False)
    ext = _ext()
    recorder = _Recorder([])

    with ws(workspace_id):
        result = await _pdl(recorder).confirm_website(
            _tool_ctx(ext, workspace_id, member_id),
            enrichment.ConfirmWebsiteInput(website="https://www.Simplecasual.com/about"),
        )
    (content,) = result.content
    assert content.text == enrichment.BUILDING_REPLY
    assert recorder.requests == []
    assert await _stored(workspace_id, member_id) is None
    assert await _candidates() == {workspace_id}

    with ws(workspace_id):
        await _recorded().tick(ext)
    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert (stored.profile.website, stored.profile.status, stored.profile.source) == (
        "simplecasual.com",
        MATCHED,
        RECORDED_SOURCE,
    )
    assert stored.profile.company is not None
    assert stored.profile.company.display_name == "Simple Casual"
    assert stored.profile.person is None
    assert await _candidates() == set()

    with ws(workspace_id):
        reconfirmed = await _recorded().confirm_website(
            _tool_ctx(ext, workspace_id, member_id),
            enrichment.ConfirmWebsiteInput(website="beta.co"),
        )
    (content,) = reconfirmed.content
    assert content.text == enrichment.BUILDING_REPLY
    assert await _stored(workspace_id, member_id) is None
    assert await _candidates() == {workspace_id}

    with ws(workspace_id):
        await _recorded().tick(ext)
    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert (stored.profile.website, stored.profile.status) == ("beta.co", NO_MATCH)
    assert stored.profile.company is None
    async with workspace_tx() as connection:
        with ws(workspace_id):
            count = len(await Profiles(connection, workspace_id).rows(enrichment.LIST_MAX))
    main = await _agent(workspace_id)
    with ws(workspace_id), agent(main):
        page = await enrichment.ProfileObjects().member_page(
            ext, member_id=member_id, admin=False, query=_query()
        )
    assert count == 1
    assert page.rows[0].fields["website"] == "beta.co"
    assert await _candidates() == set()


async def test_confirm_website_without_a_website_looks_nobody_up(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step says clearing the field skips the lookup, and it does: no request leaves the
    process, the member's address included, and the member is left out of the job as somebody who
    withheld their agreement. A row an earlier confirmation wrote is dropped."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX, consented=False)
    ext = _ext()
    recorder = _Recorder([])

    with ws(workspace_id):
        result = await _pdl(recorder).confirm_website(
            _tool_ctx(ext, workspace_id, member_id),
            enrichment.ConfirmWebsiteInput(website=""),
        )

    assert recorder.requests == []
    (content,) = result.content
    assert content.text == enrichment.SKIPPED_REPLY
    assert await _stored(workspace_id, member_id) is None
    assert await _candidates() == set()

    with ws(workspace_id):
        await _recorded().confirm_website(
            _tool_ctx(ext, workspace_id, member_id),
            enrichment.ConfirmWebsiteInput(website="simplecasual.com"),
        )
        await _recorded().tick(ext)
    assert await _stored(workspace_id, member_id) is not None

    cleared = _Recorder([])
    with ws(workspace_id):
        result = await _pdl(cleared).confirm_website(
            _tool_ctx(ext, workspace_id, member_id),
            enrichment.ConfirmWebsiteInput(website="  "),
        )
    (content,) = result.content
    assert content.text == enrichment.SKIPPED_REPLY
    assert cleared.requests == []
    assert await _stored(workspace_id, member_id) is None
    assert await _candidates() == set()


async def test_a_confirmed_mail_provider_names_no_company(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The website box opens on the sign-up domain, so a member who signed up from their mail
    provider can confirm it in one press. The provider behind `gmail.com` is nobody's company:
    the confirmation records no website, and the job asks for the person alone."""
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, "alex@gmail.com", consented=False)
    ext = _ext()
    recorder = _Recorder([(_person_payload(), 200)])

    with ws(workspace_id):
        await _pdl(recorder).confirm_website(
            _tool_ctx(ext, workspace_id, member_id),
            enrichment.ConfirmWebsiteInput(website="https://www.gmail.com"),
        )
        await _pdl(recorder).tick(ext)

    (person_request,) = recorder.requests
    assert person_request.url.path == PERSON_PATH
    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert (stored.profile.website, stored.profile.company) == (None, None)
    assert stored.profile.status == MATCHED


async def test_a_looked_up_summary_is_one_line(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    person_payload = _person_payload()
    person_data = cast(dict[str, object], person_payload["data"])
    person_data["job_title"] = "founder\noperator"
    company_payload = _company_payload()
    company_payload["display_name"] = "Simple\nCasual"
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX)

    with ws(workspace_id):
        await _pdl(_Recorder([(person_payload, 200), (company_payload, 200)])).tick(_ext())

    stored = await _stored(workspace_id, member_id)
    assert stored is not None
    assert enrichment.summary(stored.profile) == "founder operator at Simple Casual (Design)"


async def test_confirm_website_refuses_an_unseated_member_and_a_bad_website(db: None) -> None:
    workspace_id = await _workspace()
    unseated = await _member(workspace_id, "pat@simplecasual.com", seated=False)
    seated = await _member(workspace_id, ALEX)
    ext = _ext()

    with ws(workspace_id), pytest.raises(ValueError, match="seated"):
        await _recorded().confirm_website(
            _tool_ctx(ext, workspace_id, unseated),
            enrichment.ConfirmWebsiteInput(website="simplecasual.com"),
        )
    with ws(workspace_id), pytest.raises(SpeakerRequired):
        await _recorded().confirm_website(
            _tool_ctx(ext, workspace_id, None),
            enrichment.ConfirmWebsiteInput(website="simplecasual.com"),
        )
    with ws(workspace_id), pytest.raises(ValueError, match="not a website"):
        await _recorded().confirm_website(
            _tool_ctx(ext, workspace_id, seated),
            enrichment.ConfirmWebsiteInput(website="simplecasual"),
        )
    assert await _stored(workspace_id, unseated) is None
    assert await _stored(workspace_id, seated) is None


async def test_object_read_returns_one_flat_row_per_member_named_by_email(db: None) -> None:
    workspace_id = await _workspace()
    member_id = await _member(workspace_id, ALEX)
    ext = _ext()
    store = enrichment.ProfileObjects()
    main = await _agent(workspace_id)
    app = await _agent(workspace_id, main=False)
    with ws(workspace_id):
        await _recorded().tick(ext)
        stored = await _stored(workspace_id, member_id)
        with agent(main):
            page = await store.member_page(ext, member_id=member_id, admin=False, query=_query())
            detail = await store.member_detail(ext, ALEX, member_id=member_id, admin=False)
            missing = await store.member_detail(
                ext, "nobody@simplecasual.com", member_id=member_id, admin=False
            )
        with agent(app):
            fanned = await store.member_page(ext, member_id=member_id, admin=False, query=_query())
            off_lane = await store.member_detail(ext, ALEX, member_id=member_id, admin=False)
        listed = await store.list(_tool_ctx(ext, workspace_id, member_id), _query())
        got = await store.get(_tool_ctx(ext, workspace_id, member_id), ALEX)

    assert fanned.rows == ()
    assert off_lane is None

    assert stored is not None
    assert stored.profile.company is not None
    (row,) = page.rows
    assert row.name == ALEX
    assert row.summary == "Simple Casual (Internet)"
    assert row.fields == {
        "email": ALEX,
        "website": None,
        "status": "matched",
        "source": "recorded",
        "full_name": None,
        "job_title": None,
        "job_title_role": None,
        "job_title_levels": None,
        "company_name": "Simple Casual",
        "company_industry": "internet",
        "company_size": "1-10",
        "company_founded": 2013,
        "company_location": None,
        "company_website": "simplecasual.com",
        "company_summary": "keep it simple, keep it casual.",
    }
    assert set(row.fields) == enrichment.LIST_FIELDS
    assert all(isinstance(value, SCALARS) for value in row.fields.values())
    assert page.next_cursor is None
    assert detail is not None
    assert detail.row == row
    assert detail.detail.spec == stored.profile
    assert detail.detail.created_at == stored.profile.fetched_at
    assert listed.rows == page.rows
    assert got is not None
    assert got.spec == stored.profile
    assert missing is None

    refused = cast(ToolContext, SimpleNamespace(ext=ext))
    with pytest.raises(VerbNotSupported, match="confirm_website"):
        await store.apply(refused, ALEX, stored.profile, None, expected_generation=None)
    with pytest.raises(VerbNotSupported, match="confirm_website"):
        await store.delete(refused, ALEX, expected_generation=None)


async def test_hook_injects_a_walled_summary_and_nothing_for_an_unmatched_workspace(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_id = await _workspace()
    founder = await _member(workspace_id, ALEX)
    joiner = await _member(workspace_id, "sam@simplecasual.com")
    ext = _ext()
    recordings = _copy(tmp_path, {f"person:{ALEX}": _person_payload()})
    with ws(workspace_id):
        await _recorded(recordings).tick(ext)
        stored = await _stored(workspace_id, founder)
        spoken = await enrichment.inject(_hook_ctx(ext, workspace_id, founder))
        without_turn = await enrichment.inject(
            HookContext(
                ext=ext,
                payload=UserPromptSubmit(text="hi"),
                speaker_member_id=founder,
            )
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(enrichment_profile).where(enrichment_profile.c.member_id == joiner)
            )
        joined = await enrichment.inject(_hook_ctx(ext, workspace_id, joiner))

    label = enrichment.SOURCE_LABELS[RECORDED_SOURCE]
    assert stored is not None
    assert stored.profile.company is not None
    assert isinstance(spoken, InjectContext)
    assert spoken.text.startswith(enrichment.PREFACE)
    walled = wall(
        label,
        "company: Simple Casual — internet, 1-10\nmember: alex baldwin — founder",
    )
    assert spoken.text == enrichment.PREFACE + walled
    assert len(spoken.text) - len(wall(label, "")) <= enrichment.INJECT_MAX_CHARS
    assert without_turn is None
    assert isinstance(joined, InjectContext)
    assert "company: Simple Casual" in joined.text
    assert "member:" not in joined.text

    monkeypatch.setenv(API_KEY_ENV, PDL_KEY)
    unmatched = await _workspace()
    stranger = await _member(unmatched, "nobody@unknown.test")
    with ws(unmatched):
        empty = await enrichment.inject(_hook_ctx(ext, unmatched, stranger))
        await _pdl(_Recorder([(NOT_FOUND, 404), (NOT_FOUND, 404)])).tick(ext)
        no_match = await enrichment.inject(_hook_ctx(ext, unmatched, stranger))
    assert empty is None
    assert no_match is None
