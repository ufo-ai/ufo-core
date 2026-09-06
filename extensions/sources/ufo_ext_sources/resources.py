"""What a link names, and which synced pages are about it.

A trigger narrows to one resource of a source — a pull request, an issue — and the URL a person
writes for it is its identity: `resource` on the trigger row is that link in canonical form, the
alert names it back, and a page is about it when the provider's own record links to it. A page body
is the provider's JSON, so the resource's URLs are in it wherever the provider linked them — a pull
request's `html_url`, a comment's `issue_url`, a workflow run's `pull_requests[].url` — and matching
on those needs no per-provider record schema.

Each provider owns its two rules — what a link canonicalizes to and which URL forms its records
carry — in its own module, and `RESOURCE_RULES` is the table this module dispatches on. A provider
absent from it names no resource and matches no page: a narrowed trigger is a promise about one
thing, and a promise nothing can read wakes nobody rather than everybody."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

from ufo_ext_sources.providers import github

RESOURCE_DIGEST_HEX = 8
_ALIAS_END = r"(?![A-Za-z0-9_-])"


@dataclass(frozen=True)
class ResourceRules:
    """One provider's resource rules. `canonical` reads a link and answers the one URL the resource
    is stored under, or None for a link that names nothing a trigger narrows to. `aliases` answers
    the URL forms the provider's own records carry for a canonical resource — what a page body is
    matched on."""

    canonical: Callable[[str], str | None]
    aliases: Callable[[str], tuple[str, ...]]


RESOURCE_RULES: dict[str, ResourceRules] = {
    github.GitHubConnector.name: ResourceRules(
        canonical=github.resource_url, aliases=github.resource_aliases
    ),
}


def canonical_resource(provider: str, url: str) -> str | None:
    """The URL a narrowed trigger on this provider stores for `url`, or None where the link names
    nothing the provider's rules read."""
    rules = RESOURCE_RULES.get(provider)
    return None if rules is None else rules.canonical(url)


def resource_matches(provider: str, resource: str, body: str) -> bool:
    """Whether this page body is about the resource — its own page, or a record the provider linked
    to it. An alias ends where the resource's identifier ends, so `pull/5` is not `pull/50`."""
    rules = RESOURCE_RULES.get(provider)
    if rules is None:
        return False
    return any(
        re.search(re.escape(alias) + _ALIAS_END, body, re.IGNORECASE) is not None
        for alias in rules.aliases(resource)
    )


def resource_digest(resource: str) -> str:
    """A resource URL as one segment of an object name or a path, since the URL itself spells
    characters neither may carry."""
    return sha256(resource.encode()).hexdigest()[:RESOURCE_DIGEST_HEX]
