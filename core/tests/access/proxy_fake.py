import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

FAKE_CA_PEM = "-----BEGIN CERTIFICATE-----\nproxy-fake-ca\n-----END CERTIFICATE-----\n"
SESSION_TOKEN_PREFIX = "ufo-session-"
SENTINEL_HEAD = "ufo-sentinel-"
NO_PROXY = "localhost,127.0.0.1,::1"
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
CREATE_FIELDS = frozenset({"ttl_s", "labels", "budget", "policy"})
PATCH_FIELDS = frozenset({"policy", "labels", "budget"})
POLICY_FIELDS = frozenset({"internet", "hosts", "bind", "routes", "cache"})
BIND_FIELDS = frozenset({"host", "header", "secret", "env"})
LABEL_FILTER = "labels."
DEFAULT_TTL_S = 3600
MAX_TTL_S = 86400
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
CODES = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    500: "internal",
    502: "upstream_unavailable",
    503: "upstream_unavailable",
}

Handler = Callable[[Request], Awaitable[Response]]
Session = dict[str, Any]


def proxy_app(bearer: str, ca_pem: str = FAKE_CA_PEM) -> Starlette:
    app = Starlette(
        routes=[
            Route("/v1/sessions", _received(_create), methods=["POST"]),
            Route("/v1/sessions", _received(_list), methods=["GET"]),
            Route("/v1/sessions/self", _received(_self), methods=["GET"]),
            Route("/v1/sessions/{id:uuid}", _received(_get), methods=["GET"]),
            Route("/v1/sessions/{id:uuid}", _received(_patch), methods=["PATCH"]),
            Route("/v1/sessions/{id:uuid}/renew", _received(_renew), methods=["POST"]),
            Route("/v1/sessions/{id:uuid}/revoke", _received(_revoke), methods=["POST"]),
            Route("/v1/proxy/ca", _received(_ca), methods=["GET"]),
        ]
    )
    app.state.bearer = bearer
    app.state.ca_pem = ca_pem
    app.state.workspace_id = uuid4()
    app.state.token_id = uuid4()
    app.state.sessions = {}
    app.state.keys = {}
    app.state.calls = []
    app.state.fault = None
    app.state.page_size = MAX_LIMIT
    return app


def _received(handler: Handler) -> Handler:
    async def received(request: Request) -> Response:
        query = request.url.query
        target = f"{request.url.path}?{query}" if query else request.url.path
        request.app.state.calls.append(
            (request.method, target, dict(request.headers), await request.body())
        )
        fault = request.app.state.fault
        if fault is not None:
            return _error(fault, "The proxy service failed.", CODES.get(fault, "internal"))
        return await handler(request)

    return received


async def _create(request: Request) -> Response:
    if not _system(request):
        return _error(401, "The bearer does not verify.")
    body = await _json(request)
    if not (
        isinstance(body, dict)
        and body.keys() <= CREATE_FIELDS
        and _valid_policy(body.get("policy"))
        and _valid_ttl(body.get("ttl_s", DEFAULT_TTL_S))
        and _valid_labels(body.get("labels", {}))
    ):
        return _error(400, "The body does not match the session API.")
    state = request.app.state
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    key = request.headers.get("idempotency-key")
    if key is not None and (state.bearer, key) in state.keys:
        held, session_id = state.keys[(state.bearer, key)]
        if held != digest:
            return _error(409, "The idempotency key was used with another request.")
        return JSONResponse(_created(request, state.sessions[session_id]))
    now = datetime.now(UTC)
    session_id = uuid4()
    session: Session = {
        "id": session_id,
        "version": 1,
        "labels": body.get("labels", {}),
        "policy": body["policy"],
        "budget": body.get("budget"),
        "created_at": now,
        "expires_at": now + timedelta(seconds=body.get("ttl_s", DEFAULT_TTL_S)),
        "revoked_at": None,
        "token": f"{SESSION_TOKEN_PREFIX}{session_id.hex}.{secrets.token_hex(16)}",
        "sentinels": _sentinels(body["policy"], {}),
    }
    state.sessions[session_id] = session
    if key is not None:
        state.keys[(state.bearer, key)] = (digest, session_id)
    return JSONResponse(_created(request, session), status_code=201)


async def _list(request: Request) -> Response:
    if not _system(request):
        return _error(401, "The bearer does not verify.")
    params = request.query_params
    filters = {
        name.removeprefix(LABEL_FILTER): value
        for name, value in params.items()
        if name.startswith(LABEL_FILTER)
    }
    limit = params.get("limit", str(DEFAULT_LIMIT))
    cursor = params.get("cursor", "0")
    if (
        len(filters) > 1
        or not limit.isdigit()
        or not 1 <= int(limit) <= MAX_LIMIT
        or not cursor.isdigit()
    ):
        return _error(400, "The query does not match the session API.")
    newest = sorted(
        request.app.state.sessions.values(), key=lambda held: held["created_at"], reverse=True
    )
    matching = [
        held
        for held in newest
        if all(held["labels"].get(name) == value for name, value in filters.items())
    ]
    start = int(cursor)
    end = start + min(int(limit), request.app.state.page_size)
    return JSONResponse(
        {
            "items": [_view(request, held) for held in matching[start:end]],
            "next_cursor": str(end) if end < len(matching) else None,
        }
    )


async def _self(request: Request) -> Response:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    session = next(
        (held for held in request.app.state.sessions.values() if held["token"] == token), None
    )
    if session is None:
        return _error(401, "The session token does not verify.")
    return JSONResponse(_with_env(request, session))


async def _get(request: Request) -> Response:
    if not _system(request):
        return _error(401, "The bearer does not verify.")
    session = request.app.state.sessions.get(request.path_params["id"])
    if session is None:
        return _error(404, "The workspace holds no such session.")
    return JSONResponse(_view(request, session))


async def _patch(request: Request) -> Response:
    if not _system(request):
        return _error(401, "The bearer does not verify.")
    session = request.app.state.sessions.get(request.path_params["id"])
    if session is None:
        return _error(404, "The workspace holds no such session.")
    body = await _json(request)
    if not (
        isinstance(body, dict)
        and body
        and body.keys() <= PATCH_FIELDS
        and ("policy" not in body or _valid_policy(body["policy"]))
        and _valid_labels(body.get("labels", {}))
    ):
        return _error(400, "The body does not match the session API.")
    ended = _ended(session)
    if ended is not None:
        return ended
    if "policy" in body:
        session["sentinels"] = _sentinels(body["policy"], session["sentinels"])
    session.update(body)
    session["version"] += 1
    return JSONResponse(_with_env(request, session))


async def _renew(request: Request) -> Response:
    if not _system(request):
        return _error(401, "The bearer does not verify.")
    session = request.app.state.sessions.get(request.path_params["id"])
    if session is None:
        return _error(404, "The workspace holds no such session.")
    body = await _json(request)
    if not (
        isinstance(body, dict)
        and body.keys() <= {"ttl_s"}
        and _valid_ttl(body.get("ttl_s", DEFAULT_TTL_S))
    ):
        return _error(400, "The body does not match the session API.")
    ended = _ended(session)
    if ended is not None:
        return ended
    session["expires_at"] = datetime.now(UTC) + timedelta(seconds=body.get("ttl_s", DEFAULT_TTL_S))
    return JSONResponse({"id": str(session["id"]), "expires_at": session["expires_at"].isoformat()})


async def _revoke(request: Request) -> Response:
    if not _system(request):
        return _error(401, "The bearer does not verify.")
    session = request.app.state.sessions.get(request.path_params["id"])
    if session is None:
        return _error(404, "The workspace holds no such session.")
    if session["revoked_at"] is None:
        session["revoked_at"] = datetime.now(UTC)
    return JSONResponse({"id": str(session["id"]), "revoked_at": session["revoked_at"].isoformat()})


async def _ca(request: Request) -> Response:
    return JSONResponse({"ca_pem": request.app.state.ca_pem})


def _system(request: Request) -> bool:
    return request.headers.get("authorization") == f"Bearer {request.app.state.bearer}"


async def _json(request: Request) -> object:
    try:
        return json.loads(await request.body())
    except ValueError:
        return None


def _valid_policy(policy: object) -> bool:
    if not isinstance(policy, dict) or not policy.keys() <= POLICY_FIELDS:
        return False
    binds = policy.get("bind", [])
    return isinstance(binds, list) and all(
        isinstance(bind, dict) and bind.keys() == BIND_FIELDS for bind in binds
    )


def _valid_ttl(ttl_s: object) -> bool:
    return isinstance(ttl_s, int) and 1 <= ttl_s <= MAX_TTL_S


def _valid_labels(labels: object) -> bool:
    return isinstance(labels, dict) and all(
        isinstance(name, str) and isinstance(value, str) for name, value in labels.items()
    )


def _ended(session: Session) -> Response | None:
    if session["revoked_at"] is not None:
        return _error(409, "The session was revoked.", "session_revoked")
    if session["expires_at"] <= datetime.now(UTC):
        return _error(409, "The session has expired.")
    return None


def _sentinels(policy: dict[str, Any], held: dict[str, str]) -> dict[str, str]:
    return {
        env: held.get(env) or f"{SENTINEL_HEAD}{secrets.token_hex(16)}"
        for env in sorted({bind["env"] for bind in policy.get("bind", [])})
    }


def _view(request: Request, session: Session) -> dict[str, Any]:
    policy = session["policy"]
    revoked_at = session["revoked_at"]
    return {
        "id": str(session["id"]),
        "workspace_id": str(request.app.state.workspace_id),
        "token_id": str(request.app.state.token_id),
        "version": session["version"],
        "labels": session["labels"],
        "policy": {
            "internet": policy.get("internet", False),
            "hosts": policy.get("hosts", []),
            "bind": [bind | {"header": bind["header"].lower()} for bind in policy.get("bind", [])],
            "routes": [
                {
                    "host": route["host"],
                    "upstream": route["upstream"],
                    "headers": sorted(route.get("headers", {})),
                }
                for route in policy.get("routes", [])
            ],
            "cache": {"git": False, "packages": False} | policy.get("cache", {}),
        },
        "budget": session["budget"],
        "created_at": session["created_at"].isoformat(),
        "expires_at": session["expires_at"].isoformat(),
        "revoked_at": None if revoked_at is None else revoked_at.isoformat(),
        "proxy_url": f"{request.url.scheme}://{request.url.netloc}",
    }


def _with_env(request: Request, session: Session) -> dict[str, Any]:
    proxied = f"https://{session['token']}:ufo@{request.url.netloc}"
    env = (
        dict.fromkeys(PROXY_VARIABLES, proxied)
        | {"NO_PROXY": NO_PROXY, "no_proxy": NO_PROXY}
        | session["sentinels"]
    )
    return _view(request, session) | {"env": env}


def _created(request: Request, session: Session) -> dict[str, Any]:
    return _with_env(request, session) | {
        "token": session["token"],
        "ca_pem": request.app.state.ca_pem,
    }


def _error(status: int, message: str, code: str | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code or CODES[status], "message": message}}, status_code=status
    )
