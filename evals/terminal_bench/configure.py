"""Render the pinned Terminal-Bench service configuration into an isolated run directory."""

import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import tomli_w

from ufo.config import BlobConfig, ConnectConfig, DatabaseConfig, ModelsConfig, load_config

TEMPLATE = Path(__file__).with_name("ufo.toml")
DEFAULT_ROOT = Path(".local/terminal_bench/control")
DEFAULT_SERVE_PORT = 57431
DEFAULT_PROXY_PORT = 57432
CONFIG_FILE = "ufo.toml"
CONFIG_DIGEST_FILE = "ufo.toml.sha256"


def render_config(
    root: Path,
    public_base_url: str,
    model: str,
    serve_port: int = DEFAULT_SERVE_PORT,
    proxy_port: int = DEFAULT_PROXY_PORT,
) -> Path:
    """Write one runnable config whose behavioral choices come from the tracked template."""
    parsed = urlsplit(public_base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Terminal-Bench public base URL must be public HTTPS")
    if not model:
        raise ValueError("Terminal-Bench model must not be empty")
    if not 1 <= serve_port <= 65_535 or not 1 <= proxy_port <= 65_535:
        raise ValueError("Terminal-Bench ports must be between 1 and 65535")
    if serve_port == proxy_port:
        raise ValueError("Terminal-Bench serve and proxy ports must differ")

    destination = root.resolve()
    database_url = f"sqlite+aiosqlite:///{destination / 'ufo.db'}"
    template = load_config(TEMPLATE)
    config = template.model_copy(
        update={
            "database": DatabaseConfig(url=database_url, owner_url=database_url),
            "blob": BlobConfig(backend="filesystem", root=destination / "blobs"),
            "models": ModelsConfig.model_validate(
                template.models.model_dump() | {"auto_model": model}
            ),
            "serve": template.serve.model_copy(update={"host": "127.0.0.1", "port": serve_port}),
            "connect": ConnectConfig(public_base_url=public_base_url),
            "sandbox": template.sandbox.model_copy(
                update={
                    "workspace_root": destination / "workspaces",
                    "proxy_port": proxy_port,
                }
            ),
        }
    )
    payload = tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)).encode()
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / CONFIG_FILE
    output.write_bytes(payload)
    (destination / CONFIG_DIGEST_FILE).write_text(f"{hashlib.sha256(payload).hexdigest()}\n")
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.terminal_bench.configure")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--model", default=load_config(TEMPLATE).models.auto_model)
    parser.add_argument("--serve-port", type=int, default=DEFAULT_SERVE_PORT)
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    args = parser.parse_args(argv)
    try:
        output = render_config(
            args.root,
            args.public_base_url,
            args.model,
            args.serve_port,
            args.proxy_port,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(output)


if __name__ == "__main__":
    main()
