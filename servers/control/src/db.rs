//! The pool over control's own `ufo_control` schema, and nothing else.
//!
//! The role this DSN names is granted the `ufo_control` schema and no privilege on any table in
//! `public`, so a query that strays outside control's own ledgers fails as `permission denied`
//! rather than reading a tenant's rows. Core's tables are reached over the onboarding RPC instead.
//!
//! TLS follows the DSN's own `sslmode`, which `tokio-postgres` parses and honours. The default is
//! `prefer`, and `prefer` is not "TLS if it works": once the server answers the SSLRequest, a failed
//! handshake is a connection error with no fallback to plaintext (`connect_tls.rs`). A managed
//! instance always answers it, and its certificate is signed by a CA in no public root store, so
//! `UFO_CONTROL_PG_CA_BUNDLE` is what makes the connection possible at all there — the control image
//! ships the bundle and sets it. A local Postgres that offers no TLS falls back cleanly and needs
//! none of this.

use std::path::Path;
use std::sync::Arc;

use deadpool_postgres::{Manager, ManagerConfig, Pool, RecyclingMethod};
use rustls::{ClientConfig, RootCertStore};
use tokio_postgres::Config;
use tokio_postgres_rustls::MakeRustlsConnect;

pub const GATEWAY_DSN_ENV: &str = "UFO_CONTROL_GATEWAY_DSN";
pub const PG_CA_BUNDLE_ENV: &str = "UFO_CONTROL_PG_CA_BUNDLE";
pub const GATEWAY_POOL_MAX_SIZE: usize = 4;

#[derive(Debug, thiserror::Error)]
pub enum DbError {
    #[error("{0} is unset — the gateway reads its own ledgers through it")]
    MissingDsn(&'static str),
    #[error("the database DSN is not usable: {0}")]
    BadDsn(#[from] tokio_postgres::Error),
    #[error("the CA bundle at {path} is unreadable: {source}")]
    CaBundle {
        path: String,
        source: std::io::Error,
    },
    #[error("the CA bundle at {0} holds no certificate")]
    EmptyCaBundle(String),
    #[error("the pool could not be built: {0}")]
    Pool(#[from] deadpool_postgres::BuildError),
}

/// Build the gateway's pool. `max_size` is small on purpose: control's queries are short ledger
/// reads and writes on the sign-in path, and every pod's ceiling is counted against the fleet's
/// share of the instance.
pub async fn connect(dsn: &str, ca_bundle: Option<&Path>) -> Result<Pool, DbError> {
    let config: Config = dsn.parse()?;
    let tls = MakeRustlsConnect::new(client_config(ca_bundle)?);
    let manager = Manager::from_config(
        config,
        tls,
        ManagerConfig {
            recycling_method: RecyclingMethod::Fast,
        },
    );
    Ok(Pool::builder(manager)
        .max_size(GATEWAY_POOL_MAX_SIZE)
        .build()?)
}

fn client_config(ca_bundle: Option<&Path>) -> Result<ClientConfig, DbError> {
    let mut roots = RootCertStore {
        roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
    };
    if let Some(path) = ca_bundle {
        let pem = std::fs::read(path).map_err(|source| DbError::CaBundle {
            path: path.display().to_string(),
            source,
        })?;
        let mut added = 0;
        for certificate in rustls_pemfile::certs(&mut pem.as_slice()).flatten() {
            if roots.add(certificate).is_ok() {
                added += 1;
            }
        }
        if added == 0 {
            return Err(DbError::EmptyCaBundle(path.display().to_string()));
        }
    }
    Ok(
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .expect("ring supports the default protocol versions")
            .with_root_certificates(roots)
            .with_no_client_auth(),
    )
}

/// One connection for the deploy-time verbs. `migrate` and `rls-bootstrap` issue DDL as the owner
/// and exit, so they take a connection rather than a pool. The driver task is detached: it lives
/// exactly as long as the client it serves, and dropping the client ends it.
pub async fn client(
    dsn: &str,
    ca_bundle: Option<&Path>,
) -> Result<tokio_postgres::Client, DbError> {
    let tls = MakeRustlsConnect::new(client_config(ca_bundle)?);
    let (client, connection) = tokio_postgres::connect(dsn, tls).await?;
    tokio::spawn(async move {
        if let Err(error) = connection.await {
            tracing::warn!(target: "ufo_control::db", "database connection ended: {error}");
        }
    });
    Ok(client)
}

/// The gateway's DSN and CA bundle, read once at the composition root so a misconfigured deploy
/// fails at startup rather than on a member's first sign-in.
pub fn gateway_dsn_from_env() -> Result<String, DbError> {
    std::env::var(GATEWAY_DSN_ENV)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or(DbError::MissingDsn(GATEWAY_DSN_ENV))
}

pub fn ca_bundle_from_env() -> Option<String> {
    std::env::var(PG_CA_BUNDLE_ENV)
        .ok()
        .filter(|value| !value.is_empty())
}
