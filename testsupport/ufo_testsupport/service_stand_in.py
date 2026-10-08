"""A cloud service as a test serves it: every operation of its golden wire file on its method and
path, answering the golden answer, or one a test set, and recording what each call sent."""

import json
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

UNAUTHORIZED = {
    "error": {"code": "unauthorized", "message": "The bearer is missing or does not verify."}
}


@dataclass(frozen=True)
class SentRequest:
    """One call a stand-in answered: the operation it matched and what the caller sent."""

    operation: str
    method: str
    path: str
    query: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]
    body: object


class ServiceStandIn:
    """Serves a golden wire file. A route's template matches its literal segments before its
    parameters, and operations sharing a method and path are told apart by the body's `pass`."""

    def __init__(self, golden: Path) -> None:
        self._golden: dict[str, dict] = json.loads(golden.read_text(encoding="utf-8"))
        self._replaced: dict[str, tuple[int, object]] = {}
        self._queued: dict[str, deque[tuple[int, object]]] = {}
        self.sent: list[SentRequest] = []
        routes: dict[tuple[str, str], list[str]] = {}
        for operation, pair in self._golden.items():
            if pair["path"].startswith("/"):
                routes.setdefault((pair["method"], pair["path"]), []).append(operation)
        self._app = Starlette(
            routes=[
                Route(path, self._endpoint(operations), methods=[method])
                for (method, path), operations in sorted(
                    routes.items(),
                    key=lambda item: [part.startswith("{") for part in item[0][1].split("/")],
                )
            ]
        )

    @property
    def app(self) -> Starlette:
        """The ASGI app serving every operation."""
        return self._app

    def answer(self, operation: str, status: int, body: object) -> None:
        """Answer `operation` with `status` and `body` in place of its golden answer."""
        self._replaced[operation] = (status, body)

    def queue(self, operation: str, answers: Sequence[tuple[int, object]]) -> None:
        """Answer the next calls of `operation` with `answers` in turn, then as before."""
        self._queued.setdefault(operation, deque()).extend(answers)

    def _endpoint(self, operations: list[str]) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def endpoint(request: Request) -> JSONResponse:
            if "authorization" not in request.headers:
                return JSONResponse(UNAUTHORIZED, status_code=401)
            raw = await request.body()
            body = json.loads(raw) if raw else None
            operation = self._operation(operations, body)
            self.sent.append(
                SentRequest(
                    operation=operation,
                    method=request.method,
                    path=request.url.path,
                    query=tuple(request.query_params.multi_items()),
                    headers=dict(request.headers),
                    body=body,
                )
            )
            status, answer = self._answer(operation)
            return JSONResponse(answer, status_code=status)

        return endpoint

    def _operation(self, operations: list[str], body: object) -> str:
        if len(operations) == 1:
            return operations[0]
        if not isinstance(body, dict):
            raise ValueError(f"A call to {', '.join(operations)} names its pass in a body.")
        return next(
            operation
            for operation in operations
            if self._golden[operation]["request"]["pass"] == body["pass"]
        )

    def _answer(self, operation: str) -> tuple[int, object]:
        queued = self._queued.get(operation)
        if queued:
            return queued.popleft()
        if operation in self._replaced:
            return self._replaced[operation]
        golden = self._golden[operation]["answer"]
        return golden["status"], golden["body"]
