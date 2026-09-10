use std::collections::{BTreeMap, BTreeSet};

use tokio_postgres::Client;

use crate::store::SCHEMA;
use crate::{campaign, invite, invite_delivery, slack_connect, store};

pub const LEDGERS: &[&str] = &[
    store::TABLE,
    invite::TABLE,
    slack_connect::TABLE,
    invite_delivery::TABLE,
    campaign::TABLE,
    campaign::RECIPIENT_TABLE,
    campaign::EVENT_TABLE,
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

pub fn ddl() -> Vec<String> {
    let mut statements = vec![format!("create schema if not exists {SCHEMA}")];
    for group in [
        store::DDL,
        invite::DDL,
        slack_connect::DDL,
        invite_delivery::DDL,
        campaign::DDL,
    ] {
        statements.extend(group.iter().map(|statement| statement.to_string()));
    }
    statements
}

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
        format!(
            "alter table {} add column if not exists signup_subject text",
            store::TABLE
        ),
        format!(
            "alter table {} add column if not exists signup_subject text",
            invite::TABLE
        ),
        format!(
            "update {} set signup_subject = email_domain where signup_subject is null",
            store::TABLE
        ),
        format!(
            "update {} set signup_subject = email_domain where signup_subject is null",
            invite::TABLE
        ),
        "create or replace function ufo_control.fill_signup_subject() returns trigger \
         language plpgsql as $$ begin if new.signup_subject is null then \
         new.signup_subject := new.email_domain; end if; return new; end $$"
            .to_string(),
        format!(
            "drop trigger if exists onboard_claim_signup_subject on {}; \
             create trigger onboard_claim_signup_subject before insert on {} for each row \
             execute function ufo_control.fill_signup_subject()",
            store::TABLE,
            store::TABLE
        ),
        format!(
            "drop trigger if exists invite_code_signup_subject on {}; \
             create trigger invite_code_signup_subject before insert on {} for each row \
             execute function ufo_control.fill_signup_subject()",
            invite::TABLE,
            invite::TABLE
        ),
        format!(
            "alter table {} alter column signup_subject set not null",
            store::TABLE
        ),
        format!(
            "alter table {} alter column signup_subject set not null",
            invite::TABLE
        ),
        format!(
            "drop index if exists ufo_control.invite_code_live_domain; \
             create unique index if not exists {} on {} \
             (signup_subject) where consumed_at is null",
            invite::LIVE_SUBJECT_INDEX,
            invite::TABLE
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
        assert_eq!(shape.len(), 7, "one entry per ledger");
        let claim = &shape[store::TABLE];
        assert!(claim.contains("created_workspace"), "{claim:?}");
        assert!(claim.contains("invite_id"), "{claim:?}");
        assert!(claim.contains("resulting_workspace_id"), "{claim:?}");
    }

    #[test]
    fn a_check_constraints_own_commas_do_not_split_a_definition() {
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
