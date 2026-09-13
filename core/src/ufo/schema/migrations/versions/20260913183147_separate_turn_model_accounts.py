"""Persist exact turn capabilities outside optional runtime choices."""

import json
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260913183147"
down_revision: str | None = "20260913173609"
branch_labels: str | None = None
depends_on: str | None = None

MODEL_SLOTS = (
    ("anthropic", "anthropic_api_key"),
    ("openai", "openai_api_key"),
)
CONNECTION_SCOPE_MAX = 50
TRIGGER = "turn_capability_scope"
FUNCTION = "ufo_scope_turn_capabilities"


def _sqlite_backfill(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "select id, workspace_id, agent_id, on_behalf_of_member_id, runtime_config from turn"
        )
    ).mappings()
    for row in rows:
        config = None if row.runtime_config is None else json.loads(row.runtime_config)
        embedded_accounts = [] if config is None else config.pop("model_accounts", [])
        member = row.on_behalf_of_member_id
        accounts = embedded_accounts
        if member is not None:
            member_id = str(UUID(str(member)))
            accounts = []
            for provider, key_slot in MODEL_SLOTS:
                slot = f"{key_slot}:member:{member_id}"
                if connection.scalar(
                    sa.text(
                        "select count(*) from credential where workspace_id = :workspace_id "
                        "and slot = :slot"
                    ),
                    {
                        "workspace_id": row.workspace_id,
                        "slot": slot,
                    },
                ):
                    accounts.append({"provider": provider, "slot": slot})
            eligible = tuple(
                str(UUID(str(value)))
                for value in connection.execute(
                    sa.text(
                        "select connection.id from connection join connector_grant on "
                        "connector_grant.workspace_id = connection.workspace_id and "
                        "connector_grant.connection_id = connection.id where "
                        "connection.workspace_id = :workspace_id and "
                        "connector_grant.agent_id = :agent_id and "
                        "(connection.shared or connection.owner_member_id = :member_id) "
                        "order by connection.id"
                    ),
                    {
                        "workspace_id": row.workspace_id,
                        "agent_id": row.agent_id,
                        "member_id": member,
                    },
                ).scalars()
            )
            config = {} if config is None else config
            existing = config.get("connections")
            config["connections"] = (
                list(eligible[:CONNECTION_SCOPE_MAX])
                if existing is None
                else sorted(set(existing) & set(eligible))[:CONNECTION_SCOPE_MAX]
            )
        connection.execute(
            sa.text(
                "update turn set runtime_config = :runtime_config, "
                "model_accounts = :model_accounts where id = :id"
            ),
            {
                "id": row.id,
                "runtime_config": None if config is None else json.dumps(config),
                "model_accounts": json.dumps(accounts),
            },
        )


def _postgres_scope_trigger() -> None:
    op.execute(
        sa.text(
            f"""
            create function {FUNCTION}() returns trigger language plpgsql as $$
            declare
                config jsonb;
                scoped_connections jsonb;
            begin
                if NEW.runtime_config is null or NEW.runtime_config::jsonb = 'null'::jsonb then
                    config := '{{}}'::jsonb;
                else
                    config := NEW.runtime_config::jsonb;
                end if;
                if NEW.on_behalf_of_member_id is not null then
                    select coalesce(
                        jsonb_agg(
                            jsonb_build_object('provider', account.provider, 'slot', account.slot)
                            order by account.provider
                        ),
                        '[]'::jsonb
                    ) into NEW.model_accounts
                    from (
                        select provider, key_slot || ':member:' ||
                            NEW.on_behalf_of_member_id::text as slot
                        from (values
                            ('anthropic', 'anthropic_api_key'),
                            ('openai', 'openai_api_key')
                        ) as supported(provider, key_slot)
                        where exists (
                            select 1 from credential
                            where credential.workspace_id = NEW.workspace_id
                            and credential.slot = key_slot || ':member:' ||
                                NEW.on_behalf_of_member_id::text
                        )
                    ) as account;
                    if config ? 'connections'
                        and jsonb_typeof(config -> 'connections') = 'array' then
                        select coalesce(
                            jsonb_agg(to_jsonb(candidate.id) order by candidate.id),
                            '[]'::jsonb
                        ) into scoped_connections
                        from (
                            select distinct held.id
                            from jsonb_array_elements_text(config -> 'connections') as held(id)
                            join connection on connection.id::text = held.id
                            join connector_grant on
                                connector_grant.workspace_id = connection.workspace_id
                                and connector_grant.connection_id = connection.id
                            where connection.workspace_id = NEW.workspace_id
                            and connector_grant.agent_id = NEW.agent_id
                            and (
                                connection.shared
                                or connection.owner_member_id = NEW.on_behalf_of_member_id
                            )
                            order by held.id
                            limit {CONNECTION_SCOPE_MAX}
                        ) as candidate;
                    else
                        select coalesce(
                            jsonb_agg(to_jsonb(candidate.id) order by candidate.id),
                            '[]'::jsonb
                        ) into scoped_connections
                        from (
                            select connection.id::text as id
                            from connection
                            join connector_grant on
                                connector_grant.workspace_id = connection.workspace_id
                                and connector_grant.connection_id = connection.id
                            where connection.workspace_id = NEW.workspace_id
                            and connector_grant.agent_id = NEW.agent_id
                            and (
                                connection.shared
                                or connection.owner_member_id = NEW.on_behalf_of_member_id
                            )
                            order by connection.id::text
                            limit {CONNECTION_SCOPE_MAX}
                        ) as candidate;
                    end if;
                    config := jsonb_set(config, '{{connections}}', scoped_connections, true);
                elsif config ? 'model_accounts' then
                    NEW.model_accounts := coalesce(config -> 'model_accounts', '[]'::jsonb)::json;
                end if;
                config := config - 'model_accounts';
                if (NEW.runtime_config is null or NEW.runtime_config::jsonb = 'null'::jsonb)
                    and NEW.on_behalf_of_member_id is null
                    and config = '{{}}'::jsonb then
                    NEW.runtime_config := null;
                else
                    NEW.runtime_config := config::json;
                end if;
                return NEW;
            end;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            create trigger {TRIGGER}
            before insert or update of on_behalf_of_member_id, runtime_config, model_accounts
            on turn for each row execute function {FUNCTION}()
            """
        )
    )


def upgrade() -> None:
    op.add_column(
        "turn",
        sa.Column(
            "model_accounts",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        _postgres_scope_trigger()
        op.execute(sa.text("update turn set runtime_config = runtime_config"))
    else:
        _sqlite_backfill(connection)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(sa.text(f"drop trigger {TRIGGER} on turn"))
        op.execute(sa.text(f"drop function {FUNCTION}()"))
        op.execute(
            sa.text(
                "update turn set runtime_config = case "
                "when model_accounts::jsonb = '[]'::jsonb then runtime_config "
                "else (coalesce(runtime_config::jsonb, '{}'::jsonb) || "
                "jsonb_build_object('model_accounts', model_accounts::jsonb))::json end"
            )
        )
    else:
        op.execute(
            sa.text(
                "update turn set runtime_config = json_set(coalesce(runtime_config, '{}'), "
                "'$.model_accounts', json(model_accounts)) where model_accounts <> '[]'"
            )
        )
    op.drop_column("turn", "model_accounts")
