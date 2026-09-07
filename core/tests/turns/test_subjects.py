"""The disclosure atom a connection's pages are stamped with."""

from uuid import uuid4

import pytest

from ufo.runtime.turns.subjects import (
    SHARED_SUBJECT,
    connection_subject,
    member_subject,
    subject_shared,
)


def test_a_shared_connection_discloses_to_the_workspace_whoever_owns_it() -> None:
    """`shared` is the whole of the rule: an owned connection its member shared and the workspace's
    own ownerless one land on the same atom, because a page carries who may read it and neither
    answers that with an owner."""
    owner_id = uuid4()

    assert connection_subject(True, owner_id) == SHARED_SUBJECT
    assert connection_subject(True, None) == SHARED_SUBJECT
    assert subject_shared(connection_subject(True, owner_id))


def test_a_private_connection_discloses_to_its_owner_alone() -> None:
    owner_id = uuid4()

    assert connection_subject(False, owner_id) == member_subject(owner_id)
    assert connection_subject(False, owner_id) == f"member:{owner_id}"
    assert not subject_shared(connection_subject(False, owner_id))


def test_a_private_connection_with_no_owner_is_refused_rather_than_silently_shared() -> None:
    """The pair cannot be both — the schema's `connection_shared` check makes an ownerless
    connection shared — so reaching here means a row broke that check. Answering `shared` would
    disclose to the workspace content nobody said to; answering an empty member atom would stamp
    pages nothing could read. It raises instead."""
    with pytest.raises(RuntimeError, match="discloses nothing"):
        connection_subject(False, None)


def test_only_a_member_atom_is_a_subject_a_workspace_member_reads_by_ownership() -> None:
    """`subject_shared` is what separates the atom every member reads from the ones a membership
    fact has to answer. A room and a sealed foreign room are false for the same reason a member atom
    is: no membership fact reaches either from `shared` alone."""
    assert subject_shared(SHARED_SUBJECT)
    assert not subject_shared(member_subject(uuid4()))
    assert not subject_shared("room:slack:C123")
    assert not subject_shared("foreign:slack:C999")
