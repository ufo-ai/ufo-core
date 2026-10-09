"""RestConnector: the shared read scaffolding every REST-API connector reuses.

Owns the boilerplate a provider would otherwise duplicate: the httpx client (built from whichever
`Credential` the auth-proxy resolved — a broker's proxying transport, a bearer token, or auth
headers), retry on transient transport/5xx responses, a durable yield on provider rate limits where
the caller has a safe checkpoint, and one page loop per declared `PaginationStrategy`.
Async — a connector runs from the core sync driver where a blocking network call would stall every
other surface.

A subclass sets `name`, `base_url`, `streams_list`, and either declares `Pagination` on each stream
(routing through `paginate_from_strategy`) or overrides `paginate` for provider-specific shapes
(GitHub's org/repo fan-out). Inside an override, await `self._get` / `self._get_raw` so retry stays
uniform. `flatten` is a passthrough; override for payloads that nest under an envelope. The write
path (CRUD, field discovery) is deliberately absent — the source seam only reads."""

import asyncio
import math
import random
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextvars import ContextVar
from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import parse_qsl

import httpx

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.connector import (
    Connector,
    PaginationStrategy,
    ParentPages,
    Run,
    StreamPage,
    StreamSpec,
    get_path,
    no_parents,
)

MAX_ATTEMPTS = 8
RETRY_INITIAL_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 30.0
RETRY_AFTER_MAX_SECONDS = 60.0
RETRY_BUDGET_SECONDS = 120.0
RATE_LIMIT_DEFAULT_SECONDS = 60.0
RATE_LIMIT_MAX_SECONDS = 3600.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_STATUS = frozenset({429, 503, 504})
JITTER_MAX_FACTOR = 1.5
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
ERROR_BODY_CAP = 800
MAX_PAGES = 10_000

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.IGNORECASE)
_RATE_LIMIT_YIELDS = ContextVar("source_rate_limit_yields", default=True)


class ProviderRateLimited(Exception):
    """A provider cooldown that the durable source scheduler must wait outside the worker."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("provider rate limited")
        self.retry_after_seconds = retry_after_seconds


def list_or_empty(value: Any) -> list[dict[str, Any]]:
    """The dict items of `value` when it is a list, else `[]`."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def dict_or_empty(value: Any) -> dict[str, Any]:
    """`value` when it is a dict-shaped record, else `{}` — the record-shaping sibling of
    `list_or_empty`, for a provider that reaches into a nested object off a page record."""
    return value if isinstance(value, dict) else {}


def records_at(data: Any, path: str | None) -> list[dict[str, Any]]:
    if path is None:
        return list_or_empty(data)
    if not isinstance(data, Mapping):
        return []
    return list_or_empty(get_path(data, path, []))


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]:
    """Copy each record and stamp partition context (the site's `cloud_id`, a parent id) onto it, so
    a downstream fan-out and `render` can resolve the record's origin."""
    return [{**record, **context} for record in records]


def next_link(headers: httpx.Headers) -> str | None:
    link = headers.get("link")
    if not link:
        return None
    match = _LINK_NEXT_RE.search(link)
    return match.group(1) if match else None


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS
    return False


def _retry_after(error: BaseException) -> float | None:
    """A 503/504 behind a quota proxy names its reset as a 429 does. A `nan` would poison the shared
    loop's timer heap in `asyncio.sleep`."""
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    if error.response.status_code not in RETRY_AFTER_STATUS:
        return None
    header = error.response.headers.get("retry-after")
    if header is None:
        return None
    try:
        wait = float(header)
    except ValueError:
        return None
    if not math.isfinite(wait) or wait < 0.0:
        return None
    return wait


def _retry_wait(error: BaseException, delay: float) -> float:
    after = _retry_after(error)
    stated = None if after is None else min(after, RETRY_AFTER_MAX_SECONDS)
    ladder = min(delay, RETRY_MAX_DELAY_SECONDS)
    if stated is not None and stated > ladder:
        floor, cap = stated, RETRY_AFTER_MAX_SECONDS
    else:
        floor, cap = ladder, RETRY_MAX_DELAY_SECONDS
    return random.uniform(floor, min(floor * JITTER_MAX_FACTOR, cap))


def _raise_for_status(response: httpx.Response) -> None:
    """`raise_for_status` whose message carries the body — a 400's real reason (an API's
    'filter is not valid') is otherwise lost. Bounded to keep an error compact."""
    if response.is_success:
        return
    body = (response.text or "")[:ERROR_BODY_CAP]
    raise httpx.HTTPStatusError(
        f"{response.status_code} {response.reason_phrase} for "
        f"{response.request.method} {response.request.url}: {body}",
        request=response.request,
        response=response,
    )


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        return {}
    data: dict[str, Any] = response.json()
    return data


def _response_list(response: httpx.Response) -> list[dict[str, Any]]:
    if response.status_code == 204 or not response.content:
        return []
    return list_or_empty(response.json())


def _bound_pages(who: str, pages: int) -> None:
    if pages > MAX_PAGES:
        raise RuntimeError(f"{who}: pagination exceeded {MAX_PAGES} pages without terminating")


def _bound_cursor(who: str, token: str, seen: set[str]) -> None:
    if token in seen:
        raise RuntimeError(f"{who}: pagination cursor {token!r} repeated; provider not advancing")
    seen.add(token)


class RestConnector(Connector):
    """Base for REST-API connectors. See the module docstring."""

    base_url: ClassVar[str] = ""
    streams_list: ClassVar[list[StreamSpec]] = []

    def streams(self) -> list[StreamSpec]:
        return list(self.streams_list)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        timeout = httpx.Timeout(TIMEOUT_CONNECT_SECONDS, read=TIMEOUT_READ_SECONDS)
        base = base_url.rstrip("/")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential.transport is not None:
            return httpx.AsyncClient(
                base_url=base, transport=credential.transport, timeout=timeout, headers=headers
            )
        if credential.bearer is not None:
            headers["Authorization"] = f"Bearer {credential.bearer}"
        elif not credential.headers:
            raise RuntimeError(f"{type(self).__name__}: credential carries no auth")
        headers.update(credential.headers)
        return httpx.AsyncClient(base_url=base, timeout=timeout, headers=headers)

    async def _get(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _json_or_empty(await self._get_raw(client, path, params=params))

    async def _get_raw(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """httpx replaces a URL's query with the `params` it is handed, so a path's own query is
        merged here."""
        address, _, query = path.partition("?")
        if not query:
            return await self._send(lambda: client.get(path, params=params))
        declared = [pair for pair in parse_qsl(query) if pair[0] not in (params or {})]
        merged = [*declared, *(params or {}).items()]
        return await self._send(lambda: client.get(address, params=merged))

    async def _post(
        self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """A POST for a read endpoint a provider exposes only over POST (Notion's `/search`),
        retried on transient/5xx like the GET path — still a read, the write path stays absent."""
        return _json_or_empty(await self._send(lambda: client.post(path, json=json)))

    async def _post_raw(
        self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Google Ads' `googleAds:searchStream` answers a top-level array of result batches."""
        return await self._send(lambda: client.post(path, json=json))

    def rate_limited(self, error: httpx.HTTPStatusError) -> bool:
        """Whether a status error is the provider's rate limit, which yields or retries as a 429
        does. Override for a provider that names one on another status (Google's quota `403`)."""
        return error.response.status_code == 429

    async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        """Send one read request. A rate limit yields to the durable source scheduler when the
        active walk has a safe checkpoint; protected walks retry with transport and 5xx faults."""
        delay = RETRY_INITIAL_DELAY_SECONDS
        waited = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await request()
                _raise_for_status(response)
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                limited = isinstance(error, httpx.HTTPStatusError) and self.rate_limited(error)
                if limited and _RATE_LIMIT_YIELDS.get():
                    retry_after = _retry_after(error)
                    wait = RATE_LIMIT_DEFAULT_SECONDS if retry_after is None else retry_after
                    raise ProviderRateLimited(
                        min(max(wait, RETRY_INITIAL_DELAY_SECONDS), RATE_LIMIT_MAX_SECONDS)
                    ) from error
                if attempt >= MAX_ATTEMPTS or not (limited or _is_retryable(error)):
                    raise
                wait = _retry_wait(error, delay)
                if waited + wait > RETRY_BUDGET_SECONDS:
                    raise
                await asyncio.sleep(wait)
                waited += wait
                delay *= 2
        raise AssertionError("unreachable")

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: ParentPages = no_parents,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        url = (base_url or self.base_url) or ""
        if not url:
            raise RuntimeError(f"{type(self).__name__}: no base_url available")
        rate_limit_token = _RATE_LIMIT_YIELDS.set(yield_rate_limits)
        try:
            async with self._make_client(url, credential) as client:
                source = self.paginate(
                    client,
                    stream,
                    Run(
                        cursor=cursor,
                        parents=parents,
                        self_user_id=self_user_id,
                        backfill_after=backfill_after,
                    ),
                )
                try:
                    async for page in source:
                        if not page:
                            continue
                        native = page if isinstance(page, StreamPage) else None
                        records = page.records if isinstance(page, StreamPage) else page
                        self._validate_page(records, stream)
                        records = [self.flatten(record, stream) for record in records]
                        if native is not None:
                            yield StreamPage(
                                records=records,
                                deletes=native.deletes,
                                next_cursor=native.next_cursor,
                                scope=native.scope,
                            )
                        else:
                            yield records
                finally:
                    if isinstance(source, AsyncGenerator):
                        await source.aclose()
        finally:
            _RATE_LIMIT_YIELDS.reset(rate_limit_token)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """Yield raw record-pages for one stream of one run. The default routes a stream whose
        `pagination` declares a non-`none` strategy through `paginate_from_strategy`; a stream that
        needs a bespoke shape leaves `pagination` unset and the connector overrides this method.

        The whole run crosses in one object, so a connector reads the values its streams need and
        names none of the rest."""
        pagination = stream.pagination
        if pagination is None or pagination.strategy is PaginationStrategy.none:
            raise NotImplementedError(
                f"{type(self).__name__}.paginate not implemented for stream {stream.name!r}"
            )
        async for page in self.paginate_from_strategy(stream, client=client):
            yield page

    async def paginate_from_strategy(
        self, stream: StreamSpec, *, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Run a stream's declared `Pagination`. One shared loop per strategy; per-stream variation
        is the `Pagination` instance. Records are read at `record_path`, yielded as `list[dict]`."""
        spec = stream.pagination
        if spec is None or spec.strategy is PaginationStrategy.none:
            return
        path = spec.path or self._strategy_path(stream)
        if spec.strategy is PaginationStrategy.next_cursor:
            if not spec.record_path or not spec.cursor_path or not spec.cursor_param:
                raise ValueError(
                    f"{type(self).__name__}.{stream.name}: next_cursor needs "
                    "record_path, cursor_path, cursor_param"
                )
            async for page in self._get_cursor_pages(
                client,
                path,
                records_path=spec.record_path,
                next_cursor_path=spec.cursor_path,
                cursor_param=spec.cursor_param,
                page_size_param=spec.page_size_param,
                page_size=spec.page_size,
                params=dict(spec.extra_params or {}),
            ):
                yield page
            return
        if spec.strategy is PaginationStrategy.next_link:
            record_path = spec.record_path
            parse: Callable[[httpx.Response], list[dict[str, Any]]] | None = None
            if record_path is not None:

                def parse(response: httpx.Response) -> list[dict[str, Any]]:
                    body = response.json() if response.content else {}
                    return records_at(body, record_path)

            async for page in self._get_link_header_pages(
                client,
                path,
                params=dict(spec.extra_params or {}),
                page_size_param=spec.page_size_param,
                page_size=spec.page_size,
                parse_records=parse,
            ):
                yield page
            return
        if spec.strategy is PaginationStrategy.offset_limit:
            if not spec.offset_param or not spec.limit_param or spec.page_size is None:
                raise ValueError(
                    f"{type(self).__name__}.{stream.name}: offset_limit needs "
                    "offset_param, limit_param, page_size"
                )
            async for page in self._get_offset_pages(
                client,
                path,
                records_path=spec.record_path,
                limit=spec.page_size,
                limit_param=spec.limit_param,
                offset_param=spec.offset_param,
                params=dict(spec.extra_params or {}),
            ):
                yield page
            return

    async def _get_link_header_pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size_param: str | None = "per_page",
        page_size: int | None = None,
        parse_records: Callable[[httpx.Response], list[dict[str, Any]]] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """GET pages by following RFC 5988 `Link: rel=next` headers."""
        first_params = dict(params or {})
        if page_size_param and page_size is not None:
            first_params.setdefault(page_size_param, page_size)
        who = f"{type(self).__name__} {path}"
        seen: set[str] = set()
        pages = 0
        response = await self._get_raw(client, path, params=first_params)
        while True:
            pages += 1
            _bound_pages(who, pages)
            records = parse_records(response) if parse_records else _response_list(response)
            if records:
                yield records
            following = next_link(response.headers)
            if not following:
                return
            _bound_cursor(who, following, seen)
            response = await self._get_raw(client, following)

    async def _get_cursor_pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        records_path: str | None,
        next_cursor_path: str,
        params: dict[str, Any] | None = None,
        cursor_param: str = "cursor",
        page_size_param: str | None = "limit",
        page_size: int | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """GET pages whose response body carries the next cursor token."""
        who = f"{type(self).__name__} {path}"
        seen: set[str] = set()
        token: str | None = None
        pages = 0
        while True:
            pages += 1
            _bound_pages(who, pages)
            query = dict(params or {})
            if page_size_param and page_size is not None:
                query[page_size_param] = page_size
            if token:
                query[cursor_param] = token
            data = await self._get(client, path, params=query)
            records = records_at(data, records_path)
            if records:
                yield records
            token = get_path(data, next_cursor_path)
            if not isinstance(token, str) or not token:
                return
            _bound_cursor(who, token, seen)

    async def _get_odata_pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """The OData `@odata.nextLink` already encodes the continuation query, so only the first
        request carries params."""
        who = f"{type(self).__name__} {path}"
        seen: set[str] = set()
        next_path: str | None = path
        query = params
        pages = 0
        while next_path:
            pages += 1
            _bound_pages(who, pages)
            response = await self._get_raw(client, next_path, params=query)
            data = response.json() if response.content else {}
            records = list_or_empty(data.get("value") if isinstance(data, dict) else [])
            if records:
                yield records
            next_path = data.get("@odata.nextLink") if isinstance(data, dict) else None
            if isinstance(next_path, str) and next_path:
                _bound_cursor(who, next_path, seen)
            query = None

    async def _get_offset_pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        records_path: str | None,
        limit: int,
        params: dict[str, Any] | None = None,
        limit_param: str = "limit",
        offset_param: str = "offset",
        more_path: str | None = None,
        response_limit_path: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        who = f"{type(self).__name__} {path}"
        offset = 0
        pages = 0
        while True:
            pages += 1
            _bound_pages(who, pages)
            query = dict(params or {})
            query.setdefault(limit_param, limit)
            query[offset_param] = offset
            data = await self._get(client, path, params=query)
            records = records_at(data, records_path)
            if not records:
                return
            yield records
            if more_path is not None:
                if not get_path(data, more_path):
                    return
            elif len(records) < limit:
                return
            if response_limit_path:
                step = _int_or_none(get_path(data, response_limit_path))
                offset += step or len(records) or limit
            else:
                offset += limit

    async def _get_page_number_pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        records_path: str | None,
        page_size: int,
        params: dict[str, Any] | None = None,
        page_param: str = "page",
        page_size_param: str | None = "limit",
        start_page: int = 1,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """GET page-numbered collections from an envelope list path."""
        who = f"{type(self).__name__} {path}"
        page = start_page
        pages = 0
        while True:
            pages += 1
            _bound_pages(who, pages)
            query = dict(params or {})
            query[page_param] = page
            if page_size_param:
                query.setdefault(page_size_param, page_size)
            data = await self._get(client, path, params=query)
            records = records_at(data, records_path)
            if records:
                yield records
            if len(records) < page_size:
                return
            page += 1

    def _strategy_path(self, stream: StreamSpec) -> str:
        """Resolve a strategy stream's request path when `Pagination.path` is unset. Default raises;
        connectors with a per-stream path table override."""
        raise NotImplementedError(
            f"{type(self).__name__}.{stream.name}: Pagination.path is unset and no "
            "_strategy_path override is in place"
        )

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Lift a record into the flat dict the sync writes. Default passthrough; override for
        payloads that nest fields under an envelope."""
        return record

    def _validate_page(self, page: Any, stream: StreamSpec) -> None:
        if not isinstance(page, list):
            raise TypeError(
                f"{type(self).__name__}.{stream.name}: paginate yielded "
                f"{type(page).__name__}, expected list[dict]"
            )
        bad = next((type(record).__name__ for record in page if not isinstance(record, dict)), None)
        if bad is not None:
            raise TypeError(
                f"{type(self).__name__}.{stream.name}: paginate yielded a page containing "
                f"{bad}, expected dict records"
            )
