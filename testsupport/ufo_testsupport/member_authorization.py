from dataclasses import dataclass
from uuid import UUID

from ufo.runtime.access.member_authorization import (
    AuthorizationAttempt,
    AuthorizationRequest,
    AuthorizationResolution,
)


@dataclass(frozen=True)
class PermitMemberAuthorization:
    async def preflight(self, request: AuthorizationRequest) -> AuthorizationAttempt:
        return AuthorizationAttempt(request=request)

    async def authorize(
        self, request: AuthorizationRequest, attempt: AuthorizationAttempt | None = None
    ) -> AuthorizationResolution:
        return AuthorizationResolution("allow")

    async def has_pending(
        self, workspace_id: UUID, conversation_id: UUID, member_ids: frozenset[UUID]
    ) -> bool:
        return False
