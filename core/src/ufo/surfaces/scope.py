"""The shared fleet's per-request workspace-binding release.

On the shared fleet one process serves every workspace, so each request binds its own workspace as
the ambient `current_workspace` before its handler reads anything, and must leave none bound when it
finishes — else a later request whose task reuses this one's context (cpython#140947: a request task
can inherit a prior task's contextvars) would start already scoped to another tenant. The surface
sets the binding before the read; `ScopedResponse` ends it after the whole response is sent.

A live surface returns a StreamingResponse whose hub tail reads the durable turn under RLS *after*
the handler returns, so the binding must outlive the handler — a `with ws(...)` block, which resets
when the handler returns, would cut that read off. The reset therefore rides the response and fires
in `__call__`, which the router awaits in the request's own task: Starlette streams the body in a
child task (ASGI spec_version < 2.4), a *copy* of this context — so the tail reads the right
workspace, but the reset must run in the request task where the binding was set, not that child
(whose context is discarded, leaving the request task's own still bound). The binding is the
outermost per-request scope, so release is to unbound, not a restored prior — clearing even a stale
value inherited from a leaked context, so no request ever leaves one behind.
"""

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from ufo.db import current_workspace


class ScopedResponse(Response):
    """Sends `inner`, then releases the request's ambient workspace binding — always, in the request
    task, once the whole response (a streamed body included) has been sent or torn down."""

    def __init__(self, inner: Response) -> None:
        self._inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self._inner(scope, receive, send)
        finally:
            current_workspace.set(None)
