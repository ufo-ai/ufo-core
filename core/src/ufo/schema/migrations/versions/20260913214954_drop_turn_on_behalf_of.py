import sqlalchemy as sa
from alembic import op

revision: str = "20260913214954"
down_revision: str | None = "20260913193910"
branch_labels: str | None = None
depends_on: str | None = None

CONNECTION_SCOPE_MAX = 50
TRIGGER = "turn_capability_scope"
FUNCTION = "ufo_scope_turn_capabilities"


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
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(sa.text(f"drop trigger {TRIGGER} on turn"))
        op.execute(sa.text(f"drop function {FUNCTION}()"))
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_authority", type_="check")
        batch.drop_constraint("turn_on_behalf_of_member_id_fkey", type_="foreignkey")
        batch.drop_column("on_behalf_of_member_id")


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.add_column(sa.Column("on_behalf_of_member_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "turn_on_behalf_of_member_id_fkey", "member", ["on_behalf_of_member_id"], ["id"]
        )
        batch.create_check_constraint(
            "turn_authority",
            "speaker_member_id is null or on_behalf_of_member_id is null",
        )
    if op.get_bind().dialect.name == "postgresql":
        _postgres_scope_trigger()
