// Shared by every integration binary; each uses the part it needs, so the unused rest is not
// dead code but another binary's.
#![allow(dead_code)]

//! A real Postgres for the ledger suites.
//!
//! Control's ledgers hold their invariants in partial unique indexes, row locks, and
//! `for update skip locked` — none of which a fake reproduces — so every one of these tests runs
//! against the database itself. `UFO_CONTROL_TEST_POSTGRES` names it; CI starts the service and
//! points this at it, and a developer runs `make test-control-pg` for the same container.
//!
//! Each test gets its own database rather than a truncate between tests. Cargo runs the tests in a
//! binary concurrently and the binaries concurrently with each other, so a shared schema would let
//! one test's truncate land inside another's transaction — and the invariants under test here are
//! exactly the ones that show up as cross-test interference. A fresh database costs about a tenth
//! of a second and removes the whole class.

use deadpool_postgres::Pool;
use ufo_control::db;
use ufo_control::schema::shape_control_schema;
use uuid::Uuid;

pub const TEST_POSTGRES_ENV: &str = "UFO_CONTROL_TEST_POSTGRES";
pub const DEFAULT_TEST_DSN: &str = "postgresql://ufo:ufo@127.0.0.1:5549/ufo";

pub fn admin_dsn() -> String {
    std::env::var(TEST_POSTGRES_ENV).unwrap_or_else(|_| DEFAULT_TEST_DSN.to_string())
}

fn with_database(dsn: &str, database: &str) -> String {
    let (head, _) = dsn.rsplit_once('/').expect("a DSN names its database");
    format!("{head}/{database}")
}

/// One request a local server captured, and what it answered.
pub struct Exchange {
    pub path: String,
    pub body: String,
    pub authorization: Option<String>,
}

/// A local HTTP server answering each connection with the next canned response, and recording what
/// it was asked. Every outbound call in this crate is tested against one of these rather than a
/// stubbed client: the bytes on the wire are what the far side accepts or refuses, so they are what
/// the test asserts.
pub async fn spawn_http(
    responses: Vec<(u16, String)>,
) -> (String, std::sync::Arc<std::sync::Mutex<Vec<Exchange>>>) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let log = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let sink = std::sync::Arc::clone(&log);
    tokio::spawn(async move {
        for (status, payload) in responses {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let mut raw = Vec::new();
            let mut chunk = [0_u8; 4096];
            loop {
                let read = match stream.read(&mut chunk).await {
                    Ok(0) | Err(_) => break,
                    Ok(read) => read,
                };
                raw.extend_from_slice(&chunk[..read]);
                let text = String::from_utf8_lossy(&raw).to_string();
                if let Some((head, body)) = text.split_once("\r\n\r\n") {
                    let length = head
                        .lines()
                        .find_map(|line| {
                            let (name, value) = line.split_once(':')?;
                            name.trim()
                                .eq_ignore_ascii_case("content-length")
                                .then(|| value.trim().parse::<usize>().ok())?
                        })
                        .unwrap_or(0);
                    if body.len() >= length {
                        break;
                    }
                }
            }
            let text = String::from_utf8_lossy(&raw).to_string();
            let (head, body) = text.split_once("\r\n\r\n").unwrap_or((text.as_str(), ""));
            let path = head
                .lines()
                .next()
                .and_then(|line| line.split_whitespace().nth(1))
                .unwrap_or_default()
                .to_string();
            let authorization = head.lines().find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.trim()
                    .eq_ignore_ascii_case("authorization")
                    .then(|| value.trim().to_string())
            });
            sink.lock().unwrap().push(Exchange {
                path,
                body: body.to_string(),
                authorization,
            });
            let reply = format!(
                "HTTP/1.1 {status} X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                payload.len()
            );
            let _ = stream.write_all(reply.as_bytes()).await;
            let _ = stream.flush().await;
        }
    });
    (base, log)
}

/// A Postgres role is cluster-wide while a database is not, so every test in this binary that
/// creates or alters `ufo_serve` / `ufo_control` is writing the same rows. Cargo runs them
/// concurrently, so they take this in turn — the shared object is real, and serializing the tests
/// that touch it is the honest fix rather than hoping the writes interleave harmlessly.
pub static ROLE_LOCK: std::sync::LazyLock<tokio::sync::Mutex<()>> =
    std::sync::LazyLock::new(|| tokio::sync::Mutex::new(()));

/// A database of this test's own, with no schema shaped in it. The RLS suite needs one: it creates
/// its own tenant-shaped tables and roles, and `create database` cannot run inside a transaction.
pub struct FreshDatabase {
    pub name: String,
    pub dsn: String,
}

impl FreshDatabase {
    /// A connection as the owning superuser — what the deploy's migrate Job holds.
    pub async fn client(&self) -> tokio_postgres::Client {
        db::client(&self.dsn, None)
            .await
            .expect("the fresh database is reachable")
    }
}

impl FreshDatabase {
    /// The owner role the deploy's DSN names. A Postgres role is cluster-wide while a database is
    /// not, so every test in this binary reaches the same `ufo_owner` — creating it unconditionally
    /// would fail whichever test lost the race. The role's state is identical either way, so the
    /// winner creating it is enough.
    pub async fn ensure_owner_role(&self, client: &tokio_postgres::Client) {
        client
            .batch_execute(
                "do $$ begin                    if not exists (select 1 from pg_roles where rolname = 'ufo_owner') then                      create role ufo_owner;                    end if;                  exception when duplicate_object then null;                  end $$",
            )
            .await
            .expect("the owner role exists");
    }
}

pub async fn fresh_database() -> FreshDatabase {
    let admin = admin_dsn();
    let name = format!("ufo_ctl_{}", Uuid::new_v4().simple());
    let admin_client = db::client(&admin, None)
        .await
        .expect("the test database is reachable — start it with `make test-control-pg`");
    admin_client
        .batch_execute(&format!("create database {name}"))
        .await
        .expect("the test database is created");
    let dsn = with_database(&admin, &name);
    FreshDatabase { name, dsn }
}

/// A pool over a database of this test's own, freshly brought to head.
///
/// The schema is shaped rather than assumed: `shape_control_schema` is the verb the deploy runs, so
/// exercising it here is what proves a fresh database lands on the columns the build declares.
pub async fn ledger_pool() -> Pool {
    let admin = admin_dsn();
    let name = format!("ufo_ctl_{}", Uuid::new_v4().simple());
    let admin_pool = db::connect(&admin, None)
        .await
        .expect("the test database is reachable — start it with `make test-control-pg`");
    admin_pool
        .get()
        .await
        .expect("a connection")
        .batch_execute(&format!("create database {name}"))
        .await
        .expect("the test database is created");

    let pool = db::connect(&with_database(&admin, &name), None)
        .await
        .expect("the fresh database is reachable");
    let mut client = pool.get().await.expect("a connection");
    shape_control_schema(&mut client)
        .await
        .expect("the schema shapes");
    drop(client);
    pool
}
