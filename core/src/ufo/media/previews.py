from dataclasses import dataclass


@dataclass(frozen=True)
class StoredPreview:
    """A stored picture's workspace-relative blob key and exact byte size."""

    blob_key: str
    size_bytes: int
