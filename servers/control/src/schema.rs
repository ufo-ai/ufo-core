//! The `ufo_control` schema, shaped once per deploy.
//!
//! `create schema | table | index if not exists` is not atomic against a concurrent creator: two
//! callers issuing it together both find the object absent, both issue it, and the loser raises a
//! `pg_class`/`pg_namespace` unique violation. So the schema is shaped by one process before any
//! replica starts — `ufo-control migrate`, an initContainer of the `ufo-migrate` Job the gateway
//! Deployment waits on — and no replica issues DDL. `require_control_schema` is the other end: a
//! gateway that finds a ledger absent says which verb shapes it and refuses to serve, never
//! creating it under a request.
//!
//! The verb does not lean on that topology for correctness. A duplicated Job pod or an operator
//! running it by hand mid-deploy is a second caller, and the Job's `backoffLimit: 0` gives a raced
//! loser no second chance, so the statements run under `SHAPE_LOCK` — the writer that waits then
//! finds everything present and writes nothing. Every statement is `if not exists` and the reshape
//! is conditional, so the verb is a no-op on a database already at head.

use std::collections::{BTreeMap, BTreeSet};

use tokio_postgres::Client;

use crate::store::SCHEMA;
use crate::{invite, invite_delivery, slack_connect, store};

pub const LEDGERS: &[&str] = &[
    store::TABLE,
    invite::TABLE,
    slack_connect::TABLE,
    invite_delivery::TABLE,
];

pub const SHAPE_LOCK: &str = "select pg_advisory_xact_lock(hashtext('ufo_control schema'))";

const CREATE_TABLE: &str = "create table if not exists ";
const TABLE_CONSTRAINTS: &[&str] = &[
    "primary",
    "unique",
    "check",
    "foreign",
    "exclude",
    "constraint",
];

const LIVE_COLUMNS: &str = "select table_name, column_name from information_schema.columns \
                            where table_schema = $1 and table_name = any($2::text[])";

/// Every statement that brings an empty database to head, in order.
pub fn ddl() -> Vec<String> {
    let mut statements = vec![format!("create schema if not exists {SCHEMA}")];
    for group in [
        store::DDL,
        invite::DDL,
        slack_connect::DDL,
        invite_delivery::DDL,
    ] {
        statements.extend(group.iter().map(|statement| statement.to_string()));
    }
    statements
}

/// What a `create table if not exists` cannot do: a table already there keeps the shape it was
/// created with, so every column added after its first deploy arrives here. Each statement is
/// idempotent, so the reshape is a no-op on a database at head.
pub fn reshape() -> Vec<String> {
    vec![
        format!(
            "alter table {} drop column if exists code_hash, drop column if exists attempts",
            store::TABLE
        ),
        format!(
            "alter table {} alter column object_number drop not null",
            invite::TABLE
        ),
        format!(
            "alter table {} add column if not exists business text, \
             add column if not exists goals text, drop column if exists role, \
             drop column if exists member_name, drop column if exists company, \
             drop column if exists use_case",
            invite::TABLE
        ),
        format!(
            "alter table {} add column if not exists created_workspace boolean not null \
             default false",
            store::TABLE
        ),
        format!(
            "alter table {} add column if not exists invite_id uuid",
            store::TABLE
        ),
    ]
}

#[derive(Debug, thiserror::Error)]
pub enum SchemaError {
    #[error("{0} is absent — run `ufo-control migrate` before starting the gateway")]
    LedgerAbsent(String),
    #[error(
        "the shaped database does not carry {0}. A create statement adds no column to a table \
         already there. Add `alter table ... add column if not exists` to the reshape for each one."
    )]
    ShapedDrift(String),
    #[error(
        "the database does not carry {0} — run `ufo-control migrate` from this build before \
         starting the gateway"
    )]
    Drift(String),
    #[error(transparent)]
    Query(#[from] tokio_postgres::Error),
}

/// The comma-separated definitions of one column list, keeping a `check (state in ('a', 'b'))`
/// whole rather than splitting it on the commas inside its own parentheses.
fn definitions(body: &str) -> Vec<String> {
    let mut parts = vec![String::new()];
    let mut depth = 0_i32;
    for character in body.chars() {
        if character == ')' && depth == 0 {
            break;
        }
        match character {
            '(' => depth += 1,
            ')' => depth -= 1,
            _ => {}
        }
        if character == ',' && depth == 0 {
            parts.push(String::new());
            continue;
        }
        parts
            .last_mut()
            .expect("one part always exists")
            .push(character);
    }
    parts
}

/// The columns every `create table if not exists` declares, by table — the head shape read off the
/// only place that states it.
///
/// A create statement writes nothing to a table already there, so a column added to one reaches an
/// existing database through the reshape alone. A column no reshape statement adds stays absent
/// from every database shaped before it, and the first request that reads it fails under a member
/// rather than in the deploy.
pub fn head_shape() -> BTreeMap<String, BTreeSet<String>> {
    let mut shape = BTreeMap::new();
    for statement in ddl() {
        let Some(rest) = statement.strip_prefix(CREATE_TABLE) else {
            continue;
        };
        let Some((table, body)) = rest.split_once('(') else {
            continue;
        };
        let columns = definitions(body)
            .iter()
            .filter_map(|definition| definition.split_whitespace().next().map(str::to_string))
            .filter(|first| !TABLE_CONSTRAINTS.contains(&first.as_str()))
            .collect();
        shape.insert(table.trim().to_string(), columns);
    }
    shape
}

/// Every declared column the database does not carry, qualified and sorted.
async fn drifted_columns(client: &Client) -> Result<Vec<String>, SchemaError> {
    let shape = head_shape();
    let bare: Vec<String> = shape
        .keys()
        .map(|table| {
            table
                .split_once('.')
                .map(|(_, name)| name.to_string())
                .unwrap_or_default()
        })
        .collect();
    let rows = client.query(LIVE_COLUMNS, &[&SCHEMA, &bare]).await?;
    let mut live: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for row in rows {
        let table: String = row.get("table_name");
        let column: String = row.get("column_name");
        live.entry(table).or_default().insert(column);
    }
    let mut drifted = Vec::new();
    for (table, declared) in &shape {
        let bare = table
            .split_once('.')
            .map(|(_, name)| name)
            .unwrap_or_default();
        let held = live.get(bare).cloned().unwrap_or_default();
        for column in declared.difference(&held) {
            drifted.push(format!("{table}.{column}"));
        }
    }
    drifted.sort();
    Ok(drifted)
}

/// Bring the whole schema to head in one transaction, one writer at a time.
///
/// The last act holds the shaped database to `head_shape`, which is the whole class of reshape
/// omission rather than one column: a column added to a create statement and forgotten there
/// reaches no database that already holds its table. The check runs inside the transaction, so a
/// database the reshape cannot carry to head keeps everything it had — the Job fails naming every
/// missing column, and the gateway Deployment it gates never rolls.
pub async fn shape_control_schema(client: &mut Client) -> Result<(), SchemaError> {
    let transaction = client.transaction().await?;
    transaction.batch_execute(SHAPE_LOCK).await?;
    for statement in ddl() {
        transaction.batch_execute(&statement).await?;
    }
    for statement in reshape() {
        transaction.batch_execute(&statement).await?;
    }
    let drifted = drifted_columns(transaction.client()).await?;
    if !drifted.is_empty() {
        return Err(SchemaError::ShapedDrift(drifted.join(", ")));
    }
    transaction.commit().await?;
    Ok(())
}

/// A replica serves only against the shape its own build declares. A ledger absent names the verb
/// that shapes it. A ledger present but missing a column this build declares earns the same
/// refusal: the build and the database disagree, and every path that reads the column fails one
/// request at a time otherwise — a background sweep, where nobody is watching, included.
pub async fn require_control_schema(client: &Client) -> Result<(), SchemaError> {
    for table in LEDGERS {
        let present: Option<String> = client
            .query_one("select to_regclass($1)::text", &[table])
            .await?
            .get(0);
        if present.is_none() {
            return Err(SchemaError::LedgerAbsent((*table).to_string()));
        }
    }
    let drifted = drifted_columns(client).await?;
    if !drifted.is_empty() {
        return Err(SchemaError::Drift(drifted.join(", ")));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_head_shape_reads_every_declared_column() {
        let shape = head_shape();
        assert_eq!(shape.len(), 4, "one entry per ledger");
        let claim = &shape[store::TABLE];
        assert!(claim.contains("created_workspace"), "{claim:?}");
        assert!(claim.contains("invite_id"), "{claim:?}");
        assert!(claim.contains("resulting_workspace_id"), "{claim:?}");
    }

    #[test]
    fn a_check_constraints_own_commas_do_not_split_a_definition() {
        // `check (state in ('pending', 'claimed', ...))` must stay one definition, or `'claimed'`
        // reads as a column name and the head shape demands a column nothing declares.
        let shape = head_shape();
        let delivery = &shape[slack_connect::TABLE];
        assert!(delivery.contains("state"), "{delivery:?}");
        for state in DELIVERY_STATES_QUOTED {
            assert!(!delivery.contains(*state), "{state} leaked in as a column");
        }
    }

    const DELIVERY_STATES_QUOTED: &[&str] = &["'claimed'", "'delivered'", "'failed'"];

    #[test]
    fn no_declared_column_is_a_table_constraint_keyword() {
        for (table, columns) in head_shape() {
            for column in columns {
                assert!(
                    !TABLE_CONSTRAINTS.contains(&column.as_str()),
                    "{table}.{column} is a constraint, not a column"
                );
            }
        }
    }
}
