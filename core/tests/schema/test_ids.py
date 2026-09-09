from uuid import UUID

from ufo.schema.ids import uuid7


def test_uuid7_is_version_seven_and_time_ordered() -> None:
    minted = [uuid7() for _ in range(5000)]
    assert all(isinstance(u, UUID) and u.version == 7 for u in minted)
    assert all(u.variant == "specified in RFC 4122" for u in minted)
    assert minted == sorted(minted)
    assert len(set(minted)) == len(minted)
