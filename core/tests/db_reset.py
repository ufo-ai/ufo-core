"""The workspace tables in foreign-key-safe delete order — the shared truncation order the `db`
fixture and the CLI e2e's bootstrap both reset through. It lives in a uniquely-named module, not in
`conftest`, so every test directory imports the same one: a bare `from conftest import` resolves
ambiguously the moment a sibling `integration/conftest.py` is also on the collection path."""

from selfhost.schema import tables

DELETE_ORDER = (
    tables.scheduled_task,
    tables.runtime_instance,
    tables.grant,
    tables.proposal,
    tables.spend_cap,
    tables.ledger,
    tables.writeback,
    tables.shared_artifact,
    tables.turn,
    tables.conversation,
    tables.surface_identity,
    tables.agent,
    tables.ext_store,
    tables.credential,
    tables.page,
    tables.source,
    tables.member,
    tables.workspace,
)
