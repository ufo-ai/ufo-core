#![allow(dead_code)]

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

pub struct Exchange {
    pub path: String,
    pub body: String,
    pub authorization: Option<String>,
}

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

pub static ROLE_LOCK: std::sync::LazyLock<tokio::sync::Mutex<()>> =
    std::sync::LazyLock::new(|| tokio::sync::Mutex::new(()));

pub struct FreshDatabase {
    pub name: String,
    pub dsn: String,
}

impl FreshDatabase {
    pub async fn client(&self) -> tokio_postgres::Client {
        db::client(&self.dsn, None)
            .await
            .expect("the fresh database is reachable")
    }
}

impl FreshDatabase {
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
