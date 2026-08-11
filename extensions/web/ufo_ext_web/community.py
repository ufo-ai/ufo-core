"""Community skills: the portal's read of the skills.sh directory — the listing behind the Skills
tab's Community narrowing and the one-skill fetch its Install reviews. With no query the listing is
the directory's leaderboard, which the site publishes in its own page payload; with one it is the
public search endpoint the skills CLI queries. Both state a skill's name, its source repository and
its install count, and neither states a description: the directory publishes one only on the
skill's own page, and the endpoints that would carry it are rate limited to 60 requests an hour, so
a row's description is read at Install and never per listed row. Every listing is held for
`CACHE_SECONDS` and every fetched document for the life of the process, so a member who opens the
narrowing again, or re-opens a skill they already read, reads the directory no further. A refusal
carries the sentence the member reads under the toast's title."""

import json
import re
import time
from dataclasses import dataclass, field

import httpx
import yaml
from pydantic import BaseModel

SEARCH_URL = "https://skills.sh/api/search"
LEADERBOARD_URL = "https://skills.sh/"
DOCUMENT_URL = "https://skills.sh/api/download/{owner}/{repo}/{skill}"
LISTING_LIMIT = 24
LISTING_TIMEOUT_SECONDS = 30
FETCH_TIMEOUT_SECONDS = 30
CACHE_SECONDS = 15 * 60
CACHE_LISTINGS = 64
CACHE_DOCUMENTS = 256
RATE_LIMITED = 429
LEADERBOARD_MAX_BYTES = 4 * 1024 * 1024
DOCUMENT_MAX_BYTES = 4 * 1024 * 1024
LEADERBOARD_ENTRY = re.compile(r"\{[^{}]*\"skillId\":\"[^\"]+\"[^{}]*\}")
SOURCE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)


class CommunityUnavailable(RuntimeError):
    """The directory answered a failure or a shape we do not recognise — surfaced as the route's
    502 and read by the member as the toast's second line, never an empty answer."""


def _refusal(code: int) -> CommunityUnavailable:
    if code == RATE_LIMITED:
        return CommunityUnavailable(
            "The skill directory limits reads to 60 an hour and this deploy has reached it. "
            "Try again in a few minutes."
        )
    return CommunityUnavailable(f"The skill directory answered {code}.")


class CommunitySkill(BaseModel):
    name: str
    source: str
    installs: int


class CommunityDocument(BaseModel):
    name: str
    description: str
    instructions: str
    document: str


@dataclass
class CommunityCache:
    listings: dict[str, tuple[float, list[CommunitySkill]]] = field(default_factory=dict)
    documents: dict[str, CommunityDocument | None] = field(default_factory=dict)


@dataclass(frozen=True)
class CommunitySkills:
    """The two reads the Community narrowing makes, over async httpx. `transport` is the
    testability seam a test injects a `MockTransport` on; production leaves it None."""

    transport: httpx.AsyncBaseTransport | None = None
    cache: CommunityCache = field(default_factory=CommunityCache)

    async def listing(self, query: str) -> list[CommunitySkill]:
        held = self.cache.listings.get(query)
        if held is not None and time.monotonic() - held[0] < CACHE_SECONDS:
            return held[1]
        async with self._client(LISTING_TIMEOUT_SECONDS) as client:
            found = await (self._search(client, query) if query else self._popular(client))
        listed = found[:LISTING_LIMIT]
        if len(self.cache.listings) >= CACHE_LISTINGS:
            self.cache.listings.clear()
        self.cache.listings[query] = (time.monotonic(), listed)
        return listed

    async def fetch(self, source: str, name: str) -> CommunityDocument | None:
        held = f"{source}/{name}"
        if held in self.cache.documents:
            return self.cache.documents[held]
        async with self._client(FETCH_TIMEOUT_SECONDS) as client:
            owner, repo = source.split("/", 1)
            body = await self._body(
                client,
                DOCUMENT_URL.format(owner=owner, repo=repo, skill=name),
                DOCUMENT_MAX_BYTES,
            )
        try:
            files = json.loads(body).get("files", [])
        except json.JSONDecodeError as fault:
            raise CommunityUnavailable(
                f"The skill directory answered no readable document for {source}/{name}."
            ) from fault
        document = None
        for file in files:
            if isinstance(file, dict) and file.get("path") == "SKILL.md":
                document = self._parse(str(file.get("contents") or ""))
                break
        if len(self.cache.documents) >= CACHE_DOCUMENTS:
            self.cache.documents.clear()
        self.cache.documents[held] = document
        return document

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport, follow_redirects=True)

    async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]:
        body = await self._body(
            client, LEADERBOARD_URL, LEADERBOARD_MAX_BYTES, headers={"RSC": "1"}
        )
        ranked: dict[str, CommunitySkill] = {}
        for matched in LEADERBOARD_ENTRY.finditer(body.decode("utf-8", errors="replace")):
            try:
                entry = json.loads(matched.group(0))
            except json.JSONDecodeError:
                continue
            skill = self._entry(entry)
            if skill is not None:
                ranked.setdefault(f"{skill.source}/{skill.name}", skill)
        if not ranked:
            raise CommunityUnavailable("The skill directory published no listing.")
        return sorted(ranked.values(), key=lambda skill: skill.installs, reverse=True)

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]:
        response = await client.get(SEARCH_URL, params={"q": query, "limit": str(LISTING_LIMIT)})
        if response.status_code != 200:
            raise _refusal(response.status_code)
        found = [self._entry(entry) for entry in response.json().get("skills", [])]
        return sorted(
            (skill for skill in found if skill is not None),
            key=lambda skill: skill.installs,
            reverse=True,
        )

    def _entry(self, entry: object) -> CommunitySkill | None:
        if not isinstance(entry, dict):
            return None
        name = str(entry.get("skillId") or entry.get("name") or "")
        source = str(entry.get("source") or "")
        if not name or not SOURCE.match(source):
            return None
        return CommunitySkill(name=name, source=source, installs=int(entry.get("installs") or 0))

    async def _body(
        self,
        client: httpx.AsyncClient,
        url: str,
        cap: int,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        chunks: list[bytes] = []
        read = 0
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                raise _refusal(response.status_code)
            async for chunk in response.aiter_bytes():
                read += len(chunk)
                if read > cap:
                    raise CommunityUnavailable(
                        "The skill directory answered more than the "
                        f"{cap // (1024 * 1024)}MB this read accepts."
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    def _parse(self, document: str) -> CommunityDocument | None:
        matched = FRONTMATTER.match(document)
        if matched is None:
            return None
        try:
            matter = yaml.safe_load(matched.group(1))
        except yaml.YAMLError:
            return None
        if not isinstance(matter, dict):
            return None
        name = str(matter.get("name") or "")
        description = str(matter.get("description") or "")
        if not name or not description:
            return None
        return CommunityDocument(
            name=name,
            description=description,
            instructions=matched.group(2).strip(),
            document=document,
        )


COMMUNITY = CommunitySkills()
