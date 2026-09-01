from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ufo.runtime.authority import (
    WORKSPACE_AUTHORITY,
    ExecutionAuthority,
    MemberAuthority,
    authority_from_member_id,
    authority_member_id,
    turn_authority,
)
from ufo.schema.records import Turn


def test_authority_has_one_exact_runtime_shape_and_nullable_storage_encoding() -> None:
    member_id = uuid4()
    member = MemberAuthority(member_id)
    assert authority_from_member_id(member_id) == member
    assert authority_member_id(member) == member_id
    assert authority_from_member_id(None) is WORKSPACE_AUTHORITY
    assert authority_member_id(WORKSPACE_AUTHORITY) is None
    assert turn_authority(member_id, None) == member
    assert turn_authority(None, member_id) == member
    with pytest.raises(ValueError, match="cannot carry both"):
        turn_authority(member_id, uuid4())
    with pytest.raises(TypeError, match="MemberAuthority or WorkspaceAuthority"):
        authority_member_id(cast(ExecutionAuthority, member_id))


def test_turn_rejects_two_authorities_at_its_durable_boundary() -> None:
    with pytest.raises(ValidationError, match="cannot carry both"):
        Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=0,
            status="queued",
            inbound="work",
            speaker_member_id=uuid4(),
            on_behalf_of_member_id=uuid4(),
            created_at=datetime.now(UTC),
        )
