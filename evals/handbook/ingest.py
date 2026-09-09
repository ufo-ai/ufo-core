"""Index a task's policy documents as a synced source before its turn opens.

A company running ufo would have its handbook ingested and searchable, not sitting unread on a disk.
In the corpus as measured, a quarter of every case's tool calls go to converting and reading the
policy document — budget a real deployment never spends. This arm establishes the deployment a
customer would actually have: the documents arrive as pages in the workspace index, and the agent
reaches them through `memory_search` like any other synced source.

Every document-like file is ingested, not a chosen handbook. Which policy governs is sometimes the
task's own question — one task ships two near-identical SOPs — so picking one for the agent would
answer the hardest part for it. Spreadsheets stay out: they are live task data the agent reads
directly, and the folder backend decodes UTF-8 only.

Pages are indexed but no fact is derived from them. Deriving would put a model's paraphrase of a
policy into memory, and these tasks turn on exact thresholds and exact wording — a summary that
rounds "2%" or reworks a required message is worse than no memory at all. Verbatim page snippets are
what the agent needs.

Settling the cursor decides the race but not the tick already inside it. `PageChangeRunner.drive`
advances the cursor by compare-and-set, so a tick of serve's page-change job that is in the deriver
when the settle lands cannot write its older cursor over the settled one and nothing is replayed —
but that tick still commits the paraphrase it was already deriving, after any delete. So the cursor
is settled, what the gap produced is deleted, and then the invariant is *checked* —
`derived_facts` is read again before the turn is graded, and a case whose policy was paraphrased
fails loudly rather than scoring against memory it was never supposed to have."""

from __future__ import annotations

import asyncio
import re
import subprocess
import zipfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa

from evals.handbook.corpus import HandbookTask
from ufo.blob import WorkspaceBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    EmbedClient,
    IndexBackend,
    IndexScope,
)
from ufo.runtime.jobs import PAGE_CHANGE_CURSOR_KEY, PageChangeRunner
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SyncDriver,
    register_sources,
)
from ufo.schema import tables

DOCUMENT_SUFFIXES = (".pdf", ".html", ".htm", ".docx")
MEMORY_EXTENSION = "memory"
INDEX_CONSUMER = "index_pages"
DERIVE_CONSUMER = "derive_facts"
MEMORY_PACKAGE = import_module("ufo_ext_memory.manifest")
MEMORY_STORE = import_module("ufo_ext_memory.store")
TAG = re.compile(r"<[^>]+>")
BLANK_LINES = re.compile(r"[ \t]*\n[ \t]*")


def document_text(path: Path) -> str:
    """The document as UTF-8 text. The folder backend decodes bytes as UTF-8, so a PDF or docx is
    converted here — before the turn, which is the point: the agent stops paying for it."""
    match path.suffix.lower():
        case ".pdf":
            return _pdf_text(path)
        case ".docx":
            with zipfile.ZipFile(path) as archive:
                markup = archive.read("word/document.xml").decode("utf-8", errors="replace")
            return _plain(markup.replace("</w:p>", "\n"))
        case ".html" | ".htm":
            return _plain(path.read_text(encoding="utf-8", errors="replace"))
        case _:
            raise ValueError(f"{path.name} is not a policy document this arm can ingest")


def _pdf_text(path: Path) -> str:
    """`pdftotext -layout`, not a Python extractor. Compared on the same manuals, pdfplumber runs
    words together across column boundaries — `receiving-doc kusing`, `inventory_master.xls x`,
    `Heat_Numbers_On_Han cdolumn` — and the damage lands on filenames, column headers and channel
    names, which are the tokens the rubrics match character for character."""
    done = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise ValueError(f"pdftotext failed on {path.name}: {done.stderr.decode()[:200]}")
    return done.stdout.decode("utf-8", errors="replace")


def _plain(markup: str) -> str:
    return BLANK_LINES.sub("\n", TAG.sub(" ", markup)).strip()


@dataclass(frozen=True)
class DocumentIngest:
    """Stages one task's policy documents as text, registers them as a folder source, and drives the
    sync and the page indexer so they are searchable by the time the turn opens."""

    task: HandbookTask
    staging_root: Path
    blob: WorkspaceBlobStore
    index: IndexBackend
    embed: EmbedClient
    manifests: tuple[Manifest, ...]
    postgres: bool

    @property
    def folder(self) -> Path:
        return self.staging_root / self.task.task_id

    @property
    def _root(self) -> Path:
        """Resolved, so a relative `--handbook-ingest` still matches the absolute roots the source
        rows carry, and so a sibling directory sharing the name's first characters cannot match."""
        return self.staging_root.resolve()

    def _is_mine(self, config: object) -> bool:
        root = str((config or {}).get("root", "")) if isinstance(config, dict) else ""
        if not root:
            return False
        candidate = Path(root).resolve()
        return candidate == self._root or self._root in candidate.parents

    async def run(self, workspace_id: UUID) -> tuple[str, ...]:
        """Purge whatever the previous case ingested, stage this task's documents, and index them.
        Returns the document names staged, so the envelope can say what is searchable."""
        await self._purge(workspace_id)
        await self._clear_memory(workspace_id)
        staged = await asyncio.to_thread(self._stage)
        if not staged:
            raise ValueError(f"HANDBOOK.md task {self.task.task_id!r} has no policy document")
        await register_sources(
            (SourceEntry(backend="folder", config=SourceConfig(root=str(self.folder))),)
        )
        await SyncDriver(
            backends={FOLDER_BACKEND: FolderSource()}, blob=self.blob, postgres=self.postgres
        ).run()
        await self._index_pages()
        await self._settle_deriver()
        await self._drop_derived_facts(workspace_id)
        return staged

    async def derived_facts(self, workspace_id: UUID) -> list[UUID]:
        """Facts the memory extension derived from a page this arm staged. Read after ingestion to
        delete them, and read again before grading to prove none came back."""
        page_ids = await self._my_page_ids(workspace_id)
        if not page_ids:
            return []
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(MEMORY_STORE.memory_item.c.id).where(
                            MEMORY_STORE.memory_item.c.workspace_id == workspace_id,
                            MEMORY_STORE.memory_item.c.created_from_page_id.in_(page_ids),
                        )
                    )
                ).scalars()
            )

    async def _purge(self, workspace_id: UUID) -> None:
        """Drop the pages, vectors and source rows of documents a previous case staged. Cases share
        a workspace, and the corpus rewrites its thresholds per task, so a vector left behind would
        answer this task's search with the last task's numbers — a wrong answer with nothing in the
        case's own environment to explain it.

        Scoped to what this arm itself staged, by config root under `staging_root`. A deploy's own
        `[[sources]]` folder entries are somebody else's rows; selecting on the backend alone would
        delete them."""
        source_ids = await self._my_source_ids(workspace_id)
        if not source_ids:
            return
        page_ids = await self._my_page_ids(workspace_id)
        for page_id in page_ids:
            await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        async with workspace_tx() as connection:
            await connection.execute(tables.page.delete().where(tables.page.c.uid.in_(page_ids)))
            await connection.execute(
                tables.source.delete().where(tables.source.c.id.in_(source_ids))
            )

    def _stage(self) -> tuple[str, ...]:
        self.folder.mkdir(parents=True, exist_ok=True)
        for stale in self.folder.iterdir():
            stale.unlink()
        staged: list[str] = []
        for path in sorted(self.task.workspace_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in DOCUMENT_SUFFIXES:
                continue
            text = document_text(path)
            if not text.strip():
                continue
            (self.folder / f"{path.stem}.txt").write_text(text, encoding="utf-8")
            staged.append(path.name)
        return tuple(staged)

    async def _index_pages(self) -> None:
        """Drive the page indexer so the staged pages carry vectors `memory_search` can reach."""
        runner = PageChangeRunner(
            manifests=self.manifests,
            pages=CorePageFeed(blob=self.blob),
            index=self.index,
            embed=self.embed,
            blob=self.blob,
            registry=None,
        )
        consumers = {
            (consumer.extension, consumer.discriminator): consumer
            for consumer in runner.consumers()
        }
        indexer = consumers.get((MEMORY_EXTENSION, INDEX_CONSUMER))
        if indexer is None:
            raise RuntimeError(
                f"no {MEMORY_EXTENSION}/{INDEX_CONSUMER} page consumer is registered"
            )
        await runner.drive(indexer)

    async def _clear_memory(self, workspace_id: UUID) -> None:
        """Drop every memory item in the workspace before the case begins.

        `_drop_derived_facts` only reaches what a page produced; anything the agent wrote with
        `memory_update` carries no page provenance and outlived the workspace reset. Cases share a
        workspace, so the previous task's notes — one case got another tenant's persona back on all
        thirty of its searches — occupied result slots in this one. The eval provisions this
        workspace for itself, so everything in it belongs to the case that just finished."""
        async with workspace_tx() as connection:
            item_ids = list(
                (
                    await connection.execute(
                        sa.select(MEMORY_STORE.memory_item.c.id).where(
                            MEMORY_STORE.memory_item.c.workspace_id == workspace_id
                        )
                    )
                ).scalars()
            )
        for item_id in item_ids:
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item_id)))
        if not item_ids:
            return
        async with workspace_tx() as connection:
            await connection.execute(
                MEMORY_STORE.memory_source.delete().where(
                    MEMORY_STORE.memory_source.c.memory_item_id.in_(item_ids)
                )
            )
            await connection.execute(
                MEMORY_STORE.memory_item.delete().where(MEMORY_STORE.memory_item.c.id.in_(item_ids))
            )

    async def _settle_deriver(self) -> None:
        """Carry the fact deriver's cursor to the indexer's high water, so serve's page-change job
        will not derive over the policy once the turn is running."""
        store = ScopedStore(extension=MEMORY_PACKAGE.NAME)
        high_water = await store.get(f"{PAGE_CHANGE_CURSOR_KEY}:{INDEX_CONSUMER}")
        if high_water is not None:
            await store.put(f"{PAGE_CHANGE_CURSOR_KEY}:{DERIVE_CONSUMER}", high_water)

    async def _drop_derived_facts(self, workspace_id: UUID) -> None:
        """Delete any fact derived from a page this arm staged, with its vectors.

        Settling the cursor stops derivation from here on, but serve runs the page-change job every
        minute and embedding a policy takes longer than that, so a fact can already exist by the
        time the cursor moves. A paraphrase of the policy reaching the turn is the thing this arm
        exists to prevent, so whatever the gap produced is deleted rather than hoped against."""
        derived = await self.derived_facts(workspace_id)
        for item_id in derived:
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item_id)))
        if not derived:
            return
        async with workspace_tx() as connection:
            await connection.execute(
                MEMORY_STORE.memory_source.delete().where(
                    MEMORY_STORE.memory_source.c.memory_item_id.in_(derived)
                )
            )
            await connection.execute(
                MEMORY_STORE.memory_item.delete().where(MEMORY_STORE.memory_item.c.id.in_(derived))
            )

    async def _my_source_ids(self, workspace_id: UUID) -> list[UUID]:
        """The folder sources this arm staged. A deploy's own `[[sources]]` folder entries are
        somebody else's rows; selecting on the backend alone would take them too."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.source.c.id, tables.source.c.config).where(
                        tables.source.c.workspace_id == workspace_id,
                        tables.source.c.backend == FOLDER_BACKEND,
                    )
                )
            ).all()
        return [row.id for row in rows if self._is_mine(row.config)]

    async def _my_page_ids(self, workspace_id: UUID) -> list[UUID]:
        source_ids = await self._my_source_ids(workspace_id)
        if not source_ids:
            return []
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.page.c.uid).where(tables.page.c.source_id.in_(source_ids))
                    )
                ).scalars()
            )
