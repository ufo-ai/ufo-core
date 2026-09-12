"""What the prefetch is worth, and what it must not cost.

Every case seeds one workspace corpus and one web corpus and then asks one message. The web is the
`eval_search` backend the eval environment registers, answering from documents the case wrote, so a
run grades ranking and wording rather than what the live web said today. The workspace side is
staged as files a folder source syncs and the memory extension's page-change indexer chunks, so the
pages the prefetch reads arrived the way a tenant's own documents do.

Five behaviours are graded across the set:

| behaviour | cases |
| --- | --- |
| the answer comes from the right corpus | R01, R02, R03, R10 |
| a factual question is answered, not searched again | R01, R02, R04 |
| an action still reaches for tools | R05, R06 |
| disagreeing sources are reported, not merged | R07, R08 |
| several questions in one message are all answered | R09, R10 |

The deploy must select the eval search backend (`[research] search_provider = "eval_search"`), so
the suite is explicit-only: a stack pointed at the live web cannot grade a seeded ranking.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_eval_env.manifest import NAME as EVAL_ENV_NAME
from ufo_ext_eval_env.manifest import SEARCH_CORPUS_KEY, SearchDocument

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.harness import JsonObject
from evals.harness.scorers import combine
from ufo.blob import WorkspaceBlobStore
from ufo.config import SourceConfig, SourceEntry, load_config
from ufo.db import workspace_tx
from ufo.host.ext.loader import embed_backend, index_backend, load_manifests
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.indexing import OWNER_KIND_PAGE, IndexBackend, IndexScope
from ufo.runtime.jobs import PageChangeRunner
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SyncDriver,
    register_sources,
)
from ufo.schema import tables
from ufo.sdk.context import ScopedStore

PRICING_URL = "https://northwind.example/pricing"
CHANGELOG_URL = "https://northwind.example/changelog"
STATUS_URL = "https://status.northwind.example"
ARCHIVE_URL = "https://northwind.example/2025/pricing"


@dataclass(frozen=True)
class Document:
    """One document the tenant has synced: what the page is called and what it says."""

    name: str
    body: str


@dataclass(frozen=True)
class Corpus:
    """The two corpora one case runs against: the web it can reach and the pages it holds."""

    web: tuple[SearchDocument, ...]
    pages: tuple[Document, ...]


PUBLISHED_PRICE = SearchDocument(
    url=PRICING_URL,
    title="Northwind pricing",
    text=(
        "Northwind Team is $30 per seat per month billed annually. Northwind Enterprise is quoted "
        "per company and includes the audit log export."
    ),
    published_date="2026-02-11",
    terms=("northwind", "price", "pricing", "charge", "cost", "seat", "plan"),
)
ARCHIVED_PRICE = SearchDocument(
    url=ARCHIVE_URL,
    title="Northwind pricing (2025 archive)",
    text="Northwind Team is $22 per seat per month billed annually.",
    published_date="2025-08-04",
    terms=("northwind", "price", "pricing", "charge", "cost", "seat"),
)
REGION_LAUNCH = SearchDocument(
    url=CHANGELOG_URL,
    title="Northwind changelog",
    text=(
        "Northwind opened its Frankfurt region on 3 February 2026. Data residency in Frankfurt is "
        "available on the Enterprise plan only."
    ),
    published_date="2026-02-03",
    terms=("northwind", "frankfurt", "region", "residency", "eu", "europe"),
)
SUPPORT_WINDOW = SearchDocument(
    url=STATUS_URL,
    title="Northwind support hours",
    text="Northwind support answers 09:00 to 17:00 UTC on weekdays. Severity 1 is answered 24/7.",
    published_date="2026-01-20",
    terms=("northwind", "support", "hours", "sev", "severity"),
)

ORDER_FORM = Document(
    name="northwind-order-form",
    body=(
        "Northwind order form, signed 6 March 2025.\n\n"
        "Northwind bills this workspace $24 per seat per month. The rate is fixed until renewal.\n"
        "The contract renews on 1 July 2026 and takes 30 days' notice to cancel.\n"
        "Dana Whitfield owns the Northwind relationship and signs its order forms."
    ),
)
SEAT_REGISTER = Document(
    name="northwind-seats",
    body=(
        "Northwind seat register.\n\n"
        "This workspace holds 48 Northwind seats. 41 of them are assigned; 7 are spare."
    ),
)


MEMORY_EXTENSION = "memory"
INDEX_CONSUMER = "index_pages"
STAGING_ROOT = Path(tempfile.gettempdir()) / "ufo-rag-prefetch-pages"


@dataclass(frozen=True)
class PageIngest:
    """Stage the case's documents as a folder source and index them, so the page store answers
    before the turn opens.

    The product path is the whole point: the files are synced by the real `SyncDriver` and indexed
    by the memory extension's own `page_change` consumer, so what the prefetch reads is a page the
    workspace holds rather than a row the eval wrote into the index itself. Every case owns the
    staging directory, and a case seeding no document still purges it — pages of the case before
    would otherwise answer this one's question.

    The staging root and the seeded web corpus are one per workspace, so the suite is registered
    `serial=True` in `evals/registry.py`: two cases in flight would stage over each other's corpus,
    and a case would be graded against documents it never seeded."""

    documents: tuple[Document, ...]

    async def run(self, blob: WorkspaceBlobStore) -> None:
        config = load_config()
        manifests = load_manifests(config.pack.name)
        key = os.environ.get(config.credentials.key_env)
        credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
        index = index_backend(manifests, config.memory.index_backend, credentials)
        embed = embed_backend(manifests, config.memory.embed_backend, credentials)
        await self._purge(index)
        await asyncio.to_thread(self._stage)
        if not self.documents:
            return
        await register_sources(
            (SourceEntry(backend="folder", config=SourceConfig(root=str(STAGING_ROOT))),)
        )
        await SyncDriver(
            backends={FOLDER_BACKEND: FolderSource()},
            blob=blob,
            postgres=config.database.url.startswith("postgresql"),
        ).run()
        runner = PageChangeRunner(
            manifests=manifests,
            pages=CorePageFeed(blob=blob),
            index=index,
            embed=embed,
            blob=blob,
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

    async def _purge(self, index: IndexBackend) -> None:
        async with workspace_tx() as connection:
            source_ids = list(
                (
                    await connection.execute(
                        sa.select(tables.source.c.uid).where(
                            tables.source.c.backend == FOLDER_BACKEND,
                            tables.source.c.config == {"root": str(STAGING_ROOT)},
                        )
                    )
                ).scalars()
            )
            page_ids = (
                list(
                    (
                        await connection.execute(
                            sa.select(tables.page.c.uid).where(
                                tables.page.c.source_uid.in_(source_ids)
                            )
                        )
                    ).scalars()
                )
                if source_ids
                else []
            )
        for page_id in page_ids:
            await index.delete(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        if not source_ids:
            return
        async with workspace_tx() as connection:
            await connection.execute(tables.page.delete().where(tables.page.c.uid.in_(page_ids)))
            await connection.execute(
                tables.source.delete().where(tables.source.c.uid.in_(source_ids))
            )

    def _stage(self) -> None:
        STAGING_ROOT.mkdir(parents=True, exist_ok=True)
        for stale in STAGING_ROOT.iterdir():
            stale.unlink()
        for document in self.documents:
            (STAGING_ROOT / f"{document.name}.txt").write_text(document.body, encoding="utf-8")


def _seeding(corpus: Corpus) -> CapabilitySeed:
    async def seed(_workspace_id: UUID, _agent_id: UUID, blob: WorkspaceBlobStore) -> None:
        await ScopedStore(extension=EVAL_ENV_NAME).put(
            SEARCH_CORPUS_KEY, [document.model_dump() for document in corpus.web]
        )
        await PageIngest(corpus.pages).run(blob)

    return seed


async def _cleaning(_workspace_id: UUID, _agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    await ScopedStore(extension=EVAL_ENV_NAME).put(SEARCH_CORPUS_KEY, [])
    await PageIngest(()).run(blob)


def _states(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    folded = text.casefold()
    return tuple(term for term in terms if term.casefold() in folded)


def answers_with(terms: tuple[str, ...], source: str) -> Grader:
    """Every term reaches the reply, so the answer carries what only `source` holds."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        stated = _states(output.response, terms)
        missing = tuple(term for term in terms if term not in stated)
        evidence: JsonObject = {"stated": list(stated), "missing": list(missing)}
        if missing:
            return CapabilityVerdict(
                False, f"the reply omits {', '.join(missing)} from the {source} record", evidence
            )
        return CapabilityVerdict(True, f"the reply states the {source} record", evidence)

    return DescribedGrader(f"the reply states {', '.join(terms)} from the {source} record", grade)


async def _no_tools(output: CapabilityOutput) -> CapabilityVerdict:
    called = tuple(call.call for call in output.calls)
    evidence: JsonObject = {"calls": list(called)}
    if called:
        return CapabilityVerdict(
            False, f"searched again instead of answering: {', '.join(called)}", evidence
        )
    return CapabilityVerdict(True, "answered from the prefetched passages", evidence)


ANSWERS_DIRECTLY = DescribedGrader(
    "the turn answers the question without calling a tool", _no_tools
)


async def _acts(output: CapabilityOutput) -> CapabilityVerdict:
    succeeded = tuple(call.call for call in output.calls if call.succeeded)
    evidence: JsonObject = {"calls": [call.call for call in output.calls]}
    if not succeeded:
        return CapabilityVerdict(False, "the action turn called no tool", evidence)
    return CapabilityVerdict(True, f"the action turn called {', '.join(succeeded)}", evidence)


CALLS_TOOLS = DescribedGrader("the turn calls at least one tool to do the work", _acts)


def _case(
    name: str,
    message: str,
    corpus: Corpus,
    grader: Grader,
    rubric: tuple[str, ...] = (),
) -> CapabilityCase:
    return CapabilityCase(
        name,
        message,
        grader,
        rubric=rubric,
        digest_tag=f"rag-prefetch:{name}",
        seed=_seeding(corpus),
        cleanup=_cleaning,
    )


WEB_ONLY = Corpus(web=(PUBLISHED_PRICE, REGION_LAUNCH, SUPPORT_WINDOW), pages=())
WORKSPACE_ONLY = Corpus(web=(), pages=(ORDER_FORM, SEAT_REGISTER))
BOTH = Corpus(
    web=(PUBLISHED_PRICE, REGION_LAUNCH, SUPPORT_WINDOW),
    pages=(ORDER_FORM, SEAT_REGISTER),
)
DISAGREEING_WEB = Corpus(web=(PUBLISHED_PRICE, ARCHIVED_PRICE), pages=())

CITES_ITS_SOURCE = (
    "The reply names where each fact came from — the page, its address, or the workspace's own "
    "record.",
    "The reply states no figure it attributes to no source.",
)

CASES = (
    _case(
        "R01-external-fact",
        "What does Northwind charge per seat on its team plan?",
        WEB_ONLY,
        combine(answers_with(("$30",), "published"), ANSWERS_DIRECTLY),
        rubric=CITES_ITS_SOURCE,
    ),
    _case(
        "R02-internal-fact",
        "What are we paying Northwind per seat?",
        WORKSPACE_ONLY,
        combine(answers_with(("$24",), "workspace"), ANSWERS_DIRECTLY),
        rubric=CITES_ITS_SOURCE,
    ),
    _case(
        "R03-mixed-sources",
        "What is Northwind's list price per seat, and what do we actually pay?",
        BOTH,
        combine(answers_with(("$30", "$24"), "web and workspace"), ANSWERS_DIRECTLY),
        rubric=(
            "The reply attributes the list price to Northwind's own page and the paid rate to the "
            "workspace's record.",
            *CITES_ITS_SOURCE,
        ),
    ),
    _case(
        "R04-factual-answer-not-another-search",
        "When did Northwind open its Frankfurt region?",
        WEB_ONLY,
        combine(answers_with(("Frankfurt", "2026"), "published"), ANSWERS_DIRECTLY),
    ),
    _case(
        "R05-action-still-uses-tools",
        "Email Dana at dana@evalco.test the Northwind renewal date and the seat count we hold.",
        BOTH,
        CALLS_TOOLS,
        rubric=("The reply reports that the email was sent, or names exactly what stopped it.",),
    ),
    _case(
        "R06-request-phrased-as-a-question",
        "Can you send Dana at dana@evalco.test a note with the Northwind renewal date?",
        BOTH,
        CALLS_TOOLS,
        rubric=("The reply reports that the note was sent, or names exactly what stopped it.",),
    ),
    _case(
        "R07-workspace-contradicts-the-web",
        "What is our per-seat cost for Northwind? I have seen two different numbers.",
        BOTH,
        answers_with(("$24", "$30"), "web and workspace"),
        rubric=(
            "The reply states that the two sources disagree.",
            "The reply names which source gives $30 and which gives $24, and does not average, "
            "merge, or silently pick between them.",
        ),
    ),
    _case(
        "R08-two-web-pages-disagree",
        "What is Northwind's published team price per seat?",
        DISAGREEING_WEB,
        answers_with(("$30",), "current published"),
        rubric=(
            "The reply gives the 2026 price as the current one and dates it.",
            "The reply names the older $22 figure as out of date, or states that the sources "
            "disagree and gives each one's date.",
        ),
    ),
    _case(
        "R09-three-questions-at-once",
        "What does Northwind charge per seat on the team plan? What are its support hours? "
        "When did it open the Frankfurt region?",
        WEB_ONLY,
        combine(answers_with(("$30", "Frankfurt"), "published"), ANSWERS_DIRECTLY),
        rubric=(
            "The reply answers all three questions.",
            "The reply gives Northwind's support hours as 09:00 to 17:00 UTC on weekdays.",
        ),
    ),
    _case(
        "R10-two-questions-two-corpora",
        "When does our Northwind contract renew, and what does Northwind charge per seat on its "
        "public price list?",
        BOTH,
        combine(answers_with(("July", "$30"), "web and workspace"), ANSWERS_DIRECTLY),
        rubric=(
            "The reply answers both questions and attributes each to the corpus it came from.",
        ),
    ),
)
