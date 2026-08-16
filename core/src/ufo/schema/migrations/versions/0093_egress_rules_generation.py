"""count wire-affecting mutations so the proxy's rule cache can pin what it derived from

The egress proxy caches each principal's derived rule set for a TTL, so a revoked grant, a share
flip, a disconnect, or a rotated key kept drawing its old rules for up to that TTL. The counter
makes the staleness observable: triggers bump it on every write to the three tables rules derive
from, and the proxy re-derives when a CONNECT's fresh read disagrees with the cached value. The
agent row is deliberately absent — `internet_access_allowed` is snapshotted per turn by design.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | None = None
depends_on: str | None = None

RULE_TABLES = ("connection", "connector_grant", "credential")

PG_FUNCTION = """
create function bump_egress_rules_generation() returns trigger as $$
begin
    update workspace
    set egress_rules_generation = egress_rules_generation + 1
    where id = coalesce(new.workspace_id, old.workspace_id);
    return coalesce(new, old);
end;
$$ language plpgsql
"""

PG_TRIGGER = """
create trigger {table}_bump_egress_rules
after insert or update or delete on "{table}"
for each row execute function bump_egress_rules_generation()
"""

SQLITE_TRIGGER = """
create trigger {table}_bump_egress_rules_{op}
after {op} on "{table}"
begin
    update workspace
    set egress_rules_generation = egress_rules_generation + 1
    where id = {row}.workspace_id;
end
"""


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("egress_rules_generation", sa.BigInteger(), nullable=False, server_default="0"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(PG_FUNCTION)
        for table in RULE_TABLES:
            op.execute(PG_TRIGGER.format(table=table))
        return
    for table in RULE_TABLES:
        for operation, row in (("insert", "new"), ("update", "new"), ("delete", "old")):
            op.execute(SQLITE_TRIGGER.format(table=table, op=operation, row=row))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in RULE_TABLES:
            op.execute(f'drop trigger {table}_bump_egress_rules on "{table}"')
        op.execute("drop function bump_egress_rules_generation()")
    else:
        for table in RULE_TABLES:
            for operation in ("insert", "update", "delete"):
                op.execute(f"drop trigger {table}_bump_egress_rules_{operation}")
    op.drop_column("workspace", "egress_rules_generation")
