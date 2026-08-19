import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from ufo.sdk.surfaces import SurfaceInstallationConflict
from ufo.sdk.tools import TextContent, ToolContext, ToolResult
from ufo_ext_imessage.provider import (
    MessageProvider,
    ProviderNotConfigured,
    TargetNotOptedIn,
)
from ufo_ext_imessage.surface import OPT_IN_TEXT, SURFACE_IMESSAGE, PendingClaim, phone_key

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
PHONE_CLAIM_TTL = timedelta(minutes=10)


def opt_in_link(assigned_phone_number: str, opt_in_code: str) -> str:
    """The deeplink that opens Messages with the opt-in text ready, so the member sends the first
    message a shared line needs before it can message that phone. `sms:` is the only scheme Apple
    documents, and the `?&body=` form is the one Photon's own redirect ships."""
    return f"sms:{assigned_phone_number}?&body={quote(f'{OPT_IN_TEXT} {opt_in_code}')}"


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


def _opt_in_result(assigned_phone_number: str, opt_in_code: str) -> ToolResult:
    opt_in_text = f"{OPT_IN_TEXT} {opt_in_code}"
    return _result(
        "pending",
        f'Text "{opt_in_text}" to {assigned_phone_number} from that phone within 10 minutes.',
        assigned_phone_number=assigned_phone_number,
        opt_in_text=opt_in_text,
        opt_in_link=opt_in_link(assigned_phone_number, opt_in_code),
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
        configured = await ctx.ext.installations.installation(SURFACE_IMESSAGE)
        if configured != provider.installation_id:
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
                return _opt_in_result(existing.assigned_phone_number, existing.opt_in_code)
        registration_idempotency_key = f"imessage-registration:{ctx.idempotency_key or uuid4()}"
        opt_in_code = uuid4().hex.upper()
        try:
            user = await provider.register_phone(args.phone_number, registration_idempotency_key)
        except TargetNotOptedIn as refusal:
            if not refusal.assigned_phone_number:
                raise
            conversation_id = None
            assigned_phone_number = refusal.assigned_phone_number
        else:
            conversation_id = user.conversation_id
            assigned_phone_number = user.assigned_phone_number
        claim = PendingClaim(
            member_id=ctx.speaker_member_id,
            phone_number=args.phone_number,
            conversation_id=conversation_id,
            assigned_phone_number=assigned_phone_number,
            opt_in_code=opt_in_code,
            expires_at=datetime.now(UTC) + PHONE_CLAIM_TTL,
        )
        landed = await ctx.ext.store.put_if(key, claim.model_dump(mode="json"), expected=stored)
        if not landed:
            return _result("not_connected", "The phone connection changed. Ask again.")
        return _opt_in_result(assigned_phone_number, opt_in_code)
