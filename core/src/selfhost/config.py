"""Deploy configuration: one selfhost.toml, fail loud."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from selfhost.models.interface import AUTO_MODEL, DEFAULT_REASONING_EFFORT, ReasoningEffort

CONFIG_PATH_ENV = "SELFHOST_CONFIG"
DEFAULT_CONFIG_PATH = Path("selfhost.toml")
IN_PROCESS_BACKEND = "in_process"
BUA_BROWSER_BACKEND = "bua"
DEFAULT_AUTO_MODEL = "claude-opus-4-8"


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str

    @property
    def system_url(self) -> str:
        """The DBOS system store: a _dbos sibling of the schema database, sync-driver url."""
        base, _, name = self.url.rpartition("/")
        if self.url.startswith("sqlite"):
            stem, dot, suffix = name.partition(".")
            return f"{base}/{stem}_dbos{dot}{suffix}".replace("sqlite+aiosqlite", "sqlite", 1)
        return f"{base}/{name}_dbos".replace("postgresql+asyncpg", "postgresql+psycopg", 1)


class BlobConfig(BaseModel):
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
    key_env: str = "SELFHOST_CREDENTIAL_KEY"


class ServeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 8710


class ConnectConfig(BaseModel):
    """OAuth connect settings. `public_base_url` is the deploy's externally reachable base (scheme
    and host, e.g. `https://selfhost.example.com`) that a provider redirects the member's browser
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
    """The extension store toggle. `store` names a catalog file the `selfhost ext` commands search
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


class WebSurfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False


class SurfacesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    web: WebSurfaceConfig | None = None


class ArtifactsConfig(BaseModel):
    """The HMAC secret that signs artifact delivery tokens. `share_file` mints with it and the web
    surface verifies with it, so it belongs to neither consumer: `token_secret_env` names the env
    var holding the secret."""

    model_config = ConfigDict(extra="forbid")
    token_secret_env: str = "SELFHOST_ARTIFACT_TOKEN_SECRET"


class HubConfig(BaseModel):
    """The live-frame hub backend. `backend` selects among the in-process default and any backend
    an extension registers through its Manifest `hubs` point; `url` is the selected backend's
    connection string (e.g. `redis://…`), carried in config like `database.url`. A cross-process
    backend fans frames out across instances, which is what lifts the single-instance boot guard."""

    model_config = ConfigDict(extra="forbid")
    backend: str = IN_PROCESS_BACKEND
    url: str | None = None


class BrowserConfig(BaseModel):
    """The browser backend. `backend` selects among core's default `bua` engine (Chrome over the
    CDP endpoint the `BROWSER_CDP_URL` provider yields) and any backend an extension registers
    through its Manifest `browsers` point (browserbase, browser-use). Selecting a name no extension
    registers fails loud at boot."""

    model_config = ConfigDict(extra="forbid")
    backend: str = BUA_BROWSER_BACKEND


class PackConfig(BaseModel):
    """The active pack. `name` selects one pack a workspace member under `packs/<name>/` registers
    through the `selfhost.pack` entry point; activating it narrows the deploy to exactly the
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
    sources: tuple[SourceEntry, ...] = ()
    surfaces: SurfacesConfig = SurfacesConfig()
    artifacts: ArtifactsConfig = ArtifactsConfig()
    hub: HubConfig = HubConfig()
    browser: BrowserConfig = BrowserConfig()
    pack: PackConfig = PackConfig()


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> Config:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create selfhost.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
