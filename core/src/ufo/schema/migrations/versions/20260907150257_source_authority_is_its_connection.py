"""a source row is one stream of its connection

The connection is the only thing a member names, shares or grants, and a source row is one stream
of one connection carrying no authority of its own. So `source.subject` and `source.owner_member_id`
go — disclosure derives from `connection.shared` — `source_grant` goes with them, leaving
`connector_grant` as the only access edge, and `source.connection_id` becomes the row's authority:
not null, indexed, cascading. `connection` grows what a member names about an account: the tenant
`base_url` its streams dial and the `backfill_days` its first sync reaches back, both carried over
from the streams that already hold the answer, and `owner_member_id` becomes nullable so that null
is the whole of what says a connection is the workspace's rather than a member's. The
`conversation_id` that `connection` and `connector_grant` each carried is dropped; nothing read it.
A source stamped `removed_at` is deleted with its pages, since a row that means "gone" has no
reading in a model where a connection is what goes.

Every source row is re-keyed, because a row's id is content-addressed over its config and that
config has lost `account` and `base_url` to the connection. Its pages and the memory rows citing it
move with it, and `page.source_id` gains ON DELETE CASCADE, so disconnecting an account now takes
the content it synced rather than stranding it. A memory row citing a source this revision deletes
keeps its citation: the item is a member's, and a citation to a page that is gone is exactly what
the cascade leaves behind every disconnect from here.

Disclosure survives the move without widening. A source read what its `subject` named and now
reads what its connection discloses, so a feed that ran on the workspace's own credential mints a
connection whose account handle carries that answer: empty where the whole workspace read it,
`member:<id>` where one member did — owned by that member and unshared, so the derived subject is
the one the row already carried. That split is the point rather than a refinement: the spec these
were registered with left `shared` false unless the member said otherwise, so one connection per
provider would have widened the common case, not an edge of it. A feed that names no account and no
member — a repository, a folder root — takes its config's identity JSON as its handle, the string
`source_row_id` hashes and `feed_handle` spells, so each is a connection of its own and deleting one
never takes another.

A connection is shared only where every stream of it was, whether this revision minted it or found
it. That is the whole population of shared connections carrying streams, not an edge: the registrar
stamped every connected stream `member:<owner>` whatever the connection's `shared` said, and sharing
the connection never restamped them, so a connection a member shared holds only private streams and
is unshared here. Sharing it instead would have disclosed every one of those streams to the whole
workspace on the first sync after the roll, with no member act. What it costs is reach — connector
use narrows to the owner until they share it again — and the downgrade cannot give that back: a
narrowed connection is then indistinguishable from one never shared, so `shared` stays false across
a downgrade while every subject returns exactly.

Two things the move does not carry, and why each is worth it:

- A page's `body_ref` still spells the source id it was written under. The blob is stored at that
  key and no read derives the key from the row, so every stored body stays readable; only bodies
  written from here name the new id.
- A page stored before `page.source_identity` existed carries none, so its stream's next sync
  resolves it by an id derived from the source, computes a different one, and writes a second row
  for that document. A snapshot stream tombstones the first on the same run; a delta stream leaves
  it until the document changes.

This revision does not meet the image it replaces, and that is the decision it records: it ships as
one release. The migrate Job completes before the fleet rolls, so from that moment until the last
outgoing pod is gone, the outgoing image (`6fe6e13db`) fails on every read of what this drops —
background sync and the member's own turn alike:

- No source syncs. `SyncDriver.candidate_workspaces` filters on `source.removed_at` and on
  `_readers_remain()` (`runtime/sources/sync.py:646,651`), which reads `source_grant` and
  `source.subject` (`sync.py:580-608`), and `_claim_due` selects `source.subject` and
  `source.owner_member_id` on top of both (`sync.py:749-762`). Every tick raises before it reaches a
  provider; no fetch is made and no page is written.
- Every read that decides who may read a source raises inside the turn that asks. `_source_readable`
  (`runtime/ext/context.py:999-1034`) joins `source_grant` and reads `source.subject`,
  `source.owner_member_id` and `source.removed_at`; `readable_page_states`, `readable_source_ids`
  and `source_pages` stand on it (`context.py:2148,2167,2536`), and `sources()` selects the dropped
  columns itself (`context.py:2470-2482`). So on an outgoing pod the memory extension's recall
  (`ufo_ext_memory/store.py:880`) raises and, being a best-effort `user_prompt_submit` hook, is
  swallowed — the agent answers without the synced facts it would have had; its search over pages
  (`store.py:961`) and the sources extension's page list and get (`ufo_ext_sources/pages.py:182,242,
  267`) raise into the tool call that asked. A member asking a question that reaches synced content
  gets an error until the last old pod is gone. Connecting an account raises the same way, in the
  registrar (`ufo_ext_sources/connected.py:75`) and in `register_source` (`context.py:2263-2282`).
- The connection surface fails with it. `GrantStore.record` and `attach` insert the dropped
  `conversation_id` on `connection` and `connector_grant` (`runtime/access/grants.py:452,505,670`),
  so connecting an account and granting an agent raise in the turn that asked; `disconnect` deletes
  from `source_grant` and stamps `source.removed_at` (`grants.py:736,745`); `_grant_summaries` and
  `connection_summaries` select the dropped columns (`grants.py:1207,1262`), so every connector
  listing — the object kind's and the portal's — raises. Each raises before its transaction
  commits, so a member sees the error, never a half-connected account.
- The sources extension's trigger surface fails. `on_page_change` lists sources before it wakes a
  trigger (`ufo_ext_sources/tools.py:1049`, through `tools.py:313`) and reads `readable_source_ids`
  per trigger (`tools.py:1070`); `on_link_seen` reads it before it offers a watch (`tools.py:1141`).
  No alert is delivered and no watch is offered; `post_tool_use` swallows the fault and the
  `user_prompt_submit` spec is best effort, so neither fails the turn it fired in.
- A run already past its fetch when the Job completes reaches `_write`'s authority read, which
  selects `source.subject` and `source.removed_at` (`sync.py:982-986`) and raises inside the one
  transaction that would have written the batch, so no batch lands half. Every source id such a pod
  holds names no row anyway, every id having moved.

What an operator sees is a page feed that stops advancing and turns whose source reads error — not
a partial write, not a half-migrated row. Nothing recovers on the old image and everything recovers
on the new one without a hand on it: `next_sync_at` is untouched and each row's claim is cleared
with its id, so every source is due the moment a new pod first looks and the first tick after the
roll takes the lot. The window is the length of the roll, and what it costs is the batches those
minutes would have carried, which the next run carries instead, and the answers those turns would
have given.
"""

import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "20260907150257"
down_revision: str | None = "20260906225812"
branch_labels: str | None = None
depends_on: str | None = None

SHARED_SUBJECT = "shared"
MEMBER_PREFIX = "member:"
FEED_HANDLE_PREFIX = "{"
WORKSPACE_ACCOUNT = ""
DIRECT_ACCOUNT = "default"
MAX_BACKFILL_DAYS = 36500
ALL_HISTORY = "all"
CONNECTION_CONFIG_KEYS = ("account", "base_url")
NON_IDENTITY_KEYS = frozenset({"backfill_days", "backfill_after"})
SOURCE_CITATIONS = ("memory_item", "memory_source")
SOURCE_AUTHORITY_FK = "source_authority_fkey"
SOURCE_CONNECTION_OWNER_FK = "source_connection_owner_fkey"
PAGE_SOURCE_FK = "page_source_id_fkey"
CONNECTION_CONVERSATION_FK = "connection_workspace_id_conversation_id_fkey"
GRANT_CONVERSATION_FK = "connector_grant_workspace_id_conversation_id_fkey"
EGRESS_TRIGGER_ROWS = (("insert", "new"), ("update", "new"), ("delete", "old"))

SQLITE_EGRESS_TRIGGER = """
create trigger {table}_bump_egress_rules_{op}
after {op} on "{table}"
begin
    update workspace
    set egress_rules_generation = egress_rules_generation + 1
    where id = {row}.workspace_id;
end
"""

PAGE_REVISION_INSERT_TRIGGER = """
create trigger page_assign_revision_insert
after insert on page
begin
    update workspace
    set page_revision = page_revision + 1
    where id = new.workspace_id;
    update page
    set revision = (
        select page_revision from workspace where id = new.workspace_id
    )
    where id = new.id;
end
"""

PAGE_REVISION_UPDATE_TRIGGER = """
create trigger page_assign_revision_update
after update of digest, body_ref, subject, tombstone on page
when new.digest is not old.digest
  or new.body_ref is not old.body_ref
  or new.subject is not old.subject
  or new.tombstone is not old.tombstone
begin
    update workspace
    set page_revision = page_revision + 1
    where id = new.workspace_id;
    update page
    set revision = (
        select page_revision from workspace where id = new.workspace_id
    )
    where id = new.id;
end
"""

CONNECTION_WITH_CONVERSATION = sa.Table(
    "connection",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("host", sa.Text, nullable=False),
    sa.Column("owner_member_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("shared", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("account_label", sa.Text, nullable=True),
    sa.Column("commit_name", sa.Text, nullable=True),
    sa.Column("commit_email", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "provider", "account_id", name="connection_identity"),
    sa.UniqueConstraint("workspace_id", "id", name="connection_workspace_identity"),
    sa.UniqueConstraint("workspace_id", "id", "owner_member_id", name="connection_owner_identity"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
    sa.ForeignKeyConstraint(
        ["workspace_id", "owner_member_id"], ["member.workspace_id", "member.id"]
    ),
)

CONNECTOR_GRANT_WITH_CONVERSATION = sa.Table(
    "connector_grant",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("agent_id", sa.Uuid, nullable=False),
    sa.Column("connection_id", sa.Uuid, nullable=False),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "workspace_id", "agent_id", "connection_id", name="connector_grant_identity"
    ),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
    sa.ForeignKeyConstraint(["workspace_id", "agent_id"], ["agent.workspace_id", "agent.id"]),
    sa.ForeignKeyConstraint(
        ["workspace_id", "connection_id"],
        ["connection.workspace_id", "connection.id"],
        ondelete="CASCADE",
    ),
)

SOURCE_WITH_ITS_OWN_AUTHORITY = sa.Table(
    "source",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("backend", sa.Text, nullable=False),
    sa.Column("config", sa.JSON, nullable=False),
    sa.Column("subject", sa.Text, nullable=False, server_default=SHARED_SUBJECT),
    sa.Column("owner_member_id", sa.Uuid, nullable=True),
    sa.Column("connection_id", sa.Uuid, nullable=True),
    sa.Column("cursor", sa.Text, nullable=True),
    sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consecutive_errors", sa.Integer, nullable=False, server_default="0"),
    sa.Column("consecutive_refusals", sa.Integer, nullable=False, server_default="0"),
    sa.Column("parked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("parked_reason", sa.Text, nullable=True),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("source_due", "next_sync_at"),
    sa.UniqueConstraint("workspace_id", "id", name="source_workspace_identity"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
)

SOURCE_ON_ITS_CONNECTION = sa.Table(
    "source",
    sa.MetaData(),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("backend", sa.Text, nullable=False),
    sa.Column("config", sa.JSON, nullable=False),
    sa.Column("connection_id", sa.Uuid, nullable=False),
    sa.Column("cursor", sa.Text, nullable=True),
    sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consecutive_errors", sa.Integer, nullable=False, server_default="0"),
    sa.Column("consecutive_refusals", sa.Integer, nullable=False, server_default="0"),
    sa.Column("consecutive_empty", sa.Integer, nullable=False, server_default="0"),
    sa.Column("parked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("parked_reason", sa.Text, nullable=True),
    sa.Column("claimed_by", sa.Text, nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("source_due", "next_sync_at"),
    sa.Index("source_authority", "workspace_id", "connection_id"),
    sa.UniqueConstraint("workspace_id", "id", name="source_workspace_identity"),
    sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
    sa.ForeignKeyConstraint(
        ["workspace_id", "connection_id"],
        ["connection.workspace_id", "connection.id"],
        ondelete="CASCADE",
        name=SOURCE_AUTHORITY_FK,
    ),
)


def _page_without_source_fk() -> sa.Table:
    """The page table minus the foreign key on `source_id`, which each direction of this revision
    replaces with one of its own. Every index it carries is spelled here because a sqlite rebuild
    recreates exactly what `copy_from` names, `page_source_identity`'s partial predicate with it."""
    return sa.Table(
        "page",
        sa.MetaData(),
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("source_id", sa.Uuid, nullable=False),
        sa.Column("source_identity", sa.Text, nullable=True),
        sa.Column("digest", sa.Text, nullable=False),
        sa.Column("body_ref", sa.Text, nullable=False),
        sa.Column("stream", sa.Text, nullable=False, server_default=""),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("record_created_at", sa.Text, nullable=True),
        sa.Column("record_updated_at", sa.Text, nullable=True),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tombstone", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("subject = 'shared' or subject like 'member:%'", name="page_subject"),
        sa.Index("page_feed", "workspace_id", "revision", "id"),
        sa.Index("page_source", "source_id"),
        sa.Index(
            "page_source_identity",
            "source_id",
            "source_identity",
            unique=True,
            postgresql_where=sa.text("source_identity is not null"),
            sqlite_where=sa.text("source_identity is not null"),
        ),
    )


def _connections() -> sa.TableClause:
    return sa.table(
        "connection",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("provider", sa.Text()),
        sa.column("account_id", sa.Text()),
        sa.column("host", sa.Text()),
        sa.column("base_url", sa.Text()),
        sa.column("backfill_days", sa.Integer()),
        sa.column("owner_member_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("shared", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _sources() -> sa.TableClause:
    return sa.table(
        "source",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("backend", sa.Text()),
        sa.column("config", sa.JSON()),
        sa.column("subject", sa.Text()),
        sa.column("owner_member_id", sa.Uuid()),
        sa.column("connection_id", sa.Uuid()),
        sa.column("claimed_by", sa.Text()),
        sa.column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.column("removed_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _pages() -> sa.TableClause:
    return sa.table("page", sa.column("source_id", sa.Uuid()))


def _config(stored: object) -> dict[str, object]:
    """A source's stored config as a mapping. Postgres hands back the decoded object and sqlite the
    text it was written as."""
    decoded = json.loads(stored) if isinstance(stored, str) else stored
    if not isinstance(decoded, dict):
        raise RuntimeError(f"source config is not an object: {stored!r}")
    return decoded


def _window(requested: object) -> int | None:
    """A stored `backfill_days` request as the integer the column holds. The word for all history
    becomes the century that is every provider's whole history."""
    if requested is None:
        return None
    if requested == ALL_HISTORY:
        return MAX_BACKFILL_DAYS
    if isinstance(requested, int) and 1 <= requested <= MAX_BACKFILL_DAYS:
        return requested
    raise RuntimeError(f"backfill_days {requested!r} is outside 1..{MAX_BACKFILL_DAYS}")


def _feed_handle(config: dict[str, object]) -> str:
    """A config's identity JSON: what `source_row_id` hashes, and the account handle of the
    connection a feed naming no account and no member takes — spelled as `feed_handle` spells it in
    core, so the connection `register_sources` looks for at the next boot is the one minted here."""
    identity = {key: value for key, value in config.items() if key not in NON_IDENTITY_KEYS}
    return json.dumps(identity, sort_keys=True)


def _row_id(
    workspace_id: UUID, backend: str, config: dict[str, object], connection_id: UUID | None
) -> UUID:
    """The content-addressed source row id, spelled as `source_row_id` spells it on each side of
    this revision: the connection generation the row hangs off, and what of its config says which
    dataset it is. A row that hangs off no connection carries no generation, which is the shape the
    direct path wrote before this."""
    generation = "" if connection_id is None else f"/connection/{connection_id}"
    return uuid5(
        NAMESPACE_URL, f"{workspace_id}/source/{backend}/{_feed_handle(config)}{generation}"
    )


def _subject_member(subject: str) -> UUID | None:
    """The member a source was disclosed to, or None where it was the whole workspace's. The account
    handle a minted connection carries spells that member the same way, so this reads both."""
    if not subject.startswith(MEMBER_PREFIX):
        return None
    return UUID(subject.removeprefix(MEMBER_PREFIX))


def _minted_handle(config: dict[str, object], account: str, reader: UUID | None) -> str:
    """The account handle a connection minted for a source carries. A config naming no account is a
    feed of a repository or a folder root, and its handle is the identity JSON of that config as the
    re-key leaves it, so each such feed is a connection of its own. Otherwise it is the account the
    config named, or — where that is the sentinel the workspace's own credential answered to — the
    member the source was disclosed to, so that one member's feed stays theirs."""
    if "account" not in config:
        return _feed_handle(
            {key: value for key, value in config.items() if key not in CONNECTION_CONFIG_KEYS}
        )
    if account not in (DIRECT_ACCOUNT, WORKSPACE_ACCOUNT):
        return account
    return WORKSPACE_ACCOUNT if reader is None else f"{MEMBER_PREFIX}{reader}"


def _minted_here(handle: str, owner: UUID | None) -> bool:
    """Whether a connection is one this revision — or `register_sources`, after it — minted rather
    than one a member connected: it names no owner, which the revision before this one required of
    every connection, or its handle is the workspace's, a member's, or a feed's identity JSON, none
    of which a broker ever issued."""
    return (
        owner is None
        or handle == WORKSPACE_ACCOUNT
        or handle.startswith(MEMBER_PREFIX)
        or handle.startswith(FEED_HANDLE_PREFIX)
    )


def _citing_tables(bind: sa.Connection) -> tuple[sa.TableClause, ...]:
    """The tables outside `source` that hold a source id, as this deploy actually installed them.
    A source id is content-addressed, so re-keying one moves every row that cites it, and only this
    revision knows the mapping. Each belongs to an extension a pack may leave out."""
    inspector = sa.inspect(bind)
    return tuple(
        sa.table(name, sa.column("source_id", sa.Uuid()))
        for name in SOURCE_CITATIONS
        if inspector.has_table(name)
    )


def _drop_egress_triggers(table: str) -> None:
    for operation, _ in EGRESS_TRIGGER_ROWS:
        op.execute(f"drop trigger {table}_bump_egress_rules_{operation}")


def _create_egress_triggers(table: str) -> None:
    for operation, row in EGRESS_TRIGGER_ROWS:
        op.execute(SQLITE_EGRESS_TRIGGER.format(table=table, op=operation, row=row))


def _drop_page_triggers() -> None:
    op.execute("drop trigger page_assign_revision_insert")
    op.execute("drop trigger page_assign_revision_update")


def _create_page_triggers() -> None:
    op.execute(PAGE_REVISION_INSERT_TRIGGER)
    op.execute(PAGE_REVISION_UPDATE_TRIGGER)


def upgrade() -> None:
    bind = op.get_bind()
    rebuilds = bind.dialect.name == "sqlite"
    op.drop_table("source_grant")
    _delete_removed_sources(bind)
    _unbind_sources(rebuilds)
    _widen_connection(rebuilds)
    _drop_grant_conversation(rebuilds)
    _rekey_sources(bind, _found_sources_on_connections(bind), rebuilds)
    _narrow_source()
    _cascade_pages(rebuilds)


def _delete_removed_sources(bind: sa.Connection) -> None:
    """A source stamped removed is deleted with its pages. The stamp meant a row whose feed was
    gone but whose id an extension might still hold; a connection is what goes now, and the rows it
    carries go with it, so nothing is left for the stamp to say."""
    source, page = _sources(), _pages()
    gone = sa.select(source.c.id).where(source.c.removed_at.is_not(None))
    bind.execute(sa.delete(page).where(page.c.source_id.in_(gone)))
    bind.execute(sa.delete(source).where(source.c.removed_at.is_not(None)))


def _unbind_sources(rebuilds: bool) -> None:
    """A bound row was held to an owner by the `source_connection_owner` check and to its
    connection's owner by the three-column key `source_connection_owner_fkey`. Both come off before
    anything below binds a row: the re-key binds the ownerless rows — a configured folder, a shared
    repository — to the connections minted for them, which the check refuses, and the unique key
    `_widen_connection` drops is the one that foreign key depends on, which postgres refuses to
    drop first. Sqlite spells a check only in the table's DDL, so it rebuilds `source` as
    `SOURCE_WITH_ITS_OWN_AUTHORITY` spells it — neither constraint named — and rebuilds it again in
    `_narrow_source` without the columns."""
    if rebuilds:
        with op.batch_alter_table(
            "source", copy_from=SOURCE_WITH_ITS_OWN_AUTHORITY, recreate="always"
        ):
            pass
        return
    op.drop_constraint(SOURCE_CONNECTION_OWNER_FK, "source", type_="foreignkey")
    op.drop_constraint("source_connection_owner", "source", type_="check")


def _widen_connection(rebuilds: bool) -> None:
    """The connection carries what a member names about an account. `owner_member_id` becomes
    nullable, which is the whole of what says who holds one: every connection standing today names a
    member and stays a member's, and a null arrives only on the rows minted below. `conversation_id`
    goes with the three-column key that carried it, and `base_url` and `backfill_days` arrive empty
    for the streams below to fill. Sqlite rebuilds `connection` here, which takes the triggers
    counting wire-affecting writes to it."""
    if rebuilds:
        _drop_egress_triggers("connection")
    with op.batch_alter_table("connection", copy_from=CONNECTION_WITH_CONVERSATION) as batch:
        batch.add_column(sa.Column("base_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("backfill_days", sa.Integer(), nullable=True))
        batch.alter_column("owner_member_id", existing_type=sa.Uuid(), nullable=True)
        batch.drop_constraint("connection_owner_identity", type_="unique")
        batch.drop_column("conversation_id")
        batch.create_check_constraint("connection_shared", "owner_member_id is not null or shared")
        batch.create_check_constraint(
            "connection_backfill_days",
            f"backfill_days is null or backfill_days between 1 and {MAX_BACKFILL_DAYS}",
        )
    if rebuilds:
        _create_egress_triggers("connection")


def _drop_grant_conversation(rebuilds: bool) -> None:
    if rebuilds:
        _drop_egress_triggers("connector_grant")
    with op.batch_alter_table(
        "connector_grant", copy_from=CONNECTOR_GRANT_WITH_CONVERSATION
    ) as batch:
        batch.drop_column("conversation_id")
    if rebuilds:
        _create_egress_triggers("connector_grant")


def _found_sources_on_connections(bind: sa.Connection) -> dict[UUID, UUID]:
    """Give every source row the connection it is a stream of, and give every connection what its
    streams know about the account.

    A row already bound to a connection keeps it. A row bound to none takes the connection its
    workspace already holds for that provider and the account its config names, and where none
    answers to that pair it takes one minted here. Each connection then reads its tenant URL and its
    backfill window off the lowest-numbered of its streams that names one — a binding only ever
    stored one of each, repeated on every row of it.

    A minted connection's account handle is what decides who reads its streams, because disclosure
    derives from the connection now and `source.subject` is about to go:

    - The workspace's own credential authenticates as no account, so a feed that ran on it and was
      disclosed to the whole workspace takes an empty handle, no owner and `shared`.
    - The same credential read by a feed disclosed to one member takes `member:<id>`, that member as
      owner, and not shared — so the one member who could read it still can, and nobody else. This
      is the common case rather than the exception: the spec that registered these defaulted to
      unshared, so an empty handle for all of them would have widened every one of them silently.
    - A row whose own connection is gone keeps the handle it authenticated as, so it mints a
      connection of its own rather than joining the workspace's. That connection cannot
      authenticate, so its streams park with a reason a member can act on — which beats merging
      them into a feed that would answer, under a name the member never asked for.
    - A row naming no account at all — a repository, a folder root — takes its config's identity
      JSON, so two repositories of one backend are two connections and deleting one cascades to its
      own streams alone.

    A connection is shared only where every stream of it was, minted or found. A handle carrying one
    private stream and one shared one narrows rather than widens, and a connection that already
    exists and carries a private stream is unshared here, since its streams now read what it
    discloses. It has an owner, so `connection_shared` holds, and the owner gives the reach back by
    sharing it again."""
    source, connection = _sources(), _connections()
    rows = bind.execute(sa.select(source).order_by(source.c.id)).mappings().all()
    if not rows:
        return {}
    held = {
        (row.workspace_id, row.provider, row.account_id): row.id
        for row in bind.execute(
            sa.select(
                connection.c.id,
                connection.c.workspace_id,
                connection.c.provider,
                connection.c.account_id,
            )
        )
    }
    authority: dict[UUID, UUID] = {}
    minted: dict[UUID, tuple[UUID, str, str]] = {}
    owner: dict[UUID, UUID | None] = {}
    private: dict[UUID, bool] = {}
    tenant: dict[UUID, str | None] = {}
    window: dict[UUID, int | None] = {}
    for row in rows:
        config = _config(row["config"])
        account = config.get("account", "")
        if not isinstance(account, str):
            raise RuntimeError(f"source {row['id']} names a non-string account {account!r}")
        base_url = config.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            raise RuntimeError(f"source {row['id']} names a non-string base_url {base_url!r}")
        reader = _subject_member(row["subject"])
        disclosed = row["owner_member_id"] or reader
        connection_id = row["connection_id"]
        if connection_id is None and "account" in config:
            connection_id = held.get((row["workspace_id"], row["backend"], account))
        if "account" not in config and "stream" in config:
            raise RuntimeError(
                f"source {row['id']} names a stream and no account; repair or remove it before "
                "migrating"
            )
        if connection_id is None:
            handle = _minted_handle(config, account, reader)
            identity = (row["workspace_id"], row["backend"], handle)
            connection_id = held.get(identity)
            if connection_id is None:
                connection_id = uuid4()
                held[identity] = connection_id
                minted[connection_id] = identity
        authority[row["id"]] = connection_id
        days = _window(config.get("backfill_days"))
        if owner.get(connection_id) is None:
            owner[connection_id] = disclosed
        private[connection_id] = private.get(connection_id, False) or reader is not None
        if tenant.get(connection_id) is None:
            tenant[connection_id] = base_url
        if window.get(connection_id) is None:
            window[connection_id] = days
    for connection_id, (workspace_id, provider, handle) in minted.items():
        shared = not private[connection_id]
        held_by = _subject_member(handle)
        if held_by is None and handle != WORKSPACE_ACCOUNT:
            held_by = owner[connection_id]
        if held_by is None and not shared:
            raise RuntimeError(
                f"the {provider!r} streams of workspace {workspace_id} are disclosed to a member "
                f"and name none, so no connection can carry them; repair their owner before "
                f"migrating"
            )
        bind.execute(
            sa.insert(connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=provider,
                account_id=handle,
                host="",
                base_url=tenant[connection_id],
                backfill_days=window[connection_id],
                owner_member_id=held_by,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _settle_found(bind, set(tenant) - set(minted), private, tenant, window)
    return authority


def _settle_found(
    bind: sa.Connection,
    found: set[UUID],
    private: dict[UUID, bool],
    tenant: dict[UUID, str | None],
    window: dict[UUID, int | None],
) -> None:
    """A connection that already stood takes what its streams knew — the tenant URL and the backfill
    window — and is unshared where it carries a private stream, since its streams now read what it
    discloses. It has an owner, so `connection_shared` holds."""
    connection = _connections()
    for connection_id in found:
        if private[connection_id]:
            bind.execute(
                sa.update(connection)
                .where(connection.c.id == connection_id, connection.c.shared.is_(True))
                .values(shared=False, updated_at=sa.func.now())
            )
        if tenant[connection_id] is None and window[connection_id] is None:
            continue
        bind.execute(
            sa.update(connection)
            .where(connection.c.id == connection_id)
            .values(
                base_url=tenant[connection_id],
                backfill_days=window[connection_id],
                updated_at=sa.func.now(),
            )
        )


def _rekey_sources(bind: sa.Connection, authority: dict[UUID, UUID], rebuilds: bool) -> None:
    """Re-key every source under the connection it hangs off, which is what makes the config move
    real: `account` and `base_url` leave it for the connection and `backfill_days` becomes the
    integer the connection holds, so the hash over what is left names a different row. The claim on
    each row is cleared with its id, a claim naming a row that has moved being no claim at all.

    Two streams of one connection that reached different tenants collapse onto one id here, because
    a connection dials one tenant: the lower-numbered row keeps the id and the other is deleted with
    its pages, its content being what a tenant this connection no longer reaches once held.

    Postgres holds `page.source_id` to a source that exists, so the key over it comes off before a
    row moves and `_cascade_pages` puts the cascading one back. Sqlite needs neither: alembic builds
    its own engine, which never runs the pragma that turns foreign keys on."""
    source, page = _sources(), _pages()
    rows = bind.execute(sa.select(source).order_by(source.c.id)).mappings().all()
    keeps: dict[UUID, UUID] = {}
    plan: list[tuple[UUID, UUID, UUID, dict[str, object]]] = []
    strays: list[UUID] = []
    for row in rows:
        config = _config(row["config"])
        fresh = {key: value for key, value in config.items() if key not in CONNECTION_CONFIG_KEYS}
        if "backfill_days" in fresh:
            fresh["backfill_days"] = _window(fresh["backfill_days"])
        connection_id = authority[row["id"]]
        fresh_id = _row_id(row["workspace_id"], row["backend"], fresh, connection_id)
        if fresh_id in keeps:
            strays.append(row["id"])
            continue
        keeps[fresh_id] = row["id"]
        plan.append((row["id"], fresh_id, connection_id, fresh))
    if strays:
        bind.execute(sa.delete(page).where(page.c.source_id.in_(strays)))
        bind.execute(sa.delete(source).where(source.c.id.in_(strays)))
    citing = (page, *_citing_tables(bind))
    if not rebuilds:
        op.drop_constraint(PAGE_SOURCE_FK, "page", type_="foreignkey")
    for was, now, connection_id, config in plan:
        for table in citing:
            bind.execute(sa.update(table).where(table.c.source_id == was).values(source_id=now))
        bind.execute(
            sa.update(source)
            .where(source.c.id == was)
            .values(
                id=now,
                config=config,
                connection_id=connection_id,
                claimed_by=None,
                claim_expires_at=None,
                updated_at=sa.func.now(),
            )
        )


def _narrow_source() -> None:
    """The row keeps what says which stream it is and nothing that says who may read it. Dropping
    `subject` and `owner_member_id` takes the two checks and the two foreign keys spelled over them
    on both dialects, so neither is named here."""
    with op.batch_alter_table("source", copy_from=SOURCE_WITH_ITS_OWN_AUTHORITY) as batch:
        batch.drop_column("subject")
        batch.drop_column("owner_member_id")
        batch.drop_column("removed_at")
        batch.add_column(
            sa.Column("consecutive_empty", sa.Integer(), nullable=False, server_default="0")
        )
        batch.alter_column("connection_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            SOURCE_AUTHORITY_FK,
            "connection",
            ["workspace_id", "connection_id"],
            ["workspace_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_index("source_authority", ["workspace_id", "connection_id"])


def _cascade_pages(rebuilds: bool) -> None:
    """Deleting a connection deletes its streams, and a stream's pages are what it synced, so they
    go with it. Sqlite rebuilds the table to change a foreign key, which takes the page-revision
    triggers with it."""
    if not rebuilds:
        op.create_foreign_key(
            PAGE_SOURCE_FK, "page", "source", ["source_id"], ["id"], ondelete="CASCADE"
        )
        return
    _drop_page_triggers()
    with op.batch_alter_table("page", copy_from=_page_without_source_fk()) as batch:
        batch.create_foreign_key(
            PAGE_SOURCE_FK, "source", ["source_id"], ["id"], ondelete="CASCADE"
        )
    _create_page_triggers()


def downgrade() -> None:
    bind = op.get_bind()
    rebuilds = bind.dialect.name == "sqlite"
    _uncascade_pages(rebuilds)
    _widen_source()
    _return_sources_to_their_own_authority(bind, rebuilds)
    _restore_grant_conversation(bind, rebuilds)
    _narrow_connection(bind, rebuilds)
    _restore_source_authority()
    _restore_source_grants(bind)


def _uncascade_pages(rebuilds: bool) -> None:
    if not rebuilds:
        op.drop_constraint(PAGE_SOURCE_FK, "page", type_="foreignkey")
        op.create_foreign_key(PAGE_SOURCE_FK, "page", "source", ["source_id"], ["id"])
        return
    _drop_page_triggers()
    with op.batch_alter_table("page", copy_from=_page_without_source_fk()) as batch:
        batch.create_foreign_key(PAGE_SOURCE_FK, "source", ["source_id"], ["id"])
    _create_page_triggers()


def _widen_source() -> None:
    """The columns come back empty, so the rows below can be given their disclosure and their owner
    before either is constrained: the checks and the foreign keys over them are the last thing this
    direction does, once `connection` holds the key one of them points at."""
    with op.batch_alter_table("source", copy_from=SOURCE_ON_ITS_CONNECTION) as batch:
        batch.drop_column("consecutive_empty")
        batch.drop_index("source_authority")
        batch.drop_constraint(SOURCE_AUTHORITY_FK, type_="foreignkey")
        batch.add_column(
            sa.Column("subject", sa.Text(), nullable=False, server_default=SHARED_SUBJECT)
        )
        batch.add_column(sa.Column("owner_member_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column("connection_id", existing_type=sa.Uuid(), nullable=True)


def _return_sources_to_their_own_authority(bind: sa.Connection, rebuilds: bool) -> None:
    """Each source takes back the disclosure and the owner it reads off its connection, the account
    and tenant URL its config named, and the id that config hashes to. A stream of a connection this
    revision minted — `_minted_here` — returns to the direct path it came from: no connection, and
    where the handle was the workspace's or a member's, the sentinel handle that path spelled. Every
    such connection is deleted, since nothing but its streams ever named it and the revision before
    this one gives a connection no way to stand without an owner.

    A connection minted under a member owner for a broker account handle is not distinguishable here
    from the broker connection its streams were cut from, so those streams keep it and keep the id
    they were re-keyed to; a connection this revision unshared is not distinguishable from one never
    shared, so it stays unshared. Nothing recorded either act, and inventing a mark to read back
    would be a column the forward direction carries for no reader."""
    source, connection = _sources(), _connections()
    rows = (
        bind.execute(
            sa.select(
                source.c.id,
                source.c.workspace_id,
                source.c.backend,
                source.c.config,
                connection.c.id.label("connection_id"),
                connection.c.account_id,
                connection.c.base_url,
                connection.c.owner_member_id,
                connection.c.shared,
            )
            .select_from(source.join(connection, source.c.connection_id == connection.c.id))
            .order_by(source.c.id)
        )
        .mappings()
        .all()
    )
    citing = (_pages(), *_citing_tables(bind))
    if not rebuilds:
        op.drop_constraint(PAGE_SOURCE_FK, "page", type_="foreignkey")
    for row in rows:
        handle = row["account_id"]
        direct = _minted_here(handle, row["owner_member_id"])
        sentinel = handle == WORKSPACE_ACCOUNT or handle.startswith(MEMBER_PREFIX)
        config = _config(row["config"])
        if "stream" in config:
            config = {
                **config,
                "account": DIRECT_ACCOUNT if sentinel else handle,
                "base_url": row["base_url"],
            }
        connection_id = None if direct else row["connection_id"]
        was = _row_id(row["workspace_id"], row["backend"], config, connection_id)
        for table in citing:
            bind.execute(
                sa.update(table).where(table.c.source_id == row["id"]).values(source_id=was)
            )
        bind.execute(
            sa.update(source)
            .where(source.c.id == row["id"])
            .values(
                id=was,
                config=config,
                connection_id=connection_id,
                owner_member_id=row["owner_member_id"],
                subject=SHARED_SUBJECT if row["shared"] else f"member:{row['owner_member_id']}",
                claimed_by=None,
                claim_expires_at=None,
                updated_at=sa.func.now(),
            )
        )
    if not rebuilds:
        op.create_foreign_key(PAGE_SOURCE_FK, "page", "source", ["source_id"], ["id"])
    minted = [
        held.id
        for held in bind.execute(
            sa.select(connection.c.id, connection.c.account_id, connection.c.owner_member_id)
        )
        if _minted_here(held.account_id, held.owner_member_id)
    ]
    if minted:
        bind.execute(sa.delete(connection).where(connection.c.id.in_(minted)))


def _restore_grant_conversation(bind: sa.Connection, rebuilds: bool) -> None:
    if rebuilds:
        _drop_egress_triggers("connector_grant")
    with op.batch_alter_table("connector_grant") as batch:
        batch.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
    _stamp_a_conversation(bind, "connector_grant")
    with op.batch_alter_table("connector_grant") as batch:
        batch.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            GRANT_CONVERSATION_FK,
            "conversation",
            ["workspace_id", "conversation_id"],
            ["workspace_id", "id"],
        )
    if rebuilds:
        _create_egress_triggers("connector_grant")


def _narrow_connection(bind: sa.Connection, rebuilds: bool) -> None:
    if rebuilds:
        _drop_egress_triggers("connection")
    with op.batch_alter_table("connection") as batch:
        batch.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
    _stamp_a_conversation(bind, "connection")
    with op.batch_alter_table("connection") as batch:
        batch.drop_constraint("connection_backfill_days", type_="check")
        batch.drop_constraint("connection_shared", type_="check")
        batch.drop_column("backfill_days")
        batch.drop_column("base_url")
        batch.alter_column("owner_member_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("conversation_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_unique_constraint(
            "connection_owner_identity", ["workspace_id", "id", "owner_member_id"]
        )
        batch.create_foreign_key(
            CONNECTION_CONVERSATION_FK,
            "conversation",
            ["workspace_id", "conversation_id"],
            ["workspace_id", "id"],
        )
    if rebuilds:
        _create_egress_triggers("connection")


def _stamp_a_conversation(bind: sa.Connection, table_name: str) -> None:
    """Fill the restored `conversation_id` with each workspace's first conversation. Which
    conversation a connect happened in is not recoverable — this revision dropped the column because
    nothing read it — and the column is not null, so the shape returns and the fact does not."""
    table = sa.table(
        table_name,
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
    )
    conversation = sa.table(
        "conversation",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for (workspace_id,) in bind.execute(sa.select(table.c.workspace_id).distinct()):
        first = bind.execute(
            sa.select(conversation.c.id)
            .where(conversation.c.workspace_id == workspace_id)
            .order_by(conversation.c.created_at, conversation.c.id)
            .limit(1)
        ).scalar_one_or_none()
        if first is None:
            raise RuntimeError(
                f"workspace {workspace_id} has {table_name} rows and no conversation to name; "
                f"{table_name}.conversation_id is not null and cannot be restored"
            )
        bind.execute(
            sa.update(table)
            .where(table.c.workspace_id == workspace_id)
            .values(conversation_id=first)
        )


def _restore_source_authority() -> None:
    with op.batch_alter_table("source", copy_from=SOURCE_WITH_ITS_OWN_AUTHORITY) as batch:
        batch.create_check_constraint(
            "source_subject", "subject = 'shared' or subject like 'member:%'"
        )
        batch.create_check_constraint(
            "source_connection_owner", "connection_id is null or owner_member_id is not null"
        )
        batch.create_foreign_key(
            "source_owner_workspace_fkey",
            "member",
            ["workspace_id", "owner_member_id"],
            ["workspace_id", "id"],
        )
        batch.create_foreign_key(
            "source_connection_owner_fkey",
            "connection",
            ["workspace_id", "connection_id", "owner_member_id"],
            ["workspace_id", "id", "owner_member_id"],
        )


def _restore_source_grants(bind: sa.Connection) -> None:
    """Every agent holding a connector grant on a source's connection read that source's pages, so
    it takes the source grant the connector grant stood for."""
    op.create_table(
        "source_grant",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source.workspace_id", "source.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
        ),
        sa.PrimaryKeyConstraint("workspace_id", "source_id", "agent_id"),
    )
    source, grant = (
        _sources(),
        sa.table(
            "connector_grant",
            sa.column("workspace_id", sa.Uuid()),
            sa.column("agent_id", sa.Uuid()),
            sa.column("connection_id", sa.Uuid()),
        ),
    )
    source_grant = sa.table(
        "source_grant",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("source_id", sa.Uuid()),
        sa.column("agent_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind.execute(
        source_grant.insert().from_select(
            ["workspace_id", "source_id", "agent_id", "created_at", "updated_at"],
            sa.select(
                source.c.workspace_id, source.c.id, grant.c.agent_id, sa.func.now(), sa.func.now()
            ).select_from(
                source.join(
                    grant,
                    sa.and_(
                        grant.c.workspace_id == source.c.workspace_id,
                        grant.c.connection_id == source.c.connection_id,
                    ),
                )
            ),
        )
    )
