"""Deploy configuration: one ufo.toml, fail loud."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ufo.models.interface import AUTO_MODEL
from ufo.schema.records import DEFAULT_REASONING_EFFORT, ReasoningEffort

CONFIG_PATH_ENV = "UFO_CONFIG"
DEFAULT_CONFIG_PATH = Path("ufo.toml")
IN_PROCESS_BACKEND = "in_process"
DEFAULT_CDP_PROVIDER = "sandbox_chrome"
DEFAULT_AUTO_MODEL = "claude-opus-4-8"
DEFAULT_PROXY_PORT = 8888


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
    """The blob store — transcripts, compaction records, sandbox workspaces, shared artifacts.
    `endpoint_url`/`region` are the host-reachable S3 the serve process talks to. To mount a
    conversation's `workspace/` prefix into its sandbox over s3fs, the S3 backend additionally
    needs `sts_role_arn` (the role the mint assumes), `s3_url` (the sandbox-reachable S3 endpoint
    s3fs dials — may differ from `endpoint_url`), `sts_endpoint` (AWS or a MinIO STS endpoint), and
    `path_style` (MinIO addressing); their absence is caught loud at the mount seam, not here, so a
    blob-only S3 deploy (transcripts/artifacts, no sandbox mount) needs only `bucket`."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["filesystem", "s3"]
    root: Path | None = None
    bucket: str | None = None
    endpoint_url: str | None = None
    region: str | None = None
    s3_url: str | None = None
    sts_role_arn: str | None = None
    sts_endpoint: str | None = None
    path_style: bool = False

    @model_validator(mode="after")
    def _backend_complete(self) -> "BlobConfig":
        if self.backend == "filesystem" and self.root is None:
            raise ValueError("blob.root is required for the filesystem backend")
        if self.backend == "s3" and self.bucket is None:
            raise ValueError("blob.bucket is required for the s3 backend")
        return self


class ModelsConfig(BaseModel):
    """`reasoning_effort` is the extended-thinking depth every agent turn requests (`off` disables
    it); `auto_model` is the concrete model an agent authored with `model = "auto"` resolves to at
    turn time, so an agent stays model-agnostic and the deploy pins the backend."""

    model_config = ConfigDict(extra="forbid")
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    openai_api_key_env: str = "OPENAI_API_KEY"
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    auto_model: str = DEFAULT_AUTO_MODEL

    @model_validator(mode="after")
    def _auto_model_concrete(self) -> "ModelsConfig":
        if not self.auto_model or self.auto_model == AUTO_MODEL:
            raise ValueError("models.auto_model must be a concrete model id, not empty or 'auto'")
        return self


class CredentialsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_env: str = "UFO_CREDENTIAL_KEY"


class ServeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 8710


class ConnectConfig(BaseModel):
    """OAuth connect settings. `public_base_url` is the deploy's externally reachable base (scheme
    and host, e.g. `https://ufo.example.com`) that a provider redirects the member's browser
    back to; `ConnectFlow.redirect_uri` derives from it. Absent when no connector provider is
    installed (connect is inert); required, and never a bind address, once one is."""

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


class O11yConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    otlp_endpoint: str | None = None


class SandboxConfig(BaseModel):
    """Which carrier runs the per-conversation sandbox. `backend` names a registered carrier — the
    zero-dependency `local` carrier ships with core, an extension registers more (`docker`, `e2b`, a
    remote runner) through its `carriers` Manifest point — and `serve` fails loud on a name no
    carrier registers. Each carrier sources its own parameters (template, keys); core config knows
    only the selected name.

    `proxy_port` is the stable port the egress proxy binds. `proxy_public_url` is the externally
    reachable base a remote sandbox carrier such as E2B dials; local carriers leave it unset and
    reach the process-local proxy directly. `serve` fails loud when a remote carrier has no public
    proxy URL — open, unmetered egress is never a silent default."""

    model_config = ConfigDict(extra="forbid")
    backend: str = "local"
    proxy_port: int = DEFAULT_PROXY_PORT
    proxy_public_url: str | None = None


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
    source row the sync driver polls."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["folder"]
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


class BrowserConfig(BaseModel):
    """The browser transport. `cdp_provider` selects where the one BUA engine (the browser
    extension) connects — every provider is contributed by an extension at the `cdp_providers`
    Manifest point; core ships none. The default `sandbox_chrome` leases CDP from the turn's own
    sandbox (zero-config, no URL); `browserbase` leases a remote hosted endpoint. Selecting a name
    no active extension registers fails loud at boot only when a browser extension needs it."""

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
    through its Manifest `search_providers` point (e.g. the `exa` backend); the research extension's
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
    o11y: O11yConfig = O11yConfig()
    sandbox: SandboxConfig = SandboxConfig()
    ext: ExtConfig = ExtConfig()
    artifacts: ArtifactsConfig = ArtifactsConfig()
    hub: HubConfig = HubConfig()
    browser: BrowserConfig = BrowserConfig()
    connectors: ConnectorsConfig = ConnectorsConfig()
    research: ResearchConfig = ResearchConfig()
    pack: PackConfig = PackConfig()


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> Config:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create ufo.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
