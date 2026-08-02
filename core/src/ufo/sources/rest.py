"""RestConnector: the shared read scaffolding every REST-API connector reuses.

Owns the boilerplate a provider would otherwise duplicate: the httpx client (built from whichever
`Credential` the auth-proxy resolved — a broker's proxying transport, a bearer token, or auth
headers), retry on transient/5xx responses, and one page loop per declared `PaginationStrategy`.
Async — a connector runs from the core sync driver where a blocking network call would stall every
other surface.

A subclass sets `name`, `base_url`, `streams_list`, and either declares `Pagination` on each stream
(routing through `paginate_from_strategy`) or overrides `paginate` for provider-specific shapes
(GitHub's org/repo fan-out). Inside an override, await `self._get` / `self._get_raw` so retry stays
uniform. `flatten` is a passthrough; override for payloads that nest under an envelope. The write
path (CRUD, field discovery) is deliberately absent — the source seam only reads."""

import asyncio
import math
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable, Mapping
from typing import Any, ClassVar

import httpx

from ufo.connectors import Credential
from ufo.sources.connector import (
    Connector,
    PaginationStrategy,
    StreamPage,
    StreamSpec,
)

MAX_ATTEMPTS = 5
RETRY_INITIAL_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 30.0
RETRY_AFTER_MAX_SECONDS = 60.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
ERROR_BODY_CAP = 800
MAX_PAGES = 10_000

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.IGNORECASE)


def get_path(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Read a dotted path from a nested mapping."""
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return default
        value = value.get(part)
        if value is None:
            return default
    return value


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


def _retry_wait(error: BaseException, delay: float) -> float:
    """A 429's `Retry-After` when it reads as a finite non-negative number of seconds, capped at
    `RETRY_AFTER_MAX_SECONDS`, else the caller's `delay` — a provider telling us exactly how long to
    back off beats our own guess, but the cap bounds each wait so a header naming a distant
    rate-limit reset cannot park one page fetch on it, and a `nan` would poison the shared loop's
    timer heap."""
    if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code != 429:
        return delay
    header = error.response.headers.get("retry-after")
    if header is None:
        return delay
    try:
        wait = float(header)
    except ValueError:
        return delay
    if not math.isfinite(wait) or wait < 0.0:
        return delay
    return min(wait, RETRY_AFTER_MAX_SECONDS)


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
    """Fail a pagination loop that runs past `MAX_PAGES` without terminating. A provider that never
    drops its next-page signal (a bug, or a bad/hostile response) would otherwise spin the fetch
    forever, holding the source's claim lease and never committing. Failing loud is fail-closed: the
    run records failed, commits no pages, tombstones nothing, and reschedules."""
    if pages > MAX_PAGES:
        raise RuntimeError(f"{who}: pagination exceeded {MAX_PAGES} pages without terminating")


def _bound_cursor(who: str, token: str, seen: set[str]) -> None:
    """Fail a token pager whose next cursor / next-link repeats one already fetched — the provider
    is not advancing, so following it re-fetches the same page endlessly. Catches a constant-token
    spin precisely, before the page cap does."""
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
        """The HTTP client for one account, built from the resolved `Credential`. A `transport`
        wraps a broker's proxy-execute (the secret stays server-side); a `bearer` or auth `headers`
        send the credential directly (BYOK/direct, read host-side). Exactly one path is populated;
        an empty credential fails loud rather than issue an unauthenticated request."""
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
        """A GET returning the raw response for header-driven pagers, retried on transient/5xx."""
        return await self._send(lambda: client.get(path, params=params))

    async def _post(
        self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """A POST for a read endpoint a provider exposes only over POST (Notion's `/search`),
        retried on transient/5xx like the GET path — still a read, the write path stays absent."""
        return _json_or_empty(await self._send(lambda: client.post(path, json=json)))

    async def _post_raw(
        self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        """A POST returning the raw response for a read whose body is a top-level array rather than
        an object (Google Ads' `googleAds:searchStream` yields a list of result batches), retried on
        transient/5xx like `_post` — still a read, the write path stays absent."""
        return await self._send(lambda: client.post(path, json=json))

    async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        """The shared retry envelope behind `_get_raw`/`_post`: run one request coroutine, retrying
        a transient/5xx response until `MAX_ATTEMPTS` attempts are spent — waiting a 429's
        `Retry-After` when it carries a usable one, bounded by `RETRY_AFTER_MAX_SECONDS`, otherwise
        a doubling delay."""
        delay = RETRY_INITIAL_DELAY_SECONDS
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await request()
                _raise_for_status(response)
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if attempt >= MAX_ATTEMPTS or not _is_retryable(error):
                    raise
                await asyncio.sleep(_retry_wait(error, min(delay, RETRY_MAX_DELAY_SECONDS)))
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
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        url = (base_url or self.base_url) or ""
        if not url:
            raise RuntimeError(f"{type(self).__name__}: no base_url available")
        async with self._make_client(url, credential) as client:
            source = self.paginate_source(client, stream, cursor=cursor, self_user_id=self_user_id)
            try:
                async for page in source:
                    if not page:
                        continue
                    if isinstance(page, StreamPage):
                        self._validate_page(page.records, stream)
                        yield StreamPage(
                            records=[self.flatten(record, stream) for record in page.records],
                            deletes=page.deletes,
                            next_cursor=page.next_cursor,
                        )
                        continue
                    self._validate_page(page, stream)
                    yield [self.flatten(record, stream) for record in page]
            finally:
                if isinstance(source, AsyncGenerator):
                    await source.aclose()

    def paginate_source(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
        self_user_id: str | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        return self.paginate(client, stream, cursor=cursor)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """Yield raw record-pages. The default routes a stream whose `pagination` declares a
        non-`none` strategy through `paginate_from_strategy`; a stream that needs a bespoke shape
        leaves `pagination` unset and the connector overrides this method."""
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
        """GET Microsoft Graph / OData pages: records live under `value` and continuation is the
        absolute `@odata.nextLink` URL. Only the first request carries caller params — the next-link
        already encodes the continuation query."""
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
        """GET offset/limit pages from an envelope list path. A provider whose envelope reports its
        own continuation drives the loop off that: `more_path` is a boolean 'is there another page'
        the server sets, `response_limit_path` the page size it actually applied (which the next
        offset advances by). Left unset, the loop stops on a short page and steps by `limit`."""
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
