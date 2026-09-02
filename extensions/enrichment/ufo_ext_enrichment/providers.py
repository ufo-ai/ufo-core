"""Who is behind a sign-up address: People Data Labs, or the raw bodies it already returned,
replayed from a recordings file. `UFO_ENRICHMENT_PROVIDER` picks one at boot — `pdl` (the default)
or `recorded` — and any other value refuses to boot, the way the gateway's `WORKOS_MODE` does.
`UFO_ENRICHMENT_RECORDINGS` names the file: `recorded` requires it, and `pdl` reads through it
where it is set, appending every body it fetches — to a file outside this package, since the
digest that pins a deploy hashes every file inside it. `pdl` runs on the deploy key, and a deploy
carrying none has no provider at all — the extension then registers its read alone."""

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.sdk.credentials import deploy_env
from ufo.sdk.o11y import warn
from ufo_ext_enrichment.store import (
    Company,
    Location,
    Person,
    ProfileSource,
)

PROVIDER_ENV = "UFO_ENRICHMENT_PROVIDER"
RECORDINGS_ENV = "UFO_ENRICHMENT_RECORDINGS"
PDL_MODE = "pdl"
RECORDED_MODE = "recorded"
API_KEY_ENV = "PEOPLE_DATA_LABS_API_KEY"
PDL_HOST = "api.peopledatalabs.com"
PDL_API_KEY_HEADER = "X-Api-Key"
PDL_TIMEOUT_SECONDS = 20
PERSON_PATH = "/v5/person/enrich"
COMPANY_PATH = "/v5/company/enrich"
PERSON_KEY = "person:"
COMPANY_KEY = "company:"
NOT_FOUND_STATUS = 404
PERSON_FIELDS = (
    "full_name",
    "first_name",
    "last_name",
    "job_title",
    "job_title_role",
    "job_title_levels",
    "job_company_name",
    "job_company_website",
    "linkedin_url",
    "location_name",
)
COMPANY_FIELDS = (
    "name",
    "display_name",
    "website",
    "industry",
    "size",
    "employee_count",
    "founded",
    "summary",
    "location",
    "linkedin_url",
)
MAX_EMAIL_CHARS = 254
MAX_WEBSITE_CHARS = 253
MAX_ERROR_CHARS = 2_000


class EnrichmentError(RuntimeError):
    """The provider refused a lookup, answered in an unexpected shape, or is not configured.
    `retry_after` is the delay the provider named, in seconds, where it named one."""

    retry_after: float | None = None


class RateLimited(EnrichmentError):
    """The provider's rate limit was reached; the lookup is left due for the next tick."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Provider(Protocol):
    @property
    def source(self) -> ProfileSource: ...

    async def person(self, email: str) -> Person | None: ...

    async def company(self, website: str) -> Company | None: ...


class _PdlPerson(BaseModel):
    model_config = ConfigDict(extra="ignore")
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    job_title_role: str | None = None
    job_title_levels: tuple[str, ...] = ()
    job_company_name: str | None = None
    job_company_website: str | None = None
    linkedin_url: str | None = None
    location_name: str | None = None


class _PdlPersonMatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    likelihood: int | None = None
    data: _PdlPerson


class _PdlLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    locality: str | None = None
    region: str | None = None
    country: str | None = None


class _PdlCompanyMatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    likelihood: int | None = None
    name: str | None = None
    display_name: str | None = None
    website: str | None = None
    industry: str | None = None
    size: str | None = None
    employee_count: int | None = None
    founded: int | None = None
    summary: str | None = None
    location: _PdlLocation | None = None
    linkedin_url: str | None = None


def person_from_body(body: object) -> Person | None:
    """The person a raw People Data Labs body names, or None for the body it answers a miss with.
    Both providers parse here, so a replayed body and a live one cannot read differently."""
    if _is_not_found(body):
        return None
    try:
        match = _PdlPersonMatch.model_validate(body)
    except ValidationError as error:
        raise EnrichmentError(
            f"People Data Labs {PERSON_PATH} returned an invalid response"
        ) from error
    return Person(**match.data.model_dump(), likelihood=match.likelihood)


def company_from_body(body: object) -> Company | None:
    """The company a raw People Data Labs body names, or None for the body it answers a miss
    with."""
    if _is_not_found(body):
        return None
    try:
        match = _PdlCompanyMatch.model_validate(body)
    except ValidationError as error:
        raise EnrichmentError(
            f"People Data Labs {COMPANY_PATH} returned an invalid response"
        ) from error
    location = None if match.location is None else Location(**match.location.model_dump())
    return Company(**match.model_dump(exclude={"location"}), location=location)


def _is_not_found(body: object) -> bool:
    return isinstance(body, dict) and body.get("status") == NOT_FOUND_STATUS


@dataclass(frozen=True)
class Recordings:
    """The raw People Data Labs bodies a file holds, one per `person:<email>` or
    `company:<website>` key. Every read opens the file, so a body appended by one lookup replays on
    the next; an append rewrites the whole file through a temporary beside it, under a lock, so a
    reader never sees half of one."""

    path: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def body(self, key: str) -> object | None:
        recorded = await asyncio.to_thread(self._read)
        return recorded.get(key)

    async def append(self, key: str, body: object) -> None:
        async with self.lock:
            await asyncio.to_thread(self._append, key, body)

    def _read(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        recorded = json.loads(self.path.read_text())
        if not isinstance(recorded, dict):
            raise EnrichmentError(f"{self.path} does not hold an object of recorded bodies")
        return recorded

    def _append(self, key: str, body: object) -> None:
        recorded = self._read()
        recorded[key] = body
        temp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(recorded, indent=2) + "\n")
        temp.replace(self.path)


@dataclass(frozen=True)
class RecordedProvider:
    """Replays the recorded bodies: the file answers every lookup and nothing leaves the process.
    A key the file does not hold answers no match — that is what a recording honestly knows, not a
    claim that People Data Labs found nobody."""

    recordings: Recordings

    @property
    def source(self) -> ProfileSource:
        return "recorded"

    async def person(self, email: str) -> Person | None:
        body = await self.recordings.body(PERSON_KEY + email)
        return None if body is None else person_from_body(body)

    async def company(self, website: str) -> Company | None:
        body = await self.recordings.body(COMPANY_KEY + website)
        return None if body is None else company_from_body(body)


@dataclass(frozen=True)
class PdlProvider:
    """People Data Labs over httpx, keyed by the deploy's `UFO_PEOPLE_DATA_LABS_API_KEY` read on
    each call. A 404 is a clean no-match; 429 raises `RateLimited` so the caller stops its tick;
    every other refusal raises. With recordings it is a read-through cache: a recorded key replays
    without a request, and a fetched body — a match or a 404, never a refusal — is appended."""

    transport: httpx.AsyncBaseTransport | None = None
    recordings: Recordings | None = None

    @property
    def source(self) -> ProfileSource:
        return "pdl"

    async def person(self, email: str) -> Person | None:
        if len(email) > MAX_EMAIL_CHARS:
            raise EnrichmentError(f"email cannot exceed {MAX_EMAIL_CHARS} characters")
        body = await self._body(
            PERSON_KEY + email,
            PERSON_PATH,
            {"email": email, "data_include": ",".join(PERSON_FIELDS)},
        )
        return person_from_body(body)

    async def company(self, website: str) -> Company | None:
        if len(website) > MAX_WEBSITE_CHARS:
            raise EnrichmentError(f"website cannot exceed {MAX_WEBSITE_CHARS} characters")
        body = await self._body(
            COMPANY_KEY + website,
            COMPANY_PATH,
            {"website": website, "data_include": ",".join(COMPANY_FIELDS)},
        )
        return company_from_body(body)

    async def _body(self, key: str, path: str, params: dict[str, str]) -> object:
        if self.recordings is not None:
            recorded = await self.recordings.body(key)
            if recorded is not None:
                return recorded
        body = await self._get(path, params)
        if self.recordings is not None:
            await self.recordings.append(key, body)
        return body

    async def _get(self, path: str, params: dict[str, str]) -> object:
        key = deploy_env(API_KEY_ENV)
        if not key:
            raise EnrichmentError(f"UFO_{API_KEY_ENV} (or {API_KEY_ENV}) required to enrich")
        try:
            async with httpx.AsyncClient(
                base_url=f"https://{PDL_HOST}",
                timeout=PDL_TIMEOUT_SECONDS,
                transport=self.transport,
            ) as http:
                response = await http.get(path, params=params, headers={PDL_API_KEY_HEADER: key})
        except httpx.HTTPError as error:
            raise EnrichmentError(f"People Data Labs {path} unreachable: {error}") from error
        match response.status_code:
            case 200 | 404:
                try:
                    return response.json()
                except ValueError as error:
                    raise EnrichmentError(
                        f"People Data Labs {path} returned invalid JSON"
                    ) from error
            case 429:
                raise RateLimited(
                    f"People Data Labs {path} rate limited",
                    _retry_after(response.headers.get("retry-after")),
                )
            case status:
                raise EnrichmentError(
                    f"People Data Labs {path} failed ({status}): {response.text[:MAX_ERROR_CHARS]}"
                )


def _retry_after(header: str | None) -> float | None:
    """The seconds a `Retry-After` header names, or None where it names none or names a date the
    caller's own backoff answers just as well."""
    if header is None:
        return None
    try:
        seconds = float(header.strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def provider_from_env() -> Provider | None:
    """The provider this deploy runs, or None where it holds no way to look anybody up:
    `UFO_ENRICHMENT_PROVIDER` is `pdl` unless set, `recorded` replays the file
    `UFO_ENRICHMENT_RECORDINGS` names, and anything else refuses to boot. `pdl` reads through that
    file where it is named and calls People Data Labs where it is not, so it is None without the
    deploy key: nobody can mint one, and a deploy that carries none enriches nothing rather than
    raising on every lookup."""
    mode = os.environ.get(PROVIDER_ENV, PDL_MODE).strip().lower()
    named = os.environ.get(RECORDINGS_ENV, "").strip()
    match mode:
        case "pdl":
            if not deploy_env(API_KEY_ENV):
                warn("enrichment.unkeyed", key=f"UFO_{API_KEY_ENV}")
                return None
            if not named:
                return PdlProvider()
            path = Path(named)
            if Path(__file__).parent.resolve() in path.resolve().parents:
                raise RuntimeError(
                    f"{RECORDINGS_ENV}={named} is inside the extension package, which "
                    f"{PROVIDER_ENV}={PDL_MODE} appends to: name a file outside it, because the "
                    "package's own digest pins the deploy"
                )
            return PdlProvider(recordings=Recordings(path=path))
        case "recorded":
            if not named:
                raise RuntimeError(
                    f"{RECORDINGS_ENV} names the file {PROVIDER_ENV}={RECORDED_MODE} replays"
                )
            path = Path(named)
            if not path.is_file():
                raise RuntimeError(f"{RECORDINGS_ENV}={named} is not a file")
            warn("enrichment.recorded_mode")
            return RecordedProvider(recordings=Recordings(path=path))
        case _:
            raise RuntimeError(f"{PROVIDER_ENV}={mode!r} is not {PDL_MODE}|{RECORDED_MODE}")
