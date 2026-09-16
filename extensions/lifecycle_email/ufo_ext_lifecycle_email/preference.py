"""What a member asked to stop hearing, expressed the way every member action is: in chat.

There is no unsubscribe endpoint of our own. A member says it to the agent, the agent calls this,
and the preference is recorded where suppression is already applied — control — so one place
decides what goes out. The topic a member may silence is product news; a notice about what their
workspace is doing with their money is not theirs to silence, and asking for that is refused with
the reason rather than quietly doing nothing."""

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.email import PRODUCT_NEWS, EmailRefused, EmailUnanswered
from ufo.sdk.seats import Seats
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)

MEMBER_KIND = "member"

PRODUCT_EMAIL_GATE = "a member changes their own product email"

UNRESOLVED = "You are not a member of this workspace, so there is nothing of yours to change."
SILENCED = "Product email is off. Notices about this workspace's credit and access still arrive."
RESUMED = "Product email is on."
UNAVAILABLE = "This deploy sends no email, so there is nothing to change."
UNREACHABLE = "The preference was not recorded. Ask again."


def _answer(said: str, *, is_error: bool = False) -> ToolResult:
    return ToolResult(content=(TextContent(text=said),), is_error=is_error)


class ProductEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receiving: bool = Field(
        description=(
            "True to receive product email again, false to stop receiving it. Notices about the "
            "workspace's credit and access are unaffected either way."
        )
    )


async def product_email_handler(ctx: ToolContext, args: ProductEmailInput) -> ToolResult:
    if ctx.ext is None or ctx.ext.email is None:
        return _answer(UNAVAILABLE, is_error=True)
    speaker = ctx.require_speaker(PRODUCT_EMAIL_GATE)
    async with ctx.ext.transaction() as connection:
        address = next(
            (
                entry.email
                for entry in (await Seats(ctx.ext.workspace_id).snapshot(connection)).members
                if entry.id == speaker
            ),
            None,
        )
    if address is None:
        return _answer(UNRESOLVED, is_error=True)
    try:
        await ctx.ext.email.silence(address, PRODUCT_NEWS, silenced=not args.receiving)
    except (EmailRefused, EmailUnanswered):
        return _answer(UNREACHABLE, is_error=True)
    return _answer(RESUMED if args.receiving else SILENCED)


PRODUCT_EMAIL_TOOL = ToolDef(
    name="set_product_email",
    description=(
        "Turn the speaker's product email on or off, when they ask to stop or resume hearing "
        "from us by email. It changes product email alone: a notice about the workspace running "
        "out of credit, or about access, still reaches them, and this cannot turn those off. It "
        "changes the speaker's own preference and nobody else's."
    ),
    input_model=ProductEmailInput,
    handler=product_email_handler,
    side_effecting=True,
    bound=ObjectBinding(kind=MEMBER_KIND, binding="collection"),
    presentation=ActionPresentation(
        label="Set product email",
        confirm="Product email changes for you alone.",
    ),
)
