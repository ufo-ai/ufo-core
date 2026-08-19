"""The `gbrain_folder` backend: a serve-local directory of markdown files as pages."""

import asyncio
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.sandbox import contained_file, contained_root
from ufo.sdk.sources import SourceAuth, StreamFault, SyncResult
from ufo_ext_gbrain.pages import decoded, is_markdown_path, markdown_page

FOLDER_BACKEND = "gbrain_folder"
FOLDER_MAX_BYTES = 2 * 1024 * 1024 * 1024


class GbrainFolderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str = Field(pattern=r"^/", description="Absolute directory path on the serving host.")


@dataclass(frozen=True)
class GbrainFolderSource:
    """Reads a local directory's markdown files into pages: each file becomes one page keyed by
    its path relative to the root, titled from its frontmatter or first heading. A full scan each
    sync — the driver skips unchanged pages by digest and tombstones pages whose file is gone. The
    whole root going missing raises instead, so the sync fails closed and a transient mount blip
    can't sweep the index. The root arrives from chat, so every byte is read through the
    containment guard: a symlink is never followed, under the root or as the root."""

    max_bytes: int = FOLDER_MAX_BYTES
    config_model: ClassVar[type[GbrainFolderConfig]] = GbrainFolderConfig

    async def fetch(
        self, config: GbrainFolderConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        entries = await asyncio.to_thread(self._read, config.root, self.max_bytes)
        pages = tuple(markdown_page(ref, decoded(ref, data)) for ref, data in entries)
        return SyncResult(pages=pages, next_cursor=None, snapshot=True)

    @staticmethod
    def _read(root: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]:
        canonical = contained_root(root)
        entries: list[tuple[str, bytes]] = []
        total = 0
        for path in sorted(canonical.rglob("*")):
            ref = path.relative_to(canonical).as_posix()
            if path.is_symlink() or not path.is_file() or not is_markdown_path(ref):
                continue
            with contained_file(ref, canonical) as handle:
                data = handle.read_bytes(max_bytes + 1)
            total += len(data)
            if total > max_bytes:
                raise StreamFault(f"{root} holds over {max_bytes} bytes of markdown")
            entries.append((ref, data))
        return tuple(entries)
