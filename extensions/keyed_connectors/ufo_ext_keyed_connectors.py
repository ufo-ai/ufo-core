"""Keyed connectors: providers that authenticate with a workspace API key on the wire.

A broker (Composio, Pipedream) can only front a provider whose consent it hosts. The larger class —
Datadog, PostHog, Tavily, New Relic, SendGrid and hundreds more — authenticates with a key the
member holds, which no broker mints. Those reach the agent here instead: each provider in
`KEYED_PROVIDERS` becomes credential slots, the owner fills them through the standard
`request_credentials` private handoff, and the egress proxy swaps each stored secret onto the wire
for the sentinel the sandbox exports. The agent calls the provider's own REST API from the sandbox
and never holds the key — the same trade the GitHub CLI already makes, minus the broker.

A provider is one row in the table: its API host, the headers its keys ride in, and the env vars the
sandbox sees. A provider that pins its host per account (a Datadog site, an OpsGenie region)
declares `sites` instead of `host` — the closed set of hosts it publishes — and the member selects
one, which adds a companion slot for the choice. The proxy admits and injects on exactly the host
selected, so a US5 org's key never rides to US1, and because a selection resolves to one of the
declared literals there is no member-written hostname anywhere on the wire.

Out of scope by construction, because a swap of one header for one secret cannot express them: auth
signed over the request (AWS SigV4), and Basic auth composed from two stored values (Twilio,
Mixpanel), which needs a composer no declaration here can name. A provider whose host is an open set
rather than a published one (a self-hosted PostHog, a Supabase project ref) needs a declaration form
that builds the host from a member-supplied label under a fixed domain; it lands with its first
provider."""

from dataclasses import dataclass

from ufo.sdk.manifest import (
    CredentialSlot,
    HostChoice,
    InjectionTarget,
    Manifest,
    PromptSection,
)

NAME = "keyed_connectors"
VERSION = "0.1.0"
SECTION_NAME = "keyed_connectors"
REQUEST_DIMENSION = "requests"
SENTINEL_PREFIX = "UFO_SENTINEL_KEYED_"
HOST_SLOT_SUFFIX = "api_host"
SWAPPABLE_SCHEMES = frozenset({"API-Key", "Bearer", "Token"})


@dataclass(frozen=True)
class KeyedSecret:
    """One key a provider takes on the wire: the slot suffix naming it, the header it rides in —
    with the auth scheme prefixing its value, where the API takes one — the sandbox env var the
    agent sends it from, and what the member is asked for when filling it."""

    key: str
    header: str
    env: str
    description: str
    scheme: str = ""

    def __post_init__(self) -> None:
        if self.scheme and self.scheme not in SWAPPABLE_SCHEMES:
            raise ValueError(
                f"keyed secret {self.key!r} declares scheme {self.scheme!r}; the egress proxy "
                f"swaps only {sorted(SWAPPABLE_SCHEMES)}-prefixed values"
            )


@dataclass(frozen=True)
class KeyedProvider:
    """One keyed provider as the table holds it: the provider slug, the keys its API takes, and its
    API host — a single hostname, or `sites` for a provider that pins the host per account, in which
    case the member selects one of them and `host_env` carries the choice into the sandbox. `slots`
    is the whole declaration a row implies, so adding a provider is a row, never new code."""

    provider: str
    label: str
    secrets: tuple[KeyedSecret, ...]
    host: str = ""
    sites: tuple[str, ...] = ()
    host_env: str = ""
    site_description: str = ""

    def __post_init__(self) -> None:
        if bool(self.host) == bool(self.sites):
            raise ValueError(
                f"keyed provider {self.provider!r} declares exactly one of host or sites"
            )
        if self.sites and not (self.host_env and self.site_description):
            raise ValueError(
                f"keyed provider {self.provider!r} offers sites, so it needs host_env and "
                "site_description for the member to choose one and the agent to address it"
            )

    @property
    def target_host(self) -> str | HostChoice:
        if not self.sites:
            return self.host
        return HostChoice(
            slot=f"{self.provider}_{HOST_SLOT_SUFFIX}",
            description=self.site_description,
            hosts=self.sites,
            default=self.sites[0],
            env=self.host_env,
        )

    def slots(self) -> tuple[CredentialSlot, ...]:
        host = self.target_host
        keys = tuple(
            CredentialSlot(
                name=f"{self.provider}_{secret.key}",
                description=secret.description,
                injection=InjectionTarget(
                    host=host,
                    header=secret.header,
                    sentinel=f"{SENTINEL_PREFIX}{self.provider}_{secret.key}".upper(),
                    env=secret.env,
                    dimension=REQUEST_DIMENSION,
                ),
            )
            for secret in self.secrets
        )
        if isinstance(host, str):
            return keys
        return (*keys, CredentialSlot(name=host.slot, description=host.description))

    def usage(self) -> str:
        headers = " ".join(
            f'-H "{secret.header}: {secret.scheme + " " if secret.scheme else ""}${secret.env}"'
            for secret in self.secrets
        )
        host = self.target_host
        base = self.host if isinstance(host, str) else f"${self.host_env}"
        named = [f"{self.provider}_{secret.key}" for secret in self.secrets]
        if isinstance(host, HostChoice):
            named.append(host.slot)
        return (
            f"- {self.provider} ({self.label}): slots {', '.join(named)}. "
            f'Call it as `curl -sS "https://{base}/<path>" {headers}`.'
        )


KEYED_PROVIDERS: tuple[KeyedProvider, ...] = (
    KeyedProvider(
        provider="datadog",
        label="Datadog",
        host_env="DD_HOST",
        secrets=(
            KeyedSecret(
                key="api_key",
                header="DD-API-KEY",
                env="DD_API_KEY",
                description="Datadog API key (Organization Settings → API Keys).",
            ),
            KeyedSecret(
                key="application_key",
                header="DD-APPLICATION-KEY",
                env="DD_APP_KEY",
                description="Datadog application key (Organization Settings → Application Keys); "
                "read endpoints need it alongside the API key.",
            ),
        ),
        sites=(
            "api.datadoghq.com",
            "api.us3.datadoghq.com",
            "api.us5.datadoghq.com",
            "api.datadoghq.eu",
            "api.ap1.datadoghq.com",
            "api.ap2.datadoghq.com",
            "api.uk1.datadoghq.com",
            "api.ddog-gov.com",
            "api.us2.ddog-gov.com",
        ),
        site_description="Datadog API host for this org's site — one of `api.datadoghq.com` (US1), "
        "`api.us3.datadoghq.com`, `api.us5.datadoghq.com`, `api.datadoghq.eu` (EU1), "
        "`api.ap1.datadoghq.com`, `api.ap2.datadoghq.com`, `api.uk1.datadoghq.com`, "
        "`api.ddog-gov.com` (US1-FED) or `api.us2.ddog-gov.com`. Read it off your Datadog URL; "
        "US1 orgs may leave it unset.",
    ),
    KeyedProvider(
        provider="posthog",
        label="PostHog",
        host_env="POSTHOG_HOST",
        secrets=(
            KeyedSecret(
                key="api_key",
                header="Authorization",
                scheme="Bearer",
                env="POSTHOG_API_KEY",
                description="PostHog personal API key (account settings → Personal API keys), "
                "scoped to what the workspace needs.",
            ),
        ),
        sites=("us.posthog.com", "eu.posthog.com"),
        site_description="PostHog private API host — `us.posthog.com` (US Cloud) or "
        "`eu.posthog.com` (EU Cloud). Read it off your PostHog URL; US Cloud orgs may leave it "
        "unset.",
    ),
    KeyedProvider(
        provider="mercury",
        label="Mercury",
        host="api.mercury.com",
        secrets=(
            KeyedSecret(
                key="api_key",
                header="Authorization",
                scheme="Bearer",
                env="MERCURY_API_KEY",
                description="Mercury API token (Settings → API tokens). A read-only token reads "
                "accounts and transactions; payment initiation needs a read-write token, which "
                "Mercury issues to approved partners only.",
            ),
        ),
    ),
    KeyedProvider(
        provider="apollo",
        label="Apollo",
        host="api.apollo.io",
        secrets=(
            KeyedSecret(
                key="api_key",
                header="X-Api-Key",
                env="APOLLO_API_KEY",
                description="Apollo API key (Settings → Integrations → API). People and "
                "organization search work with any key; sequences, tasks and deals answer 403 "
                "unless the key is a master key.",
            ),
        ),
    ),
    KeyedProvider(
        provider="pandadoc",
        label="PandaDoc",
        host="api.pandadoc.com",
        secrets=(
            KeyedSecret(
                key="api_key",
                header="Authorization",
                scheme="API-Key",
                env="PANDADOC_API_KEY",
                description="PandaDoc API key (Settings → Integrations → API and Webhooks). It "
                "reaches documents, templates, contacts and folders — the same surface as the "
                "OAuth app, without the consent leg.",
            ),
        ),
    ),
)

SECTION_BODY = "\n".join(
    (
        "## Keyed providers",
        "",
        "These providers authenticate with a workspace API key rather than a connected account, so "
        "`list_external_tools` does not list them and `connect_account` cannot reach them. Their "
        "state is visible as credential slots: list the `credential` object kind to see which are "
        "filled.",
        "",
        "To connect one, call `request_credentials` for its slots (a workspace admin fills them "
        "privately — never ask for a key in chat prose), then call the provider's own REST API "
        "from the sandbox. Each slot exports an env var holding a sentinel, not the secret: the "
        "egress proxy swaps in the real key on the wire, so the sandbox never holds it and the "
        "raw value "
        "cannot be read, echoed, or written to a file. An env var that is absent means the slot is "
        "empty — read the `credential` object kind, which reports each slot filled or empty and "
        "the host a keyed slot resolves to here, then ask the member for what is missing rather "
        "than guessing a key or a host.",
        "",
        *(provider.usage() for provider in KEYED_PROVIDERS),
    )
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=tuple(slot for provider in KEYED_PROVIDERS for slot in provider.slots()),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
