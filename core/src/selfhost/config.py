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


class SlackSurfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = False
    team_id: str
    bot_user_id: str


class SurfacesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slack: SlackSurfaceConfig | None = None


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: DatabaseConfig
    blob: BlobConfig
    models: ModelsConfig = ModelsConfig()
    credentials: CredentialsConfig = CredentialsConfig()
    serve: ServeConfig = ServeConfig()
    o11y: O11yConfig = O11yConfig()
    surfaces: SurfacesConfig = SurfacesConfig()


def load_config(path: Path | None = None) -> Config:
    resolved = path or Path(os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing config file {resolved} (create selfhost.toml or set {CONFIG_PATH_ENV})"
        )
    return Config.model_validate(tomllib.loads(resolved.read_text()))
