"""Deploy configuration: one ufo.toml, fail loud."""

import os
import re
import tomllib
from pathlib import Path
from string import Formatter
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.harness.models.interface import AUTO_MODEL
from ufo.sdk.http import plain_local

CONFIG_PATH_ENV = "UFO_CONFIG"
DEFAULT_CONFIG_PATH = Path("ufo.toml")
IN_PROCESS_BACKEND = "in_process"
DEFAULT_CDP_PROVIDER = "sandbox_chrome"
DEFAULT_AUTO_MODEL = "claude-opus-5-5"
DEFAULT_AMBIENT_REPLY_MODEL = "gpt-5.6-luna"
DEFAULT_BACKGROUND_JOBS_MODEL = "gpt-5.6-luna"
DEFAULT_INGRESS_PORT = 8100
DEFAULT_FLAG_CACHE_TTL_SECONDS = 30.0
DEFAULT_CONTEXT_STRATEGY = "compact"
"""The context boundary a deploy runs when its toml names none: spend one model call over the
window and install the summary. A name, not an import — the strategy behind it ships as the
`context_compact` extension, and this default resolves through the manifests like any
other."""
DEFAULT_FLAGGED_CONTEXT_STRATEGY = "rollover"
"""The context boundary a workspace runs where `ufo.runtime.context_boundary.CONTEXT_ROLLOVER_FLAG`
reads on: reset the window at the line and keep the outgoing one in the sandbox history file. A name
like the one above, so the flag selects between two names a toml holds rather than between two
imports."""
DEFAULT_OPERATOR_RULE = "seated_admin"
TURN_URL_FIELDS = frozenset({"trace_id", "start_ms", "end_ms", "conversation_id"})
CONVERSATION_URL_FIELDS = frozenset({"installation", "address"})
PLACEHOLDER_PART = re.compile(r"\[\d+\]")


class DatabaseConfig(BaseModel):
    """`system_url` is the DBOS system store's sync-driver url. Unset, it derives as a `_dbos`
    sibling of the application database. A dedicated server and a shared service each use one DBOS
    store paired with that application database; set it explicitly only when DBOS lives elsewhere.

    `owner_url` is the RLS-bypassing owner-role DSN the shared fleet's jobs and `ufoctl proxy` use
    for cross-workspace enumeration. Reads bind or filter the workspace before accessing its data.
    Serve fails loud when neither this field nor its `UFO_OWNER_DSN` override is set."""

    model_config = ConfigDict(extra="forbid")
    url: str
    system_url: str = ""
    owner_url: str | None = None

    @model_validator(mode="after")
    def _derive_system_url(self) -> "DatabaseConfig":
        if self.system_url:
            return self
        base, _, name = self.url.rpartition("/")
        if self.url.startswith("sqlite"):
            stem, dot, suffix = name.partition(".")
            self.system_url = f"{base}/{stem}_dbos{dot}{suffix}".replace(
                "sqlite+aiosqlite", "sqlite", 1
            )
        else:
            self.system_url = f"{base}/{name}_dbos".replace(
                "postgresql+asyncpg", "postgresql+psycopg", 1
            )
        return self


class BlobConfig(BaseModel):
    """The blob store — transcripts, rollover records, shared artifacts. Never a conversation's
    workspace: that lives in the sandbox, which holds no credential for this store.
    `endpoint_url`/`region` are the S3 the serve process talks to; leaving `endpoint_url` unset
    selects AWS and its virtual-hosted addressing, setting it selects an S3-compatible endpoint and
    path addressing — the same choice a presigned artifact PUT is signed under."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["filesystem", "s3"]
    root: Path | None = None
    bucket: str | None = None
    endpoint_url: str | None = None
    region: str | None = None

    @model_validator(mode="after")
    def _backend_complete(self) -> "BlobConfig":
        if self.backend == "filesystem" and self.root is None:
            raise ValueError("blob.root is required for the filesystem backend")
        if self.backend == "s3" and self.bucket is None:
            raise ValueError("blob.bucket is required for the s3 backend")
        return self


class ModelsConfig(BaseModel):
    """`auto_model` is the concrete model an agent authored with `model = "auto"` resolves to at
    turn time, so an agent stays model-agnostic and the deploy pins the backend.
    `ambient_reply_model` is the cheap model the pre-admission ambient-reply decision runs on, one
    call per un-addressed thread reply ahead of any turn — pinned separately because it is priced
    against the turn it prevents, not against the answer an agent gives.
    `background_jobs_model` is the model a background job's own metered call runs on — memory fact
    extraction, memory consolidation, chat titles — pinned separately for the same reason: a job is
    one bounded schema-bound one-shot over a payload nothing re-reads, so it reads no cache and pays
    full price on every token. A job whose payload can outgrow this model's context window declares
    `JobSpec.needs_deploy_model` and keeps `auto_model` instead."""

    model_config = ConfigDict(extra="forbid")
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    openai_api_key_env: str = "OPENAI_API_KEY"
    auto_model: str = DEFAULT_AUTO_MODEL
    ambient_reply_model: str = DEFAULT_AMBIENT_REPLY_MODEL
    background_jobs_model: str = DEFAULT_BACKGROUND_JOBS_MODEL

    @model_validator(mode="after")
    def _models_concrete(self) -> "ModelsConfig":
        if not self.auto_model or self.auto_model == AUTO_MODEL:
            raise ValueError("models.auto_model must be a concrete model id, not empty or 'auto'")
        if not self.ambient_reply_model or self.ambient_reply_model == AUTO_MODEL:
            raise ValueError(
                "models.ambient_reply_model must be a concrete model id, not empty or 'auto'"
            )
        if not self.background_jobs_model or self.background_jobs_model == AUTO_MODEL:
            raise ValueError(
                "models.background_jobs_model must be a concrete model id, not empty or 'auto'"
            )
        return self


class CredentialsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_env: str = "UFO_CREDENTIAL_KEY"


class ServeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 8710
    request_shutdown_seconds: int = Field(default=30, ge=0)
    graceful_shutdown_seconds: int = Field(default=0, ge=0)
    disabled_jobs: tuple[str, ...] = ()
    sign_in_path: str | None = None
    """The path on this deploy's host where a browser signs in, answered by a gateway in front of
    it. An expired artifact link sends a browser there with the link as its `a` target. Unset, the
    deploy signs no browser in: that link answers 403, and only a public site opens in a
    browser."""
    gateway_prefixes: tuple[str, ...] = ()
    """The paths a gateway in front of this deploy answers on the same host. The ingress would
    shadow a route of this deploy's under one, so serve refuses to boot while one lies there."""

    @model_validator(mode="after")
    def _gateway_paths_are_paths(self) -> "ServeConfig":
        sign_in = () if self.sign_in_path is None else (self.sign_in_path,)
        for path in (*sign_in, *self.gateway_prefixes):
            split = urlsplit(path)
            if not path.startswith("/") or split.netloc or split.query or split.fragment:
                raise ValueError(
                    f"serve.sign_in_path and serve.gateway_prefixes are paths on this deploy's "
                    f"host (e.g. /login), not {path!r}"
                )
        return self


class ConnectConfig(BaseModel):
    """OAuth connect settings. `public_base_url` is the base (scheme and host, e.g.
    `https://ufo.example.com` hosted, `http://localhost:8710` for a local node) that a provider
    redirects the member's browser back to; `ConnectFlow.redirect_uri` derives from it. Absent when
    no connector provider is installed (connect is inert); required, and never a wildcard bind,
    once one is."""

    model_config = ConfigDict(extra="forbid")
    public_base_url: str | None = None


class MemoryConfig(BaseModel):
    """Memory retrieval settings. `index_backend` names a vector-index backend an extension
    contributes through its `indexes` Manifest point; `embed_backend` names an
    embedding backend an extension contributes through its `embeds` point. Either unset resolves the
    base-pinned `"default"` backend — the `index_default` extension (SQLite FTS5 + local cosine,
    Postgres tsvector + pgvector) and the `embed_openai` extension."""

    model_config = ConfigDict(extra="forbid")
    index_backend: str | None = None
    embed_backend: str | None = None


class SkillsConfig(BaseModel):
    """`member_block` renders the bound agent's saved-skill cards into each member turn's injected
    context. Off, member skills stay loadable through the skill kind's `skill_search` action and
    `load_skill` but no turn suggests them — the ablation arm the suggestion-bias evals run
    against."""

    model_config = ConfigDict(extra="forbid")
    member_block: bool = True


class O11yConfig(BaseModel):
    """`otlp_endpoint` is the collector every metric, log, and span exports to.

    `datadog_check_url` is Datadog's `check_run` intake, which a service check is submitted to
    directly — OTLP defines no service check, so nothing the collector carries can hold one.
    `datadog_env` is the `env` tag those submissions carry, which is the scope a monitor selects
    them by; `datadog_api_key_env` names the env var holding the key the intake authenticates. Unset
    `datadog_check_url` reports no service check at all, which is every deploy no monitor watches —
    a developer's node, the eval stack."""

    model_config = ConfigDict(extra="forbid")
    otlp_endpoint: str | None = None
    datadog_check_url: str | None = None
    datadog_env: str | None = None
    datadog_api_key_env: str = "DD_API_KEY"


class SandboxConfig(BaseModel):
    """Which carrier runs the per-conversation sandbox. `backend` names a registered carrier — the
    zero-dependency `local` carrier ships with core, an extension registers more (`docker`, `e2b`, a
    remote runner) through its `carriers` Manifest point — and `serve` fails loud on a name no
    carrier registers. Each carrier sources its own parameters (template, keys); core config knows
    only the selected name.

    `workspace_root` holds one directory per conversation, which an in-cluster carrier serves
    `/workspace` from — the Docker carrier's bind-mount source, the local carrier's cwd. An
    off-cluster carrier (E2B) cannot see the host filesystem and serves `/workspace` from its own
    sandbox disk, so the setting does not reach it.

    `image_ref` is the exact sandbox image an image-backed carrier starts. A content-addressed
    reference lets an eval or deploy bind the runtime tools it validated instead of resolving a
    mutable local tag after validation.

    `proxy_url` names the proxy service, `https` and a host with nothing after it: a carrier whose
    sandbox runs off the cluster, such as E2B, egresses only through it, and `serve` fails loud
    when one is selected without it — open, unmetered egress is never a silent default. The
    in-cluster carriers run unenforced and open no proxy session.

    `ingress_port` is the stable port the sandbox ingress binds. `ingress_public_url` is the
    wildcard base every served sandbox port is a subdomain of (`https://example.com`, backed
    by a wildcard DNS record and cert): the ingress resolves each request's site from the label
    under it, and `SurfaceContext.ingress_url` mints links against it. `https` and a host, with an
    optional port and nothing more — a site's session cookie is `Secure`. Unset, a surface mints no
    link and `ufoctl ingress` refuses to boot — a site would have no address to be served at."""

    model_config = ConfigDict(extra="forbid")
    backend: str = "local"
    image_ref: str = Field(default="ufo-sandbox:latest", min_length=1)
    resume_backends: tuple[str, ...] = ()
    """Backends kept live only for the stored handles bearing their scheme — a conversation whose
    sandbox another provider still holds keeps resuming there, while new sandboxes always open on
    `backend`. Every name must resolve to a registered carrier, and repeating `backend` here fails
    loud."""
    workspace_root: Path = Path("./workspaces")
    proxy_url: str | None = None
    ingress_port: int = DEFAULT_INGRESS_PORT
    ingress_public_url: str | None = None
    apps_dev_server: str | None = None
    """`http://host:port` of a dev server holding the app pages from source. Set, the ingress relays
    a shipped app page there — its root at `/<slug>/`, every other path verbatim — in place of the
    published bundle: the local stack's edit loop. Unset, a shipped page is the bundle."""
    preview_service: str | None = None
    """`host:port` of the preview service the proxy relays the preview host to. Set
    admits that host for every sandbox, whatever its internet policy. Unset, the host is not
    admitted, document reads are unavailable, and a share carries no rendered preview."""

    @model_validator(mode="after")
    def _ingress_base_is_addressable(self) -> "SandboxConfig":
        """Browsers resolve `*.localhost` to loopback and store no `Secure` cookie from plain http;
        under bare `localhost` each label is its own site, so the `Lax` cookie is third-party."""
        if self.ingress_public_url is None:
            return self
        base = urlsplit(self.ingress_public_url)
        if not base.hostname or (
            base.scheme != "https" and not plain_local(self.ingress_public_url)
        ):
            raise ValueError(
                "sandbox.ingress_public_url must be an https base with a host "
                "(e.g. https://example.com) — a site carries a member's session, and plain http "
                "hands it to the network. http is allowed for the one host that never leaves "
                "the machine: localhost"
            )
        if base.path or base.query or base.fragment or base.username or base.password:
            raise ValueError(
                "sandbox.ingress_public_url is a scheme and a host only, with no path, query, "
                "fragment, or credentials (e.g. https://example.com) — every site's address "
                "is a label put in front of that host"
            )
        return self

    @model_validator(mode="after")
    def _proxy_url_is_an_origin(self) -> "SandboxConfig":
        if self.proxy_url is None:
            return self
        base = urlsplit(self.proxy_url)
        if (
            base.scheme != "https"
            or not base.hostname
            or base.path
            or base.query
            or base.fragment
            or base.username
            or base.password
        ):
            raise ValueError("sandbox.proxy_url is the proxy service's https URL with no path")
        return self

    @model_validator(mode="after")
    def _apps_dev_server_is_an_origin(self) -> "SandboxConfig":
        if self.apps_dev_server is None:
            return self
        base = urlsplit(self.apps_dev_server)
        if (
            base.scheme not in {"http", "https"}
            or not base.hostname
            or base.path
            or base.query
            or base.fragment
            or base.username
            or base.password
        ):
            raise ValueError(
                "sandbox.apps_dev_server is an http(s) scheme and a host only (e.g. "
                "http://web:5174) — the ingress relays a shipped page's own path onto it"
            )
        return self


class ExtConfig(BaseModel):
    """The extension store toggle. `store` names a catalog file the `ufoctl ext` commands search
    and install from; omit it and the store is off — the deploy runs only what its bundle pinned."""

    model_config = ConfigDict(extra="forbid")
    store: Path | None = None


class SourceConfig(BaseModel):
    """The folder backend's parameters — the local directory it syncs. Crosses two boundaries: the
    config file and the `source.config` JSON column, so it validates at construction both times."""

    model_config = ConfigDict(extra="forbid")
    root: str


class SourceEntry(BaseModel):
    """One content source: which backend, with its config. `register_sources` lands each as a
    source row of a connection of its own, keyed by `feed_handle` — the authority a feed nobody
    owns runs under, one per feed so removing one root never takes another's pages — which the sync
    driver then polls. `backend` names core's `folder` or a backend an
    extension registers through its Manifest `sources` point; serve refuses an unknown name at boot.
    A configured source is operator authority: nobody owns that connection, so it is shared and its
    pages sync workspace-shared, and a local directory root enters only here, never from a chat
    act."""

    model_config = ConfigDict(extra="forbid")
    backend: str = Field(min_length=1)
    config: SourceConfig


class ArtifactsConfig(BaseModel):
    """The HMAC secret that signs artifact delivery tokens. `share_file` mints with it and core's
    artifact route verifies with it, so it belongs to neither consumer: `token_secret_env` names the
    env var holding the secret."""

    model_config = ConfigDict(extra="forbid")
    token_secret_env: str = "UFO_ARTIFACT_TOKEN_SECRET"


class HubConfig(BaseModel):
    """The live-frame hub backend. `backend` selects among the in-process default and any backend
    an extension registers through its Manifest `hubs` point; `url` is the selected backend's
    connection string (e.g. `redis://…`), carried in config like `database.url`. A cross-process
    backend fans frames out across instances, which is what lifts the single-instance boot guard."""

    model_config = ConfigDict(extra="forbid")
    backend: str = IN_PROCESS_BACKEND
    url: str | None = None


class TerminalConfig(BaseModel):
    """The terminal-rendezvous transport. `backend` selects among the in-process default and any
    backend an extension registers through its Manifest `terminal_transports` point (the `redis_hub`
    extension registers `redis`). The transport reuses `hub.url` for its connection string, so a
    fleet already running the Redis hub selects the Redis terminal transport beside it with no new
    URL. The in-process transport is correct only on a single serve instance; a shared fleet selects
    a cross-pod backend so a member's held connection and their turn's workflow reach one terminal
    even when they land on different pods."""

    model_config = ConfigDict(extra="forbid")
    backend: str = IN_PROCESS_BACKEND


class BrowserConfig(BaseModel):
    """The browser transport. `cdp_provider` selects where the one BUA engine (the browser
    extension) connects — every provider is contributed by an extension at the `cdp_providers`
    Manifest point; core ships none. The default `sandbox_chrome` leases CDP from the turn's own
    sandbox (zero-config, no key); `browserbase` mints a hosted session per browser run, which is
    what hosted deploys select. Selecting a name no active extension registers fails loud at boot
    only when a browser extension needs it."""

    model_config = ConfigDict(extra="forbid")
    cdp_provider: str = DEFAULT_CDP_PROVIDER


class ConnectorsConfig(BaseModel):
    """Feed-sync connector settings. A brokered provider resolves its credential through its own
    broker (the extension that registers the connector), never a config knob; `auth_backend` names
    the auth-proxy backend for every other provider — the `direct` BYOK backend (a member-added key
    read host-side) or any backend an extension registers through its Manifest `auth_proxies`
    point. The sole installed backend is automatic; with several installed, this setting is
    required. Selecting a name no extension registers fails loud at boot."""

    model_config = ConfigDict(extra="forbid")
    auth_backend: str | None = None


class ResearchConfig(BaseModel):
    """Web research settings. `search_provider` names a search backend an extension registers
    through its Manifest `search_providers` point (e.g. `perplexity`); the research extension's
    tools call the selected provider host-side. Unset selects no backend — a deploy with a research
    extension active (which `requires` the seam) fails loud at boot until it is set."""

    model_config = ConfigDict(extra="forbid")
    search_provider: str | None = None


class ContextConfig(BaseModel):
    """The context boundary — what a turn does when its window crosses its line. `strategy` names
    one strategy registered at the Manifest `context_boundaries` point. Core registers none: the
    `context_rollover` extension registers `rollover` (reset the window to a recovery record no
    model authored and keep the outgoing window in the sandbox history file) and
    `context_compact` registers `compact` (spend one model call over the head and
    install the verified summary in front of a verbatim tail). Both ship in the wheel, so the
    default below resolves on a stock deploy. Exactly one runs over one window: the name selects the
    boundary the turn crosses, and a name no active extension registers fails loud rather than
    falling back.

    `flagged_strategy` is the other name — the one a workspace runs where
    `ufo.runtime.context_boundary.CONTEXT_ROLLOVER_FLAG` reads on. That flag reads closed, so
    `strategy` is what every workspace crosses until somebody turns the flag on for one, which is
    how one build runs compaction in production and rollover in testing."""

    model_config = ConfigDict(extra="forbid")
    strategy: str = DEFAULT_CONTEXT_STRATEGY
    flagged_strategy: str = DEFAULT_FLAGGED_CONTEXT_STRATEGY


class FlagsConfig(BaseModel):
    """Feature flags. `backend` names a flag provider an extension registers through its Manifest
    `flag_providers` point (the `flags_open` extension registers `open`); unset selects
    none, and every flag resolves to the default its call site passes — the closed state, which is
    what a deploy carrying no flag service runs on. `cache_ttl_seconds` is how long the selected
    backend may answer a flag out of its own response cache before evaluating it again, so a turn
    reading a flag pays one round trip per window rather than one per read; 0 evaluates every read.
    A backend that cannot answer inside `ufo.flags.FLAG_TIMEOUT_SECONDS` holds no turn open — that
    read falls back to the same default."""

    model_config = ConfigDict(extra="forbid")
    backend: str | None = None
    cache_ttl_seconds: float = Field(default=DEFAULT_FLAG_CACHE_TTL_SECONDS, ge=0)


class PackConfig(BaseModel):
    """The active pack. `name` selects one pack a workspace member under `packs/<name>/` registers
    through the `ufo.pack` entry point; activating it narrows the deploy to exactly the
    extensions that pack bundles plus its own pack-level skills and onboarding steps. Unset runs the
    unnarrowed extension set — the lockfile's pins, or every discovered extension in dev — and boot
    refuses a set in which two extensions publish one tool, as `browser` and its alternative
    `browser_use` do."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None


class SitesConfig(BaseModel):
    """`page_kit` names the app-page kit archive: a gzipped tar holding, at its root, the modules an
    app page builds against (`kit.js`, `blocks.js` and what they load), which the sites extension
    unpacks beside each page project it builds. Serve refuses to boot when it names no file. Unset,
    the deploy builds no app page, and a deploy of a directory holding `app.tsx` is refused naming
    this setting."""

    model_config = ConfigDict(extra="forbid")
    page_kit: Path | None = None
    sign_in_url: str | None = None
    """The path on this deploy's host a non-public site's page links a browser not signed in to the
    site's workspace. Unset, it is `[serve] sign_in_path`; a gateway whose sign-in forwards a
    browser that already holds a session names its sign-out path instead, which drops a session
    held for another workspace first."""
    share_card_url: str | None = None
    """The absolute http(s) URL of the 1200x630 JPEG a site with no card of its own unfurls as.
    Unset, it is the generic card the sites extension serves off `[connect] public_base_url`."""

    @model_validator(mode="after")
    def _links_are_links(self) -> "SitesConfig":
        if self.sign_in_url is not None:
            split = urlsplit(self.sign_in_url)
            if (
                not self.sign_in_url.startswith("/")
                or split.netloc
                or split.query
                or split.fragment
            ):
                raise ValueError(
                    f"sites.sign_in_url is a path on this deploy's host (e.g. /logout), not "
                    f"{self.sign_in_url!r}"
                )
        if self.share_card_url is not None:
            split = urlsplit(self.share_card_url)
            if split.scheme not in {"http", "https"} or not split.netloc:
                raise ValueError(
                    f"sites.share_card_url is an absolute http(s) URL, not {self.share_card_url!r}"
                )
        return self


class OperatorConfig(BaseModel):
    """Who an operator surface (the debugger, the memory explorer) admits. `rule` names one rule:
    the built-in `seated_admin`, which admits a seated admin of the bearer's own workspace to that
    workspace alone, or a rule a first-party extension registers at the Manifest `operator_rules`
    point, which may grant reach across the fleet. An unknown name fails boot."""

    model_config = ConfigDict(extra="forbid")
    rule: str = DEFAULT_OPERATOR_RULE


class DebuggerConfig(BaseModel):
    """Where the session debugger links out. `turn_urls` maps a label to a page an opened turn
    links to (its trace, its logs), filled from `{trace_id}`, `{conversation_id}`, and the turn's
    window from `{start_ms}` to `{end_ms}` in epoch milliseconds. `conversation_urls` maps a surface
    name to the pages that surface shows a conversation on, filled from `{installation}` (the
    workspace's installation on that surface) and `{address}` (the conversation's key there); the
    first template whose every value fills is the link. A placeholder may name a value's
    `:`-separated part, `{address[1]}`, and a template naming a value or part that is absent renders
    no link. Unset, the debugger renders no link."""

    model_config = ConfigDict(extra="forbid")
    turn_urls: dict[str, str] = Field(default_factory=dict)
    conversation_urls: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_placeholders(self) -> "DebuggerConfig":
        for label, template in self.turn_urls.items():
            _require_placeholders(f"debugger.turn_urls.{label}", template, TURN_URL_FIELDS)
        for surface, templates in self.conversation_urls.items():
            for template in templates:
                _require_placeholders(
                    f"debugger.conversation_urls.{surface}", template, CONVERSATION_URL_FIELDS
                )
        return self


def _require_placeholders(key: str, template: str, allowed: frozenset[str]) -> None:
    for _, field, spec, conversion in Formatter().parse(template):
        if field is None:
            continue
        name, bracket, part = field.partition("[")
        if (
            name not in allowed
            or spec
            or conversion
            or (bracket and not PLACEHOLDER_PART.fullmatch(bracket + part))
        ):
            raise ValueError(
                f"{key} names {{{field}}}; its placeholders are "
                + ", ".join(f"{{{placeholder}}}" for placeholder in sorted(allowed))
                + ", each optionally indexed by part, as in {name[0]}"
            )


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: DatabaseConfig
    blob: BlobConfig
    models: ModelsConfig = ModelsConfig()
    credentials: CredentialsConfig = CredentialsConfig()
    serve: ServeConfig = ServeConfig()
    connect: ConnectConfig = ConnectConfig()
    memory: MemoryConfig = MemoryConfig()
    skills: SkillsConfig = SkillsConfig()
    o11y: O11yConfig = O11yConfig()
    sandbox: SandboxConfig = SandboxConfig()
    ext: ExtConfig = ExtConfig()
    artifacts: ArtifactsConfig = ArtifactsConfig()
    hub: HubConfig = HubConfig()
    terminal: TerminalConfig = TerminalConfig()
    browser: BrowserConfig = BrowserConfig()
    connectors: ConnectorsConfig = ConnectorsConfig()
    research: ResearchConfig = ResearchConfig()
    context: ContextConfig = ContextConfig()
    flags: FlagsConfig = FlagsConfig()
    pack: PackConfig = PackConfig()
    sources: tuple[SourceEntry, ...] = ()
    sites: SitesConfig = SitesConfig()
    operator: OperatorConfig = OperatorConfig()
    debugger: DebuggerConfig = DebuggerConfig()

    @model_validator(mode="after")
    def _site_sign_in_needs_a_sign_in(self) -> "Config":
        if self.sites.sign_in_url is not None and self.serve.sign_in_path is None:
            raise ValueError(
                "sites.sign_in_url is set but serve.sign_in_path is not: a deploy that signs no "
                "browser in has no sign-in to link"
            )
        return self


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> Config:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create ufo.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
