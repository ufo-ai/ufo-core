"""The route ABI: a route handler receives a Request and returns a Response, built with one of the
concrete response classes re-exported here — so an extension serving routes never reaches past
`ufo.sdk` for its HTTP types. A live surface serves a page as an `HTMLResponse` and its hub
tail as a `StreamingResponse`."""

from starlette.requests import Request as Request
from starlette.responses import HTMLResponse as HTMLResponse
from starlette.responses import JSONResponse as JSONResponse
from starlette.responses import PlainTextResponse as PlainTextResponse
from starlette.responses import RedirectResponse as RedirectResponse
from starlette.responses import Response as Response
from starlette.responses import StreamingResponse as StreamingResponse
