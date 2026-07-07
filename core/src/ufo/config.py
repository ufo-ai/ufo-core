"""Deploy configuration: one ufo.toml, fail loud."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ufo.models.interface import AUTO_MODEL, DEFAULT_REASONING_EFFORT, ReasoningEffort

CONFIG_PATH_ENV = "UFO_CONFIG"
DEFAULT_CONFIG_PATH = Path("ufo.toml")
IN_PROCESS_BACKEND = "in_process"
DEFAULT_CDP_PROVIDER = "sandbox-cdp"
DEFAULT_AUTO_MODEL = "claude-opus-4-8"


class DatabaseConfig(BaseModel):
    """`system_url` is the DBOS system store's sync-driver url. Unset, it derives as a `_dbos`
    sibling of the schema database — the single-workspace default. A shared-database deploy sets it
    explicitly per tenant so tenants never derive the same system store and cross-recover each
    other's workflows."""

    model_config = ConfigDict(extra="forbid")
    url: str
    system_url: str = ""

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
    base-pinned `"default"` backend — the `index-default` extension (SQLite FTS5 + local cosine,
    Postgres tsvector + pgvector) and the `embed-openai` extension."""

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
    only the selected name."""

    model_config = ConfigDict(extra="forbid")
    backend: str = "local"


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
    """One `[[sources]]` block: which backend, with its config. Boot registers each as a source
    row; the sync driver polls those rows."""

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
    extension) connects: core's default `sandbox-cdp` provider (a static lease over the
    `BROWSER_CDP_URL` endpoint) or any provider an extension registers through its Manifest
    `cdp_providers` point (browserbase mints a hosted session per turn). Selecting a name no
    extension registers fails loud at boot."""

    model_config = ConfigDict(extra="forbid")
    cdp_provider: str = DEFAULT_CDP_PROVIDER


class ConnectorsConfig(BaseModel):
    """Feed-sync connector settings. `auth_backend` names the auth-proxy backend the sync runner
    resolves a connector's provider credential through — the Composio broker default (the token
    stays server-side), a `direct` BYOK backend (a member-added key read host-side), or any backend
    an extension registers through its Manifest `auth_proxies` point. Selecting a name no extension
    registers fails loud at boot; a deploy with no auth-proxy extension installed runs no connector
    source."""

    model_config = ConfigDict(extra="forbid")
    auth_backend: str = "composio"


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


class DeployConfig(BaseModel):
    """`ufoctl deploy` settings — the set-once identity of this deploy, so the verb takes no
    per-run flags. `backend` selects how the deploy request is realized: `compose` (a local
    single-box stack) or `k8s` (posted to a ufo-control control plane). `remote` is that control
    plane's base URL — posting is automatic when it (or `--remote`) is set. `host` is the public
    hostname; unset, it derives as `<name>.<base_domain>` when `base_domain` is given, else
    `localhost` for the compose backend (the k8s backend fails loud without one). `name` is the
    tenant slug, defaulting to the workspace's agent name. `bundle_image` is the digest-pinned
    (`repo@sha256:…`) serve image the control plane runs; `sandbox_image` is the digest-pinned image
    the `docker`/`pod` carrier pulls, left unset when the carrier needs none (the `e2b` carrier runs
    from its own template). Both come from a build+push, so they are set here, not derived.
    `owner_email` is never here: it is read from the workspace the owner's `ufoctl init` created."""

    model_config = ConfigDict(extra="forbid")
    backend: Literal["compose", "k8s"] = "compose"
    remote: str | None = None
    host: str | None = None
    base_domain: str | None = None
    name: str | None = None
    bundle_image: str | None = None
    sandbox_image: str | None = None


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
    sources: tuple[SourceEntry, ...] = ()
    artifacts: ArtifactsConfig = ArtifactsConfig()
    hub: HubConfig = HubConfig()
    browser: BrowserConfig = BrowserConfig()
    connectors: ConnectorsConfig = ConnectorsConfig()
    research: ResearchConfig = ResearchConfig()
    pack: PackConfig = PackConfig()
    deploy: DeployConfig = DeployConfig()


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> Config:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create ufo.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
