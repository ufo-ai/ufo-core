"""The provider-facing OAuth callback the shared fleet mounts."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ufo.grants import (
    ConnectStateInvalid,
    ConnectUnavailable,
    UnknownProvider,
    installed_connect_flow,
)

CONNECT_CALLBACK_PATH = "/v1/connect/callback"

callback_router = APIRouter(prefix="/v1")


@callback_router.get("/connect/callback")
async def connect_callback(state: str = "", code: str = "") -> PlainTextResponse:
    """Complete the OAuth handoff the provider redirects to: verify the sealed state, exchange the
    code for the broker-owned account, and land the grant. The grant a turn's `connect_account`
    began lands here. State-verified, not bearer-authenticated — the browser carries only the state
    the connect tool sealed with the speaking member, agent, and conversation."""
    try:
        flow = installed_connect_flow()
    except ConnectUnavailable as error:
        raise HTTPException(503, str(error)) from error
    if not state or not code:
        raise HTTPException(400, "missing state or code")
    try:
        recorded = await flow.complete(state=state, code=code)
    except ConnectStateInvalid as error:
        raise HTTPException(400, str(error)) from error
    except UnknownProvider:
        raise HTTPException(404, "connector provider is not installed") from None
    return PlainTextResponse(
        f"connected {recorded.provider} account {recorded.account_id}; "
        "return to chat and ask me to continue"
    )
