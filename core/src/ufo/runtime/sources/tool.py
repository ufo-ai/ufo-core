"""ToolConnector: the read scaffolding for a provider whose only surface is broker-executed tools.

A provider that publishes no REST host cannot be synced by `RestConnector` — there is no host to
dial and no request for a broker's proxy transport to rewrite. An MCP-only service a broker fronts
(Granola) is read instead by executing its tools: the broker holds the account's token, runs each
call server-side, and answers with the payload. The auth proxy hands that seam over as
`Credential.execute`, so this base resolves it, fails loud when the resolved credential carries
none (a member's API key cannot execute a broker's tool), and hands it to `paginate`, which a
subclass writes in tool calls where a REST connector writes requests.

Everything else is the shared connector contract: a subclass sets `name` and `streams_list`, yields
`StreamPage`s with its own checkpoints, and overrides `render`. The connector dials nothing, so it
declares `dials_host` False and takes no `base_url`."""

from abc import abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
from typing import Any, ClassVar

from ufo.runtime.access.connectors import Credential, ToolExecutor
from ufo.runtime.sources.connector import Connector, StreamPage, StreamSpec


class ToolConnector(Connector):
    """Base for connectors that read through broker tool executions. See the module docstring."""

    dials_host: ClassVar[bool] = False
    streams_list: ClassVar[list[StreamSpec]] = []

    def streams(self) -> list[StreamSpec]:
        return list(self.streams_list)

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if credential.execute is None:
            raise RuntimeError(
                f"{type(self).__name__}: the resolved credential executes no broker tools, and "
                "this connector reads through nothing else — connect the account through its "
                "broker rather than setting a provider key"
            )
        pages = self.paginate(
            credential.execute, stream, cursor=cursor, backfill_after=backfill_after
        )
        try:
            async for page in pages:
                if page:
                    yield page
        finally:
            if isinstance(pages, AsyncGenerator):
                await pages.aclose()

    @abstractmethod
    def paginate(
        self,
        execute: ToolExecutor,
        stream: StreamSpec,
        *,
        cursor: str | None,
        backfill_after: datetime | None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """Async-yield one stream's records from `cursor`, reading through `execute`."""
