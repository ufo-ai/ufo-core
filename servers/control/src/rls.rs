use sha2::{Digest, Sha256};
use tokio_postgres::Client;

pub const POSTGRES_OWNER_DSN_ENV: &str = "UFO_CONTROL_POSTGRES_OWNER_DSN";
pub const PG_ROLE_SEED_ENV: &str = "UFO_CONTROL_PG_ROLE_SEED";

pub const ALEMBIC_VERSION_TABLE: &str = "alembic_version";
pub const WORKSPACE_TABLE: &str = "workspace";
pub const WORKSPACE_COLUMN: &str = "workspace_id";
pub const POLICY_NAME: &str = "ufo_workspace_rls";
pub const WORKSPACE_GUC: &str = "app.workspace_id";

pub const OWNER_ROLE: &str = "ufo_owner";
pub const SERVE_ROLE: &str = "ufo_serve";
pub const CONTROL_ROLE: &str = "ufo_control";
pub const CONTROL_SCHEMA: &str = "ufo_control";

pub const LOCK_TIMEOUT: &str = "10s";
pub const IDLE_IN_TRANSACTION_TIMEOUT: &str = "60s";

#[derive(Debug, thiserror::Error)]
pub enum RlsError {
    #[error("{0} is unset — no Postgres owner DSN for RLS")]
    MissingOwnerDsn(&'static str),
    #[error("{0} is unset — cannot derive the shared Postgres roles")]
    MissingSeed(&'static str),
    #[error("lock on table {table:?} timed out after {LOCK_TIMEOUT}; held by: {holders}")]
    LockTimeout { table: String, holders: String },
    #[error(
        "table {0:?} is neither the workspace table nor workspace-scoped \
         (no {WORKSPACE_COLUMN} column)"
    )]
    Unscoped(String),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

pub fn owner_dsn() -> Result<String, RlsError> {
    std::env::var(POSTGRES_OWNER_DSN_ENV)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or(RlsError::MissingOwnerDsn(POSTGRES_OWNER_DSN_ENV))
}

fn seed() -> Result<String, RlsError> {
    std::env::var(PG_ROLE_SEED_ENV)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or(RlsError::MissingSeed(PG_ROLE_SEED_ENV))
}

pub fn role_password(seed: &str, role: &str) -> String {
    format!("{:x}", Sha256::digest(format!("{seed}:{role}").as_bytes()))
}

pub fn serve_password() -> Result<String, RlsError> {
    Ok(role_password(&seed()?, SERVE_ROLE))
}

pub fn control_password() -> Result<String, RlsError> {
    Ok(role_password(&seed()?, CONTROL_ROLE))
}

pub fn serve_dsn(postgres_host: &str, app_database: &str) -> Result<String, RlsError> {
    Ok(format!(
        "postgresql+asyncpg://{SERVE_ROLE}:{}@{postgres_host}/{app_database}",
        serve_password()?
    ))
}

pub fn control_dsn(postgres_host: &str, app_database: &str) -> Result<String, RlsError> {
    Ok(format!(
        "postgresql://{CONTROL_ROLE}:{}@{postgres_host}/{app_database}",
        control_password()?
    ))
}

pub async fn ensure_serve_role(client: &Client) -> Result<(), RlsError> {
    let password = serve_password()?;
    client
        .batch_execute(&format!("set lock_timeout = '{LOCK_TIMEOUT}'"))
        .await?;
    ensure_login_role(client, SERVE_ROLE, &password).await?;
    client
        .batch_execute(&format!(
            "grant \"{SERVE_ROLE}\" to \"{OWNER_ROLE}\" with set true, inherit false"
        ))
        .await?;
    for role in [SERVE_ROLE, OWNER_ROLE] {
        client
            .batch_execute(&format!(
                "alter role \"{role}\" set idle_in_transaction_session_timeout = \
                 '{IDLE_IN_TRANSACTION_TIMEOUT}'"
            ))
            .await?;
    }
    grant_serve_role(client).await?;
    let app_database: String = client
        .query_one("select current_database()", &[])
        .await?
        .get(0);
    ensure_database(client, &format!("{app_database}_dbos"), SERVE_ROLE).await?;
    Ok(())
}

pub async fn ensure_control_role(client: &Client) -> Result<(), RlsError> {
    let password = control_password()?;
    client
        .batch_execute(&format!("set lock_timeout = '{LOCK_TIMEOUT}'"))
        .await?;
    ensure_login_role(client, CONTROL_ROLE, &password).await?;
    client
        .batch_execute(&format!(
            "grant \"{CONTROL_ROLE}\" to \"{OWNER_ROLE}\" with set true, inherit false"
        ))
        .await?;
    client
        .batch_execute(&format!(
            "alter role \"{CONTROL_ROLE}\" set idle_in_transaction_session_timeout = \
             '{IDLE_IN_TRANSACTION_TIMEOUT}'"
        ))
        .await?;
    client
        .batch_execute(&format!(
            "create schema if not exists {CONTROL_SCHEMA}; \
             grant usage on schema {CONTROL_SCHEMA} to \"{CONTROL_ROLE}\"; \
             grant select, insert, update, delete on all tables in schema {CONTROL_SCHEMA} \
               to \"{CONTROL_ROLE}\"; \
             alter default privileges for role \"{OWNER_ROLE}\" in schema {CONTROL_SCHEMA} \
               grant select, insert, update, delete on tables to \"{CONTROL_ROLE}\""
        ))
        .await?;
    Ok(())
}

async fn ensure_login_role(client: &Client, role: &str, password: &str) -> Result<(), RlsError> {
    let exists = client
        .query_opt("select 1 from pg_roles where rolname = $1", &[&role])
        .await?;
    let verb = if exists.is_some() { "alter" } else { "create" };
    client
        .batch_execute(&format!(
            "{verb} role \"{role}\" with login password '{password}'"
        ))
        .await?;
    Ok(())
}

async fn grant_serve_role(client: &Client) -> Result<(), RlsError> {
    client
        .batch_execute(&format!(
            "alter default privileges for role \"{OWNER_ROLE}\" in schema public \
               revoke select, insert, update, delete on tables from \"{SERVE_ROLE}\"; \
             alter default privileges for role \"{OWNER_ROLE}\" in schema public \
               revoke usage on sequences from \"{SERVE_ROLE}\"; \
             grant usage on schema public to \"{SERVE_ROLE}\"; \
             grant select, insert, update, delete on all tables in schema public \
               to \"{SERVE_ROLE}\"; \
             grant usage on all sequences in schema public to \"{SERVE_ROLE}\""
        ))
        .await?;
    Ok(())
}

async fn ensure_database(client: &Client, name: &str, owner: &str) -> Result<(), RlsError> {
    let exists = client
        .query_opt("select 1 from pg_database where datname = $1", &[&name])
        .await?;
    if exists.is_none() {
        client
            .batch_execute(&format!("create database \"{name}\" owner \"{owner}\""))
            .await?;
    }
    Ok(())
}

pub async fn bootstrap_policies(client: &mut Client) -> Result<(), RlsError> {
    client
        .batch_execute(&format!("set lock_timeout = '{LOCK_TIMEOUT}'"))
        .await?;
    let tables: Vec<String> = client
        .query(
            "select tablename from pg_tables where schemaname = 'public' order by tablename",
            &[],
        )
        .await?
        .iter()
        .map(|row| row.get("tablename"))
        .collect();
    for table in tables {
        if table == ALEMBIC_VERSION_TABLE {
            continue;
        }
        match conformant(client, &table).await {
            Ok(true) => continue,
            Ok(false) => {}
            Err(error) => return Err(lock_aware(client, &table, error).await),
        }
        let transaction = client.transaction().await?;
        let column = scope_column(transaction.client(), &table).await?;
        let predicate = format!("{column} = current_setting('{WORKSPACE_GUC}')::uuid");
        let applied = transaction
            .batch_execute(&format!(
                "alter table \"{table}\" enable row level security; \
                 drop policy if exists {POLICY_NAME} on \"{table}\"; \
                 create policy {POLICY_NAME} on \"{table}\" \
                   using ({predicate}) with check ({predicate})"
            ))
            .await;
        match applied {
            Ok(()) => transaction.commit().await?,
            Err(error) => {
                drop(transaction);
                return Err(lock_aware(client, &table, RlsError::Query(error)).await);
            }
        }
    }
    Ok(())
}

async fn lock_aware(client: &Client, table: &str, error: RlsError) -> RlsError {
    let RlsError::Query(query) = &error else {
        return error;
    };
    if query.code() != Some(&tokio_postgres::error::SqlState::LOCK_NOT_AVAILABLE) {
        return error;
    }
    RlsError::LockTimeout {
        table: table.to_string(),
        holders: lock_holders(client, table).await,
    }
}

async fn conformant(client: &Client, table: &str) -> Result<bool, RlsError> {
    let row = client
        .query_opt(
            "select rel.relrowsecurity, pol.qual, pol.with_check, pol.permissive, pol.roles, \
                    pol.cmd \
             from pg_class rel join pg_namespace ns on ns.oid = rel.relnamespace \
             left join pg_policies pol on pol.schemaname = ns.nspname \
               and pol.tablename = rel.relname and pol.policyname = $2 \
             where ns.nspname = 'public' and rel.relname = $1",
            &[&table, &POLICY_NAME],
        )
        .await?;
    let Some(row) = row else {
        return Ok(false);
    };
    let enabled: bool = row.get("relrowsecurity");
    let qual: Option<String> = row.get("qual");
    let (Some(qual), true) = (qual, enabled) else {
        return Ok(false);
    };
    let column = scope_column(client, table).await?;
    let expected = format!("({column} = (current_setting('{WORKSPACE_GUC}'::text))::uuid)");
    let with_check: Option<String> = row.get("with_check");
    let permissive: Option<String> = row.get("permissive");
    let roles: Option<Vec<String>> = row.get("roles");
    let cmd: Option<String> = row.get("cmd");
    Ok(qual == expected
        && with_check.as_deref() == Some(expected.as_str())
        && permissive.as_deref() == Some("PERMISSIVE")
        && cmd.as_deref() == Some("ALL")
        && roles.as_deref() == Some(&["public".to_string()][..]))
}

async fn lock_holders(client: &Client, table: &str) -> String {
    let rows = client
        .query(
            "select stat.pid, stat.usename, stat.state, now() - stat.xact_start as xact_age, \
                    left(stat.query, 200) as query \
             from pg_locks locks join pg_stat_activity stat on stat.pid = locks.pid \
             where locks.relation = $1::regclass and locks.granted \
               and locks.pid <> pg_backend_pid()",
            &[&table],
        )
        .await
        .unwrap_or_default();
    let described: Vec<String> = rows
        .iter()
        .map(|row| {
            let pid: i32 = row.get("pid");
            let role: Option<String> = row.get("usename");
            let state: Option<String> = row.get("state");
            let query: Option<String> = row.get("query");
            format!(
                "pid={pid} role={} state={:?} query={:?}",
                role.unwrap_or_default(),
                state.unwrap_or_default(),
                query.unwrap_or_default()
            )
        })
        .collect();
    if described.is_empty() {
        "(no holder visible)".to_string()
    } else {
        described.join("; ")
    }
}

async fn scope_column(client: &Client, table: &str) -> Result<String, RlsError> {
    if table == WORKSPACE_TABLE {
        return Ok("id".to_string());
    }
    let has_column = client
        .query_opt(
            "select 1 from information_schema.columns \
             where table_schema = 'public' and table_name = $1 and column_name = $2",
            &[&table, &WORKSPACE_COLUMN],
        )
        .await?;
    if has_column.is_none() {
        return Err(RlsError::Unscoped(table.to_string()));
    }
    Ok(WORKSPACE_COLUMN.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_role_password_is_derived_from_the_seed_and_the_role_name() {
        let seed = "ufo-local-dev";
        let serve = role_password(seed, SERVE_ROLE);
        let control = role_password(seed, CONTROL_ROLE);
        assert_eq!(serve.len(), 64);
        assert_ne!(
            serve, control,
            "one seed derives a distinct password per role"
        );
        assert_eq!(serve, role_password(seed, SERVE_ROLE), "and it is stable");
        assert_ne!(serve, role_password("other-seed", SERVE_ROLE));
    }
}
