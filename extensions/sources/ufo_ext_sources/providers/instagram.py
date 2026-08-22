"""The Instagram connector — Facebook Pages, their linked Instagram business accounts, media,
stories, and the insights on each synced into recallable pages, over the Facebook Graph API.

Every stream fans out from the grant's Pages: `paginate` walks `/me/accounts`, lifts each Page's
linked `instagram_business_account`, then reads that account's media/stories and their per-object
insights. Graph collections page by a `data` array plus an absolute `paging.next` URL
(`_paged`). Media and stories are incremental — each page is filtered past the stored watermark on
`timestamp`; user insights advance over `end_time`. A per-object insights read that the account
can't serve (HTTP 400/403/404) is skipped and the walk continues; a refusal at the account walk
(HTTP 401/403) raises `StreamSkipped` so the run records a skip, not a failure. The credential is
resolved through the auth proxy the runner threads — this connector holds no token. The write path
(media publish) is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
    records_at,
    with_context,
)

GRAPH_VERSION = "v25.0"
_REFUSAL_STATUS = frozenset({401, 403})

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
)
MEDIA_INSIGHTS = StreamSpec(
    name="media_insights",
    source_object="insights",
    primary_key="id",
    canonical=False,
)
STORIES = StreamSpec(
    name="stories",
    source_object="stories",
    primary_key="id",
    cursor_field="timestamp",
    created_at_field="timestamp",
    updated_at_field=None,
    canonical=False,
)
STORY_INSIGHTS = StreamSpec(
    name="story_insights",
    source_object="story_insights",
    primary_key="id",
    canonical=False,
)
USER_INSIGHTS = StreamSpec(
    name="user_insights",
    source_object="user_insights",
    primary_key="id",
    cursor_field="end_time",
    created_at_field="end_time",
    updated_at_field=None,
    canonical=False,
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

    async def _object_insights(
        self,
        client: httpx.AsyncClient,
        objects: AsyncIterator[list[dict[str, Any]]],
        *,
        metrics: str,
        stream_name: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in objects:
            out: list[dict[str, Any]] = []
            for obj in page:
                obj_id = obj.get("id")
                if not isinstance(obj_id, str) or not obj_id:
                    continue
                try:
                    data = await self._get(
                        client,
                        f"/{obj_id}/insights",
                        params={"metric": metrics},
                    )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in {400, 403, 404}:
                        continue
                    raise
                for insight in records_at(data, "data"):
                    out.append(
                        {
                            **insight,
                            "id": f"{obj_id}:{insight.get('name')}",
                            "parent_external_id": obj_id,
                            "stream_name": stream_name,
                        }
                    )
            if out:
                yield out

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
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "pages":
                pages = await self._pages(client)
                if pages:
                    yield pages
                return
            if stream.name == "instagram_accounts":
                accounts = await self._instagram_accounts(client)
                if accounts:
                    yield accounts
                return
            if stream.name == "media":
                async for page in self._account_collection(
                    client,
                    "media",
                    fields=(
                        "id,caption,media_type,media_url,permalink,timestamp,username,"
                        "like_count,comments_count"
                    ),
                    cursor=cursor,
                    cursor_field="timestamp",
                ):
                    yield page
                return
            if stream.name == "stories":
                async for page in self._account_collection(
                    client,
                    "stories",
                    fields="id,caption,media_type,media_url,permalink,timestamp,username",
                    cursor=cursor,
                    cursor_field="timestamp",
                ):
                    yield page
                return
            if stream.name == "media_insights":
                async for page in self._object_insights(
                    client,
                    self.paginate(
                        client,
                        next(s for s in self.streams_list if s.name == "media"),
                        cursor=None,
                    ),
                    metrics="impressions,reach,engagement,saved,video_views",
                    stream_name="media_insights",
                ):
                    yield page
                return
            if stream.name == "story_insights":
                async for page in self._object_insights(
                    client,
                    self.paginate(
                        client,
                        next(s for s in self.streams_list if s.name == "stories"),
                        cursor=None,
                    ),
                    metrics="impressions,reach,replies,taps_forward,taps_back,exits",
                    stream_name="story_insights",
                ):
                    yield page
                return
            if stream.name == "user_insights":
                async for page in self._user_insights(client, cursor=cursor):
                    yield page
                return
            raise StreamSkipped(f"instagram stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"instagram: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks scope or the token is invalid"
                ) from error
            raise
