"""The disclosure atom a connection's pages are stamped with."""

from uuid import uuid4

import pytest

from ufo.runtime.turns.subjects import (
    SHARED_SUBJECT,
    connection_subject,
    member_subject,
)


def test_a_shared_connection_discloses_to_the_workspace_whoever_owns_it() -> None:
    """`shared` is the whole of the rule: an owned connection its member shared and the workspace's
    own ownerless one land on the same atom, because a page carries who may read it and neither
    answers that with an owner."""
    owner_id = uuid4()

    assert connection_subject(True, owner_id) == SHARED_SUBJECT
    assert connection_subject(True, None) == SHARED_SUBJECT


def test_a_private_connection_discloses_to_its_owner_alone() -> None:
    owner_id = uuid4()

    assert connection_subject(False, owner_id) == member_subject(owner_id)
    assert connection_subject(False, owner_id) == f"member:{owner_id}"


def test_a_private_connection_with_no_owner_is_refused_rather_than_silently_shared() -> None:
    """The pair cannot be both — the schema's `connection_shared` check makes an ownerless
    connection shared — so reaching here means a row broke that check. Answering `shared` would
    disclose to the workspace content nobody said to; answering an empty member atom would stamp
    pages nothing could read. It raises instead."""
    with pytest.raises(RuntimeError, match="discloses nothing"):
        connection_subject(False, None)
