"""Deploy configuration: one ufo.toml, fail loud."""

import os
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.models.interface import AUTO_MODEL

CONFIG_PATH_ENV = "UFO_CONFIG"
DEFAULT_CONFIG_PATH = Path("ufo.toml")
IN_PROCESS_BACKEND = "in_process"
DEFAULT_CDP_PROVIDER = "sandbox_chrome"
DEFAULT_AUTO_MODEL = "claude-opus-5"
DEFAULT_AMBIENT_REPLY_MODEL = "gpt-5.6-luna"
DEFAULT_BACKGROUND_JOBS_MODEL = "gpt-5.6-luna"
DEFAULT_PROXY_PORT = 8888
DEFAULT_INGRESS_PORT = 8100


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
    """The blob store — transcripts, compaction records, shared artifacts. Never a conversation's
    workspace: that lives in the sandbox, which holds no credential for this store.
    `endpoint_url`/`region` are the S3 the serve process talks to; leaving `endpoint_url` unset
    selects AWS and its virtual-hosted addressing, setting it selects an S3-compatible endpoint and
    path addressing — the same choice a presigned artifact PUT is signed under.

    `root` may be a symlink to the directory the objects live on; it is canonicalized once per
    operation and every key is contained under the result, so it is refused only when it names
    something that is not a directory."""

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
    contributes through its `indexes` Manifest point (e.g. turbopuffer); `embed_backend` names an
    embedding backend an extension contributes through its `embeds` point. Either unset resolves the
    base-pinned `"default"` backend — the `index_default` extension (SQLite FTS5 + local cosine,
    Postgres tsvector + pgvector) and the `embed_openai` extension."""

    model_config = ConfigDict(extra="forbid")
    index_backend: str | None = None
    embed_backend: str | None = None


class SkillsConfig(BaseModel):
    """`member_block` renders the bound agent's saved-skill cards into each member turn's injected
    context. Off, member skills stay loadable through `skill_search` and `load_skill` but no turn
    suggests them — the ablation arm the suggestion-bias evals run against."""

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
    sandbox disk, so the setting does not reach it. It may be a symlink to the volume the workspaces
    live on; it is canonicalized once and the per-conversation directory under it is then opened
    component by component without following a link, so a link planted inside the root is still
    refused.

    `proxy_port` is the stable port the egress proxy binds. `proxy_public_url` is the externally
    reachable base a remote sandbox carrier such as E2B dials; local carriers leave it unset and
    reach the process-local proxy directly. `serve` fails loud when a remote carrier has no public
    proxy URL — open, unmetered egress is never a silent default.

    `ingress_port` is the stable port the sandbox ingress binds. `ingress_public_url` is the
    wildcard base every served sandbox port is a subdomain of (`https://example.com`, backed
    by a wildcard DNS record and cert): the ingress resolves each request's site from the label
    under it, and `SurfaceContext.ingress_url` mints links against it. `https` and a host, with an
    optional port and nothing more — a site's session cookie is `Secure`. Unset, a surface mints no
    link and `ufoctl ingress` refuses to boot — a site would have no address to be served at."""

    model_config = ConfigDict(extra="forbid")
    backend: str = "local"
    workspace_root: Path = Path("./workspaces")
    proxy_port: int = DEFAULT_PROXY_PORT
    proxy_public_url: str | None = None
    ingress_port: int = DEFAULT_INGRESS_PORT
    ingress_public_url: str | None = None
    cache_daemon: str | None = None
    """`host:port` of the sandbox cache daemon co-located on the proxy pod (RFC 0032). Set enables
    the cache: the proxy relays the cache host to it and internet-holding sandboxes route git and
    npm through it. Unset, no sandbox is rewritten and the cache host is not admitted."""
    preview_service: str | None = None
    """`host:port` of the preview service the proxy relays the preview host to (RFC 0037). Set
    admits that host for every sandbox, whatever its internet policy. Unset, the host is not
    admitted and a share carries no rendered preview."""

    @model_validator(mode="after")
    def _ingress_base_is_addressable(self) -> "SandboxConfig":
        """The knob is `https` and an authority, and nothing else. Its two readers take it apart
        differently — the ingress resolves a request's site by stripping `hostname` off the Host,
        while `SurfaceContext.ingress_url` puts a label in front of `netloc` — and they agree only
        while the value carries no path, query, fragment, or userinfo: a path would be dropped from
        every minted link without a word, and userinfo would ride into the hostname the label goes
        in front of. `https` is required for a separate reason: the session cookie the ingress binds
        is `Secure`, so a plain-http base would boot green and 403 every visit. Rejected here so no
        deploy can boot holding either."""
        if self.ingress_public_url is None:
            return self
        base = urlsplit(self.ingress_public_url)
        if base.scheme != "https" or not base.hostname:
            raise ValueError(
                "sandbox.ingress_public_url must be an https base with a host "
                "(e.g. https://example.com) — a site's session cookie is `Secure`, so a "
                "plain-http origin can never carry one"
            )
        if base.path or base.query or base.fragment or base.username or base.password:
            raise ValueError(
                "sandbox.ingress_public_url is a scheme and a host only, with no path, query, "
                "fragment, or credentials (e.g. https://example.com) — every site's address "
                "is a label put in front of that host"
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
    source row the sync driver polls. `backend` names core's `folder` or a backend an extension
    registers through its Manifest `sources` point; serve refuses an unknown name at boot. A
    configured source is operator authority: its pages sync workspace-shared, and a local
    directory root enters only here, never from a chat act."""

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


class PackConfig(BaseModel):
    """The active pack. `name` selects one pack a workspace member under `packs/<name>/` registers
    through the `ufo.pack` entry point; activating it narrows the deploy to exactly the
    extensions that pack bundles plus its own pack-level skills and onboarding steps. Unset runs the
    unnarrowed extension set — the lockfile's pins, or every discovered extension in dev."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None


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
    pack: PackConfig = PackConfig()
    sources: tuple[SourceEntry, ...] = ()


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> Config:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create ufo.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
