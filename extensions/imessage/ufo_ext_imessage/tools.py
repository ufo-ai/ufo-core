import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from ufo.sdk.surfaces import SurfaceInstallationConflict
from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_imessage.provider import MessageProvider, ProviderNotConfigured
from ufo_ext_imessage.surface import SURFACE_IMESSAGE, PendingClaim, phone_key

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
PROJECT_KEY = "project"
PHONE_CLAIM_TTL = timedelta(minutes=10)
CONFIRMATION_TEXT = (
    "Reply to connect this number to your ufo account. If you did not request this, do not reply."
)


class ImessageConnectInput(BaseModel):
    phone_number: str = Field(
        description="The member's iMessage phone number in E.164 form, such as +14155550123."
    )
    user_description: str = Field(
        description="That you are connecting their phone to iMessage, in plain language."
    )

    @field_validator("phone_number")
    @classmethod
    def _e164(cls, value: str) -> str:
        phone = "".join(value.split())
        if E164_PATTERN.fullmatch(phone) is None:
            raise ValueError("phone_number must use E.164 form")
        return phone


def _result(state: str, instruction: str, **extra: object) -> ToolResult:
    return ToolResult(
        content=(
            TextContent(text=json.dumps({"state": state, "instruction": instruction, **extra})),
        ),
        untrusted=True,
    )


@dataclass(frozen=True)
class ImessageConnect:
    provider: Callable[[], MessageProvider]

    async def run(self, ctx: ToolContext, args: ImessageConnectInput) -> ToolResult:
        """Bind this provider installation and stage one member identity claim."""
        assert ctx.ext is not None
        if ctx.speaker_member_id is None:
            return _result(
                "not_connected", "A signed-in workspace member must request this connection."
            )
        try:
            provider = self.provider()
        except ProviderNotConfigured:
            return _result("not_connected", "This deploy has no iMessage provider credentials.")
        configured = await ctx.ext.store.get(PROJECT_KEY)
        if configured is None:
            if not await ctx.speaker_is_admin():
                return _result(
                    "not_connected", "Ask a workspace admin to connect the iMessage provider."
                )
            try:
                await ctx.ext.installations.bind(SURFACE_IMESSAGE, provider.installation_id)
            except SurfaceInstallationConflict:
                return _result(
                    "not_connected", "This iMessage provider is connected to another workspace."
                )
            await ctx.ext.store.put(PROJECT_KEY, provider.installation_id)
        elif configured != provider.installation_id:
            raise RuntimeError("The configured iMessage provider does not match this deploy")
        key = phone_key(args.phone_number)
        stored = await ctx.ext.store.get(key)
        now = datetime.now(UTC)
        if stored is not None:
            existing = PendingClaim.model_validate(stored)
            if existing.expires_at > now:
                if existing.member_id != ctx.speaker_member_id:
                    return _result(
                        "not_connected", "This phone number is claimed by another member."
                    )
                await provider.send_text(
                    existing.conversation_id,
                    CONFIRMATION_TEXT,
                    existing.confirmation_idempotency_key,
                )
                return _result(
                    "pending", "Check Messages and reply to the confirmation within 10 minutes."
                )
        confirmation_idempotency_key = f"imessage-confirmation:{ctx.idempotency_key or uuid4()}"
        user = await provider.register_phone(args.phone_number, confirmation_idempotency_key)
        claim = PendingClaim(
            member_id=ctx.speaker_member_id,
            phone_number=args.phone_number,
            conversation_id=user.conversation_id,
            confirmation_idempotency_key=confirmation_idempotency_key,
            expires_at=datetime.now(UTC) + PHONE_CLAIM_TTL,
        )
        landed = await ctx.ext.store.put_if(key, claim.model_dump(mode="json"), expected=stored)
        if not landed:
            return _result("not_connected", "The phone connection changed. Ask again.")
        await provider.send_text(
            user.conversation_id, CONFIRMATION_TEXT, confirmation_idempotency_key
        )
        return _result(
            "pending",
            "Check Messages and reply to the confirmation within 10 minutes.",
        )
