"""Whether the answer is right, now that every message is prefetched.

Every case seeds one workspace corpus and one web corpus and then asks one message. The web is the
`eval_search` backend the eval environment registers, answering from documents the case wrote, so a
run grades the answer against a corpus whose true values the case knows rather than against what
the live web said today. The workspace side is staged as files a folder source syncs and the memory
extension's page-change indexer chunks, so the pages the prefetch reads arrived the way a tenant's
own documents do.

The prefetch runs on every member message, so whether it routed is not a question a case asks. What
is graded is accuracy:

| what a right answer does | cases |
| --- | --- |
| states the corpus value, named with the source that holds it | R01, R02, R03, R04, R09, R10 |
| states every value a multi-part question asks for | R09, R10, R13 |
| says what the corpus does not answer instead of inventing it | R13 |
| states a rate in the unit its passage gives it | R14 |
| resolves two web passages that disagree | R08 |
| resolves a workspace record against a web passage | R07, R12 |
| resolves two workspace records that disagree | R11, R15 |
| acts, because the member asked for an action | R05, R06 |

A conflict case grades the resolution, not the detection: the reply names each side with its source
and its date, lands on one value, and gives the reason that value wins — authority, recency, or
specificity. A figure merged out of the two fails deterministically.

Whether the turn answered without searching again is recorded and never gates a verdict. R05 and
R06 still require tool calls, because the member asked for an action.

The deploy must select the eval search backend (`[research] search_provider = "eval_search"`), so
the suite is explicit-only: a stack pointed at the live web cannot grade a seeded corpus.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_eval_env.manifest import NAME as EVAL_ENV_NAME
from ufo_ext_eval_env.manifest import SEARCH_CORPUS_KEY, SearchDocument
from ufo_ext_memory.store import memory_item

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.harness import JsonObject
from evals.harness.scorers import combine, scored_only
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
USAGE_URL = "https://northwind.example/usage-rates"


@dataclass(frozen=True)
class Document:
    """One document the tenant has synced: what the page is called, what it says, and the date it
    carries. `dated` becomes the staged file's modification time, which the folder source reports
    as the page's record date, so the passage header states it the way a web hit's date is
    stated."""

    name: str
    body: str
    dated: str


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
USAGE_RATES = SearchDocument(
    url=USAGE_URL,
    title="Northwind usage rates",
    text="Northwind bills API calls over the plan limit at $0.40 per 1,000 calls.",
    published_date="2026-02-11",
    terms=("northwind", "api", "call", "calls", "overage", "over", "limit", "usage", "charge"),
)

ORDER_FORM = Document(
    name="northwind-order-form",
    dated="2025-03-06",
    body=(
        "Northwind order form, signed 6 March 2025.\n\n"
        "Northwind bills this workspace $24 per seat per month. The rate is fixed until renewal.\n"
        "The contract renews on 1 July 2026 and takes 30 days' notice to cancel.\n"
        "Dana Whitfield owns the Northwind relationship and signs its order forms."
    ),
)
SEAT_REGISTER = Document(
    name="northwind-seats",
    dated="2026-03-02",
    body=(
        "Northwind seat register, updated 2 March 2026.\n\n"
        "This workspace holds 48 Northwind seats. 41 of them are assigned; 7 are spare."
    ),
)
SEAT_AUDIT = Document(
    name="northwind-seat-audit",
    dated="2026-08-07",
    body=(
        "Northwind seat audit, completed 7 August 2026.\n\n"
        "This workspace holds 52 Northwind seats after the June expansion. 44 are assigned; "
        "8 are spare."
    ),
)
ORDER_AMENDMENT = Document(
    name="northwind-order-amendment",
    dated="2026-06-12",
    body=(
        "Northwind order form amendment, signed 12 June 2026.\n\n"
        "From 1 July 2026 Northwind bills this workspace $27 per seat per month. This amendment "
        "replaces the rate in the order form signed 6 March 2025."
    ),
)
SUPPORT_NOTE = Document(
    name="northwind-support-note",
    dated="2024-09-09",
    body=(
        "Northwind support note, written 9 September 2024.\n\n"
        "Northwind support answers 08:00 to 16:00 UTC on weekdays."
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
            path = STAGING_ROOT / f"{document.name}.txt"
            path.write_text(document.body, encoding="utf-8")
            stamped = datetime.fromisoformat(document.dated).replace(tzinfo=UTC).timestamp()
            os.utime(path, (stamped, stamped))


def _seeding(corpus: Corpus) -> CapabilitySeed:
    async def seed(_workspace_id: UUID, _agent_id: UUID, blob: WorkspaceBlobStore) -> None:
        await ScopedStore(extension=EVAL_ENV_NAME).put(
            SEARCH_CORPUS_KEY, [document.model_dump() for document in corpus.web]
        )
        await PageIngest(corpus.pages).run(blob)

    return seed


async def _cleaning(workspace_id: UUID, _agent_id: UUID, blob: WorkspaceBlobStore) -> None:
    """Drop what this case left behind: its web corpus, its staged pages, and every memory row the
    turn wrote. The cases share one workspace, so a row remembering Northwind's price would answer
    the next case out of memory rather than out of the corpus that case seeded."""
    await ScopedStore(extension=EVAL_ENV_NAME).put(SEARCH_CORPUS_KEY, [])
    await PageIngest(()).run(blob)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(memory_item).where(memory_item.c.workspace_id == workspace_id)
        )


@dataclass(frozen=True)
class Fact:
    """One truth the seeded corpus holds: the phrasings a right answer may state the value in, and
    the ways it may name the source that holds it. A case grades the value and the source, never
    the wording around them."""

    name: str
    values: tuple[str, ...]
    sources: tuple[str, ...]


def _stated(response: str, option: str) -> bool:
    """Whether the reply states this value as a value of its own. A digit sits inside longer
    figures a reply computes — `48` inside `$1,248`, `52` inside `1,152` — so a value is read only
    where no digit runs into it. A sentence-ending point is not a digit and keeps the match."""
    return (
        re.search(rf"(?<![\d.,]){re.escape(option)}(?!\d|[.,]\d)", response, re.IGNORECASE)
        is not None
    )


def states(*facts: Fact) -> Grader:
    """Every fact reaches the reply as one of its values, named with one of its sources."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        response = output.response
        stated: list[str] = []
        missing: list[str] = []
        for fact in facts:
            value = next((option for option in fact.values if _stated(response, option)), "")
            source = next((option for option in fact.sources if _stated(response, option)), "")
            if not value:
                missing.append(f"{fact.name}: no value")
            elif not source:
                missing.append(f"{fact.name}: {value} with no source")
            else:
                stated.append(f"{fact.name}: {value} from {source}")
        evidence: JsonObject = {"stated": list(stated), "missing": list(missing)}
        if missing:
            return CapabilityVerdict(False, f"the reply misses {'; '.join(missing)}", evidence)
        return CapabilityVerdict(True, f"the reply states {'; '.join(stated)}", evidence)

    return DescribedGrader(
        "; ".join(
            f"the reply states {fact.name} as {' or '.join(fact.values)}, sourced to "
            f"{' or '.join(fact.sources)}"
            for fact in facts
        ),
        grade,
    )


HEDGE = r"somewhere between|anywhere between|split the difference|average of the two"
"""A reply that states a span instead of a value has merged the two sources as surely as one that
states their midpoint, so every conflict case fails on both."""


def never_states(description: str, pattern: str) -> Grader:
    """No figure matching `pattern` reaches the reply: the merged, invented, or wrongly united
    value a right answer never lands on."""

    expression = re.compile(pattern, re.IGNORECASE)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        found = tuple(match.group(0) for match in expression.finditer(output.response))
        evidence: JsonObject = {"found": list(found)}
        if found:
            return CapabilityVerdict(
                False, f"the reply gives {description}: {', '.join(found)}", evidence
            )
        return CapabilityVerdict(True, f"the reply gives no {description}", evidence)

    return DescribedGrader(f"the reply gives no {description}", grade)


async def _no_tools(output: CapabilityOutput) -> CapabilityVerdict:
    called = tuple(call.call for call in output.calls)
    evidence: JsonObject = {"calls": list(called)}
    if called:
        return CapabilityVerdict(False, f"called {', '.join(called)}", evidence)
    return CapabilityVerdict(True, "answered from the prefetched passages", evidence)


ANSWERED_WITHOUT_A_SEARCH = scored_only(
    DescribedGrader("whether the turn called a tool, recorded and not required", _no_tools)
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
DISAGREEING_SEATS = Corpus(web=(), pages=(SEAT_REGISTER, SEAT_AUDIT))
DISAGREEING_RATES = Corpus(web=(), pages=(ORDER_FORM, ORDER_AMENDMENT))
DISAGREEING_SUPPORT = Corpus(web=(SUPPORT_WINDOW,), pages=(SUPPORT_NOTE,))
USAGE_WEB = Corpus(web=(PUBLISHED_PRICE, USAGE_RATES), pages=())

PRICING_PAGE = ("northwind.example/pricing", "pricing page")
ARCHIVE_PAGE = ("northwind.example/2025/pricing", "2025 archive", "2025 pricing")
CHANGELOG_PAGE = ("northwind.example/changelog", "changelog")
STATUS_PAGE = ("status.northwind.example", "status page")
USAGE_PAGE = ("northwind.example/usage-rates", "usage rates")
ORDER_FORM_RECORD = ("order form", "northwind-order-form")
AMENDMENT_RECORD = ("amendment", "northwind-order-amendment")
SEAT_REGISTER_RECORD = ("seat register", "northwind-seats")
SEAT_AUDIT_RECORD = ("seat audit", "northwind-seat-audit")
SUPPORT_NOTE_RECORD = ("support note", "northwind-support-note")

LIST_PRICE = Fact("the $30 list price", ("$30",), PRICING_PAGE)
ARCHIVED_LIST_PRICE = Fact("the superseded $22 price", ("$22",), ARCHIVE_PAGE)
CONTRACT_RATE = Fact("the $24 contracted rate", ("$24",), ORDER_FORM_RECORD)
AMENDED_RATE = Fact("the $27 amended rate", ("$27",), AMENDMENT_RECORD)
RENEWAL_DATE = Fact(
    "the renewal date",
    ("1 july 2026", "july 1, 2026", "2026-07-01", "1 jul 2026"),
    ORDER_FORM_RECORD,
)
FRANKFURT_DATE = Fact(
    "the Frankfurt opening date",
    ("3 february 2026", "february 3, 2026", "2026-02-03", "3 feb 2026"),
    CHANGELOG_PAGE,
)
SUPPORT_HOURS = Fact(
    "the published support window",
    ("09:00 to 17:00", "09:00-17:00", "09:00\u201317:00", "9:00 to 17:00", "9am to 5pm"),
    STATUS_PAGE,
)
NOTED_SUPPORT_HOURS = Fact(
    "the workspace note's support window",
    ("08:00 to 16:00", "08:00-16:00", "08:00\u201316:00", "8:00 to 16:00", "8am to 4pm"),
    SUPPORT_NOTE_RECORD,
)
REGISTERED_SEATS = Fact("the registered 48 seats", ("48",), SEAT_REGISTER_RECORD)
AUDITED_SEATS = Fact("the audited 52 seats", ("52",), SEAT_AUDIT_RECORD)
OVERAGE_RATE = Fact(
    "the overage rate in its own unit",
    ("$0.40 per 1,000", "$0.40 per 1000", "$0.40 per thousand", "$0.0004"),
    USAGE_PAGE,
)

CITES_ITS_SOURCE = (
    "The reply names where the answer came from — the page, its address, or the workspace record "
    "it came from. A passage number beside that name is part of the convention and is not a "
    "missing source; a passage number in place of the name is.",
)
"""One criterion, and it is the one accuracy rests on. A second criterion counting whether every
figure in the reply carries its own citation failed two correct answers in three runs, because the
judge reads the answer without the corpus and can only measure how often the reply repeats a
source."""


def resolving(value: str, reason: str, losing: str) -> tuple[str, ...]:
    """The three criteria a conflict case grades: both sides reported, one value acted on, and the
    reason it wins."""
    return (
        "The reply names each side with its source and its date, and states that the two do not "
        "agree or that one supersedes the other.",
        f"The reply lands on {value} as the value to act on, and gives {reason} as the reason.",
        f"The reply neither merges the two into one figure nor leaves {losing}.",
    )


CASES = (
    _case(
        "R01-external-fact",
        "What does Northwind charge per seat on its team plan?",
        WEB_ONLY,
        combine(states(LIST_PRICE), ANSWERED_WITHOUT_A_SEARCH),
        rubric=CITES_ITS_SOURCE,
    ),
    _case(
        "R02-internal-fact",
        "What are we paying Northwind per seat?",
        WORKSPACE_ONLY,
        combine(states(CONTRACT_RATE), ANSWERED_WITHOUT_A_SEARCH),
        rubric=CITES_ITS_SOURCE,
    ),
    _case(
        "R03-mixed-sources",
        "What is Northwind's list price per seat, and what do we actually pay?",
        BOTH,
        combine(states(LIST_PRICE, CONTRACT_RATE), ANSWERED_WITHOUT_A_SEARCH),
        rubric=(
            "The reply attributes the list price to Northwind's own page and the paid rate to the "
            "workspace's record.",
            *CITES_ITS_SOURCE,
        ),
    ),
    _case(
        "R04-external-date-fact",
        "When did Northwind open its Frankfurt region?",
        WEB_ONLY,
        combine(states(FRANKFURT_DATE), ANSWERED_WITHOUT_A_SEARCH),
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
        combine(
            states(CONTRACT_RATE, LIST_PRICE),
            never_states("a rate merged out of $24 and $30", HEDGE + r"|\$2[5-9]\b"),
        ),
        rubric=resolving(
            "$24, the rate the workspace's own signed order form fixes",
            "that the tenant's signed contract is the authority for what this workspace pays, "
            "while $30 is Northwind's public list price",
            "$30 standing as what this workspace pays",
        ),
    ),
    _case(
        "R08-two-web-pages-disagree",
        "What is Northwind's published team price per seat?",
        DISAGREEING_WEB,
        combine(
            states(LIST_PRICE, ARCHIVED_LIST_PRICE),
            never_states("a price merged out of $22 and $30", HEDGE + r"|\$2[3-9]\b"),
        ),
        rubric=resolving(
            "$30, the price on the page published 11 February 2026",
            "that the 2026 page is newer than the 2025 archive holding $22",
            "$22 standing as the current published price",
        ),
    ),
    _case(
        "R09-three-questions-at-once",
        "What does Northwind charge per seat on the team plan? What are its support hours? "
        "When did it open the Frankfurt region?",
        WEB_ONLY,
        combine(states(LIST_PRICE, SUPPORT_HOURS, FRANKFURT_DATE), ANSWERED_WITHOUT_A_SEARCH),
        rubric=("The reply answers all three questions.",),
    ),
    _case(
        "R10-two-questions-two-corpora",
        "When does our Northwind contract renew, and what does Northwind charge per seat on its "
        "public price list?",
        BOTH,
        combine(states(RENEWAL_DATE, LIST_PRICE), ANSWERED_WITHOUT_A_SEARCH),
        rubric=(
            "The reply answers both questions and attributes each to the corpus it came from.",
        ),
    ),
    _case(
        "R11-two-workspace-records-disagree",
        "How many Northwind seats do we hold?",
        DISAGREEING_SEATS,
        combine(
            states(AUDITED_SEATS, REGISTERED_SEATS),
            never_states("a seat count merged out of 48 and 52", HEDGE + r"|\b(?:49|50|51)\b"),
        ),
        rubric=resolving(
            "52 seats, the count the audit completed 7 August 2026 gives",
            "that the audit is the newer record, taken after the June expansion the register of "
            "2 March 2026 predates",
            "48 seats standing as the current count",
        ),
    ),
    _case(
        "R12-web-overrules-a-stale-workspace-note",
        "What are Northwind's support hours?",
        DISAGREEING_SUPPORT,
        combine(
            states(SUPPORT_HOURS, NOTED_SUPPORT_HOURS),
            never_states(
                "a support window merged out of the two",
                HEDGE + "|08:00\\s*(?:to|-|\u2013)\\s*17:00|09:00\\s*(?:to|-|\u2013)\\s*16:00",
            ),
        ),
        rubric=resolving(
            "09:00 to 17:00 UTC on weekdays, the hours Northwind's own status page gives",
            "that the status page of 20 January 2026 is newer than the workspace note of "
            "9 September 2024, or that Northwind's own page is the authority for its support "
            "hours",
            "08:00 to 16:00 standing as the current support window",
        ),
    ),
    _case(
        "R13-half-the-question-is-answerable",
        "What does Northwind charge per seat on the team plan, and what uptime does its SLA "
        "promise?",
        WEB_ONLY,
        combine(
            states(LIST_PRICE),
            never_states("an uptime figure no passage carries", r"\b99(?:\.\d+)?\s*%"),
            ANSWERED_WITHOUT_A_SEARCH,
        ),
        rubric=(
            "The reply states that it has no uptime commitment for Northwind, or that its sources "
            "do not give one.",
            "The reply states no uptime figure as a fact about Northwind.",
        ),
    ),
    _case(
        "R14-rate-keeps-its-unit",
        "What does Northwind charge for API calls over the plan limit?",
        USAGE_WEB,
        combine(
            states(OVERAGE_RATE),
            never_states(
                "the overage rate as a per-call price", r"\$?0\.40\s*(?:per|/|a)\s*(?:api\s+)?call"
            ),
            ANSWERED_WITHOUT_A_SEARCH,
        ),
        rubric=(
            "The reply gives the overage rate per 1,000 calls, or converts it to a per-call price "
            "of $0.0004.",
        ),
    ),
    _case(
        "R15-amendment-supersedes-the-order-form",
        "What are we paying Northwind per seat now?",
        DISAGREEING_RATES,
        combine(
            states(AMENDED_RATE, CONTRACT_RATE),
            never_states("a rate merged out of $24 and $27", HEDGE + r"|\$2(?:5|6)(?:\.\d+)?\b"),
        ),
        rubric=resolving(
            "$27 per seat per month, the rate the amendment signed 12 June 2026 sets from "
            "1 July 2026",
            "that the amendment replaces the rate in the order form signed 6 March 2025",
            "$24 standing as the rate paid today",
        ),
    ),
)
