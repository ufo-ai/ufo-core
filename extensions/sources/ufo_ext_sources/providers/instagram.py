"""The Instagram connector — Facebook Pages, their linked Instagram business accounts, media,
stories, and the insights on each synced into recallable pages, over the Facebook Graph API.

The account a collection hangs under is the grant's own: `paginate` walks `/me/accounts` and lifts
each Page's linked `instagram_business_account`, which is this connection's tenant identity rather
than a parent record. Media, stories and user insights read under it. Graph collections page by a
`data` array plus an absolute `paging.next` URL (`_paged`). Media and stories are incremental — each
page is filtered past the stored watermark on `timestamp`; user insights advance over `end_time`.

`media_insights` and `story_insights` are the two real edges, and a metric name addresses one
insight inside one object. A per-object read the account can't serve (HTTP 400/403/404) drops that
object from the pass and the rest still land; a refusal at the account walk (HTTP 401/403) raises
`StreamSkipped` so the run records a skip, not a failure. The credential is resolved through the
auth proxy the runner threads — this connector holds no token. The write path (media publish) is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    ParentEdge,
    Partition,
    PartitionBound,
    PartitionSkipped,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
    list_or_empty,
    records_at,
    with_context,
)
from ufo_ext_sources.watermark import text_checkpoint

GRAPH_VERSION = "v25.0"
_REFUSAL_STATUS = frozenset({401, 403})
_OBJECT_REFUSAL_STATUS = frozenset({400, 403, 404})
INSIGHT_METRICS = {
    "media_insights": "impressions,reach,engagement,saved,video_views",
    "story_insights": "impressions,reach,replies,taps_forward,taps_back,exits",
}

PAGES = StreamSpec(name="pages", source_object="accounts", primary_key="id")
INSTAGRAM_ACCOUNTS = StreamSpec(
    name="instagram_accounts",
    source_object="instagram_business_account",
    primary_key="id",
)
MEDIA = StreamSpec(
    name="media",
    source_object="media",
    primary_key="id",
    cursor_field="timestamp",
    created_at_field="timestamp",
    updated_at_field=None,
    canonical=True,
)
MEDIA_INSIGHTS = StreamSpec(
    name="media_insights",
    source_object="insights",
    primary_key="name",
    parents=(ParentEdge(stream="media", path="/{id}/insights"),),
)
STORIES = StreamSpec(
    name="stories",
    source_object="stories",
    primary_key="id",
    cursor_field="timestamp",
    created_at_field="timestamp",
    updated_at_field=None,
    canonical=True,
)
STORY_INSIGHTS = StreamSpec(
    name="story_insights",
    source_object="insights",
    primary_key="name",
    parents=(ParentEdge(stream="stories", path="/{id}/insights"),),
)
USER_INSIGHTS = StreamSpec(
    name="user_insights",
    source_object="user_insights",
    primary_key="id",
    cursor_field="end_time",
    created_at_field="end_time",
    updated_at_field=None,
)

ALL_STREAMS = [
    PAGES,
    INSTAGRAM_ACCOUNTS,
    MEDIA,
    MEDIA_INSIGHTS,
    STORIES,
    STORY_INSIGHTS,
    USER_INSIGHTS,
]


class InstagramConnector(RestConnector):
    name = "instagram"
    base_url = f"https://graph.facebook.com/{GRAPH_VERSION}"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def _paged(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        next_path: str | None = path
        query = params
        while next_path:
            response = await self._get_raw(client, next_path, params=query)
            data = response.json() if response.content else {}
            records = records_at(data, "data") if isinstance(data, dict) else []
            if records:
                yield records
            paging = data.get("paging") if isinstance(data, dict) else {}
            next_path = paging.get("next") if isinstance(paging, dict) else None
            query = None

    async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        fields = "id,name,instagram_business_account{id,username,name,profile_picture_url}"
        out: list[dict[str, Any]] = []
        async for page in self._paged(client, "/me/accounts", params={"fields": fields}):
            out.extend(page)
        return out

    async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for page in await self._pages(client):
            account = page.get("instagram_business_account")
            if isinstance(account, dict) and account.get("id"):
                out[str(account["id"])] = {
                    **account,
                    "page_id": page.get("id"),
                    "page_name": page.get("name"),
                }
        return list(out.values())

    async def _account_collection(
        self,
        client: httpx.AsyncClient,
        path_suffix: str,
        *,
        fields: str,
        cursor: str | None,
        cursor_field: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for account in await self._instagram_accounts(client):
            account_id = account.get("id")
            if not isinstance(account_id, str) or not account_id:
                continue
            async for page in self._paged(
                client,
                f"/{account_id}/{path_suffix}",
                params={"fields": fields, "limit": 100},
            ):
                if cursor and cursor_field:
                    page = [r for r in page if str(r.get(cursor_field) or "") > cursor]
                if page:
                    yield with_context(page, instagram_account_id=account_id)

    async def _user_insights(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for account in await self._instagram_accounts(client):
            account_id = account.get("id")
            if not isinstance(account_id, str) or not account_id:
                continue
            data = await self._get(
                client,
                f"/{account_id}/insights",
                params={"metric": "impressions,reach,profile_views", "period": "day"},
            )
            rows: list[dict[str, Any]] = []
            for insight in records_at(data, "data"):
                values = list_or_empty(insight.get("values"))
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    end_time = value.get("end_time")
                    if cursor and str(end_time or "") <= cursor:
                        continue
                    rows.append(
                        {
                            **value,
                            "id": f"{account_id}:{insight.get('name')}:{end_time}",
                            "name": insight.get("name"),
                            "instagram_account_id": account_id,
                        }
                    )
            if rows:
                yield rows

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            async for page in self._stream_pages(client, stream, run):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"instagram: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks scope or the token is invalid"
                ) from error
            raise

    def _stream_pages(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        name = stream.name
        if stream.parents:
            pages = partial(self._object_insights, client, INSIGHT_METRICS[name])
            return fanned_out(stream, run, pages)
        if name in {"pages", "instagram_accounts"}:
            return self._root_pages(client, name)
        if name == "media":
            return self._account_collection(
                client,
                "media",
                fields=(
                    "id,caption,media_type,media_url,permalink,timestamp,username,"
                    "like_count,comments_count"
                ),
                cursor=run.cursor,
                cursor_field="timestamp",
            )
        if name == "stories":
            return self._account_collection(
                client,
                "stories",
                fields="id,caption,media_type,media_url,permalink,timestamp,username",
                cursor=run.cursor,
                cursor_field="timestamp",
            )
        if name == "user_insights":
            return self._user_insights(client, cursor=run.cursor)
        raise StreamSkipped(f"instagram stream {name!r} is not implemented")

    async def _root_pages(
        self, client: httpx.AsyncClient, name: str
    ) -> AsyncIterator[list[dict[str, Any]]]:
        records = await (
            self._pages(client) if name == "pages" else self._instagram_accounts(client)
        )
        if records:
            yield records

    async def _object_insights(
        self,
        client: httpx.AsyncClient,
        metrics: str,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One object's insights. Graph refuses a metric an account's plan or a media type does not
        carry with a `400`, which is about that object and not the stream, so it drops out of the
        pass and every other object still lands."""
        try:
            data = await self._get(client, partition.path, params={"metric": metrics})
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _OBJECT_REFUSAL_STATUS:
                raise PartitionSkipped(f"instagram: {partition.ref} refused") from error
            raise
        yield WalkPage(records=records_at(data, "data"))
