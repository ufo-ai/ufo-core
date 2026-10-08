"""The client of the cloud API's public routes.

Core and a `cloud_client` extension call the memory and sources services through one base, the
deploy's `[cloud] api_url`, as one workspace at a time: every call presents the bearer the deploy's
`ProxyCredentials` answers for the bound workspace, so the system token never leaves core. A 4xx
raises `CloudRefused` with the contract's error code; a 5xx, a timeout, a transport error, or a
bearer that could not be read raises `CloudUnavailable`."""

import asyncio
import functools
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict

from ufo.runtime.workspace import ws

if TYPE_CHECKING:
    from ufo.runtime.ext.manifest import ProxyCredentials

CLOUD_TIMEOUT_SECONDS = 10.0
CLOUD_CONNECT_TIMEOUT_SECONDS = 3.0
CLOUD_BODY_MAX_BYTES = 8_388_608
IDEMPOTENCY_HEADER = "Idempotency-Key"
UNKNOWN_CODE = "unknown"


class CloudError(Exception):
    """A call to the cloud API that did not answer what its caller asked for."""


class CloudRefused(CloudError):
    """The cloud API refused the call with a 4xx: `code` is the contract's error code, or
    `unknown` when the body carried no error envelope."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"The cloud API answered {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class CloudUnavailable(CloudError):
    """The cloud API could not be reached or failed on its side, or the bearer to present it could
    not be read."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ErrorBody(BaseModel):
    """The `error` member of a refusal."""

    model_config = ConfigDict(extra="ignore")
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """The body every cloud service answers a refusal with."""

    model_config = ConfigDict(extra="ignore")
    error: ErrorBody


_TRANSPORTS: dict[asyncio.AbstractEventLoop, httpx.AsyncHTTPTransport] = {}


class LoopTransport(httpx.AsyncBaseTransport):
    """A connection pool per event loop: serve's surfaces and its durable workflows run on
    different loops, and a pooled connection answers only on the loop that opened it. A closed
    loop's pool leaves the registry on the next request from any loop; `dispose_loop_clients`
    closes the running loop's pool before that loop closes."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        loop = asyncio.get_running_loop()
        for stale in [held for held in list(_TRANSPORTS) if held.is_closed()]:
            _TRANSPORTS.pop(stale, None)
        transport = _TRANSPORTS.get(loop)
        if transport is None:
            transport = _TRANSPORTS[loop] = httpx.AsyncHTTPTransport()
        return await transport.handle_async_request(request)


async def dispose_loop_clients() -> None:
    """Close and drop the running loop's pool. The steps serve drives on throwaway `asyncio.run`
    loops call this before their loop closes, so no pooled connection is abandoned to it."""
    transport = _TRANSPORTS.pop(asyncio.get_running_loop(), None)
    if transport is not None:
        await transport.aclose()


@dataclass(frozen=True)
class CloudApi:
    """One workspace's client of the cloud API's public routes."""

    base_url: str
    bearer: Callable[[], Awaitable[str]]
    client: httpx.AsyncClient

    async def send[T: BaseModel](
        self,
        method: str,
        path: str,
        *,
        body: BaseModel | None = None,
        partial: bool = False,
        params: Sequence[tuple[str, str]] = (),
        idempotency_key: str | None = None,
        answer: type[T],
    ) -> T:
        """Send `body` to `path` and parse the 2xx answer as `answer`. `partial` sends only the
        fields the caller set, so an optional key the caller left alone is absent rather than
        null. A body past `CLOUD_BODY_MAX_BYTES` raises `ValueError` before anything is sent."""
        content = (
            None
            if body is None
            else json.dumps(
                body.model_dump(mode="json", exclude_unset=partial), separators=(",", ":")
            ).encode()
        )
        if content is not None and len(content) > CLOUD_BODY_MAX_BYTES:
            raise ValueError(f"A cloud request body is at most {CLOUD_BODY_MAX_BYTES} bytes.")
        try:
            bearer = await self.bearer()
        except Exception as error:
            raise CloudUnavailable("The cloud bearer could not be read.") from error
        headers = {"authorization": f"Bearer {bearer}"}
        if content is not None:
            headers["content-type"] = "application/json"
        if idempotency_key is not None:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        try:
            response = await self.client.request(
                method,
                self.base_url + path,
                content=content,
                params=list(params),
                headers=headers,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise CloudUnavailable(type(error).__name__) from error
        if response.is_server_error:
            raise CloudUnavailable(f"{path} answered {response.status_code}.")
        if response.is_client_error:
            try:
                refusal = ErrorEnvelope.model_validate_json(response.content).error
            except ValueError:
                raise CloudRefused(
                    response.status_code, UNKNOWN_CODE, f"{response.status_code} from {path}."
                ) from None
            raise CloudRefused(response.status_code, refusal.code, refusal.message)
        return answer.model_validate_json(response.content)


@dataclass(frozen=True)
class CloudApis:
    """The deploy's cloud API, bound per workspace. `clients` names the services
    `UFO_CLOUD_CLIENTS` selects for the deploy's own reads through it."""

    base_url: str
    client: httpx.AsyncClient
    bearer_for: Callable[[UUID], Awaitable[str]]
    clients: frozenset[str]

    def bound(self, workspace_id: UUID) -> CloudApi:
        """The client that calls as `workspace_id`."""
        return CloudApi(
            base_url=self.base_url,
            bearer=functools.partial(self.bearer_for, workspace_id),
            client=self.client,
        )


def proxy_bearer(credentials: "ProxyCredentials") -> Callable[[UUID], Awaitable[str]]:
    """The bearer the deploy's `ProxyCredentials` answers for a workspace, the one the proxy
    session presents, minted on first use."""

    async def bearer_for(workspace_id: UUID) -> str:
        with ws(workspace_id):
            return await credentials.bearer()

    return bearer_for
