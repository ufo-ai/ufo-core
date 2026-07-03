"""Deploy configuration: one selfhost.toml, fail loud."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

CONFIG_PATH_ENV = "SELFHOST_CONFIG"
DEFAULT_CONFIG_PATH = Path("selfhost.toml")


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
    model_config = ConfigDict(extra="forbid")
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    openai_api_key_env: str = "OPENAI_API_KEY"


class CredentialsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_env: str = "SELFHOST_CREDENTIAL_KEY"


class ServeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 8710


class O11yConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    otlp_endpoint: str | None = None


class HubConfig(BaseModel):
    """Which stream hub backs live deltas. Core ships only the in-process hub; `shared` declares a
    shared-hub extension (Redis) is wired, and the boot guard treats an unshared hub as a dev
    default no second instance may run against."""

    model_config = ConfigDict(extra="forbid")
    shared: bool = False


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


class SlackSurfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False
    team_id: str
    bot_user_id: str


class WebSurfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False


class SurfacesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slack: SlackSurfaceConfig | None = None
    web: WebSurfaceConfig | None = None


class ArtifactsConfig(BaseModel):
    """The HMAC secret that signs artifact delivery tokens. `share_file` mints with it and the web
    surface verifies with it, so it belongs to neither consumer: `token_secret_env` names the env
    var holding the secret."""

    model_config = ConfigDict(extra="forbid")
    token_secret_env: str = "SELFHOST_ARTIFACT_TOKEN_SECRET"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: DatabaseConfig
    blob: BlobConfig
    models: ModelsConfig = ModelsConfig()
    credentials: CredentialsConfig = CredentialsConfig()
    serve: ServeConfig = ServeConfig()
    o11y: O11yConfig = O11yConfig()
    hub: HubConfig = HubConfig()
    ext: ExtConfig = ExtConfig()
    sources: tuple[SourceEntry, ...] = ()
    surfaces: SurfacesConfig = SurfacesConfig()
    artifacts: ArtifactsConfig = ArtifactsConfig()


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_config(path: Path | None = None) -> Config:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create selfhost.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
