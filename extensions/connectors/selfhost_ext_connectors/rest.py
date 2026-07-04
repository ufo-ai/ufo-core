"""RestConnector: the shared read scaffolding every REST-API connector reuses.

Owns the boilerplate a provider would otherwise duplicate: the httpx client (a direct bearer client
for a real token, a Composio-proxied client for the `composio-proxy:` sentinel a source backend
hands it), exponential-backoff retry on transient/5xx responses, and one page loop per declared
`PaginationStrategy`. Async — a connector runs from the core sync driver where a blocking network
call would stall every other surface.

A subclass sets `name`, `base_url`, `streams_list`, and either declares `Pagination` on each stream
(routing through `paginate_from_strategy`) or overrides `paginate` for provider-specific shapes
(GitHub's org/repo fan-out). Inside an override, await `self._get` / `self._get_raw` so retry stays
uniform. `flatten` is a passthrough; override for payloads that nest under an envelope. The write
path (CRUD, field discovery) is deliberately absent — the source seam only reads."""

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, ClassVar

import httpx

from selfhost_ext_connectors import composio, composio_proxy
from selfhost_ext_connectors.connector import (
    Connector,
    PaginationStrategy,
    StreamPage,
    StreamSpec,
)

MAX_ATTEMPTS = 5
RETRY_INITIAL_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 30.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
ERROR_BODY_CAP = 800

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


def records_at(data: Any, path: str | None) -> list[dict[str, Any]]:
    if path is None:
        return list_or_empty(data)
    if not isinstance(data, Mapping):
        return []
    return list_or_empty(get_path(data, path, []))


def next_link(headers: httpx.Headers) -> str | None:
    link = headers.get("link")
    if not link:
        return None
    match = _LINK_NEXT_RE.search(link)
    return match.group(1) if match else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS
    return False


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


class RestConnector(Connector):
    """Base for REST-API connectors. See the module docstring."""

    base_url: ClassVar[str] = ""
    streams_list: ClassVar[list[StreamSpec]] = []

    def streams(self) -> list[StreamSpec]:
        return list(self.streams_list)

    def _make_client(self, base_url: str, access_token: str) -> httpx.AsyncClient:
        """The HTTP client for one account. A `composio-proxy:` sentinel builds a Composio-proxied
        client (the broker injects the credential server-side, so the source holds no token); a real
        token builds a direct bearer client (tests, any non-brokered path)."""
        if access_token.startswith(composio_proxy.PROXY_TOKEN_PREFIX):
            return composio_proxy.proxied_client(
                client=composio.composio_client(),
                connected_account_id=composio_proxy.account_id_from_proxy_token(access_token),
                base_url=base_url,
                timeout=TIMEOUT_READ_SECONDS,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        return httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(TIMEOUT_CONNECT_SECONDS, read=TIMEOUT_READ_SECONDS),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    async def _get(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _json_or_empty(await self._get_raw(client, path, params=params))

    async def _get_raw(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """A GET returning the raw response for header-driven pagers, retried on transient/5xx."""
        delay = RETRY_INITIAL_DELAY_SECONDS
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.get(path, params=params)
                _raise_for_status(response)
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if attempt >= MAX_ATTEMPTS or not _is_retryable(error):
                    raise
                await asyncio.sleep(min(delay, RETRY_MAX_DELAY_SECONDS))
                delay *= 2
        raise AssertionError("unreachable")

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        access_token: str,
        base_url: str,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        url = (base_url or self.base_url) or ""
        if not url:
            raise RuntimeError(f"{type(self).__name__}: no base_url available")
        async with self._make_client(url, access_token) as client:
            async for page in self.paginate(client, stream, cursor=cursor):
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
        async for page in self.paginate_from_strategy(stream, client=client, cursor=cursor):
            yield page

    async def paginate_from_strategy(
        self, stream: StreamSpec, *, client: httpx.AsyncClient, cursor: str | None
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
        if spec.strategy is PaginationStrategy.page_number:
            if not spec.cursor_param or spec.page_size is None:
                raise ValueError(
                    f"{type(self).__name__}.{stream.name}: page_number needs cursor_param+page_size"
                )
            async for page in self._get_page_number_pages(
                client,
                path,
                records_path=spec.record_path,
                page_size=spec.page_size,
                page_param=spec.cursor_param,
                page_size_param=spec.page_size_param,
                params=dict(spec.extra_params or {}),
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
        if spec.strategy is PaginationStrategy.time_window:
            if not spec.cursor_param:
                raise ValueError(
                    f"{type(self).__name__}.{stream.name}: time_window needs cursor_param"
                )
            params = dict(spec.extra_params or {})
            if cursor:
                params[spec.cursor_param] = cursor
            if spec.page_size_param and spec.page_size is not None:
                params.setdefault(spec.page_size_param, spec.page_size)
            data = await self._get(client, path, params=params)
            records = records_at(data, spec.record_path)
            if records:
                yield records
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
        response = await self._get_raw(client, path, params=first_params)
        while True:
            records = parse_records(response) if parse_records else _response_list(response)
            if records:
                yield records
            following = next_link(response.headers)
            if not following:
                return
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
        token: str | None = None
        while True:
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
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """GET offset/limit pages from an envelope list path."""
        offset = 0
        while True:
            query = dict(params or {})
            query.setdefault(limit_param, limit)
            query[offset_param] = offset
            data = await self._get(client, path, params=query)
            records = records_at(data, records_path)
            if not records:
                return
            yield records
            if len(records) < limit:
                return
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
        page = start_page
        while True:
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
