"""The reference environment host: one overrides document served to the main-agent turns that pin
it.

`ufoctl dev-host overrides.json` runs it locally; an eval arm is one such document — a replacement
system prompt, rewritten tool descriptions, withheld tools — and a stack whose deployment sets
`environment.dev_host_allowed` applies it to any turn admitted with the host's URL pinned. The
document targets the main-agent prompt, and a pinned host is inherited by every spawned child, so
a subagent turn is answered with no change rather than the parent's replacement prompt."""

from pathlib import Path

import uvicorn
from fastapi import FastAPI

from ufo.harness.o11y import log
from ufo.runtime.environment import ENVIRONMENT_PATH, EnvironmentOverrides, EnvironmentRequest


def overrides_app(overrides: EnvironmentOverrides) -> FastAPI:
    app = FastAPI()

    @app.post(ENVIRONMENT_PATH)
    async def environment(request: EnvironmentRequest) -> EnvironmentOverrides:
        log(
            "devhost.environment",
            agent=request.agent_name,
            model=request.model,
            tools=len(request.tools),
            subagent_profile=request.subagent_profile,
        )
        if request.subagent_profile is not None:
            return EnvironmentOverrides()
        return overrides

    return app


def serve_dev_host(overrides_path: Path, host: str, port: int) -> None:
    overrides = EnvironmentOverrides.model_validate_json(overrides_path.read_text())
    uvicorn.run(overrides_app(overrides), host=host, port=port)
