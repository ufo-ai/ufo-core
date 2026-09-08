use std::net::SocketAddr;
use std::path::PathBuf;

use clap::{Parser, Subcommand};
use ufo_control::db;
use ufo_control::email::{
    apex_host, email_sender_from_env, invite_email, public_apex_host_from_env,
    DEFAULT_PUBLIC_BASE_URL, PUBLIC_BASE_URL_ENV,
};
use ufo_control::gateway::{
    parse_invite_required, router, stamped_script, GatewayState, Onboarding, CLIENT_BIN_DIR_ENV,
    GATEWAY_PORT_ENV, INVITE_REQUIRED_ENV, SIGNUP_KEY_ENV,
};
use ufo_control::invite::{InviteCodes, SignupProfile};
use ufo_control::invite_delivery::{self, InviteDeliveries, POLL_INTERVAL_SECONDS};
use ufo_control::rls::{
    bootstrap_policies, ensure_control_role, ensure_serve_role, owner_dsn, serve_dsn,
    PG_ROLE_SEED_ENV,
};
use ufo_control::schema::{require_control_schema, shape_control_schema};
use ufo_control::shared::{
    SharedWorkspaces, ONBOARD_CONTROL_TOKEN_ENV, SERVE_INTERNAL_URL_ENV, WORKSPACE_BASE_URL_ENV,
};
use ufo_control::slack_connect::{rearm_failed_delivery, slack_connect_from_env};
use ufo_control::store::OnboardStore;
use ufo_control::token::TOKEN_SECRET_ENV;
use ufo_control::{claim::ClaimWorkflow, directives::CLIENT_VERSION_ENV, workos};
use uuid::Uuid;

const DEFAULT_HOST: &str = "0.0.0.0";
const DEFAULT_PORT: u16 = 8080;
const OTLP_ENDPOINT_ENV: &str = "UFO_CONTROL_OTLP_ENDPOINT";
const SERVICE_NAME: &str = "ufo-control";

#[derive(Parser)]
#[command(name = SERVICE_NAME, about = "Operate the hosted shared-workspace service.")]
struct Cli {
    #[command(subcommand)]
    verb: Verb,
}

#[derive(Subcommand)]
enum Verb {
    Gateway,
    Migrate,
    Invite {
        email: String,
        #[arg(long)]
        business: Option<String>,
        #[arg(long)]
        goals: Option<String>,
        #[arg(long = "object", value_parser = positive_object)]
        object_number: Option<i32>,
    },
    #[command(name = "slack-connect-retry")]
    SlackConnectRetry {
        email_domain: String,
    },
    #[command(name = "invite-delivery-retry")]
    InviteDeliveryRetry {
        workspace_id: Uuid,
        email: String,
    },
    #[command(name = "rls-bootstrap")]
    RlsBootstrap,
    #[command(name = "serve-dsn")]
    ServeDsn {
        postgres_host: String,
        app_database: String,
    },
}

fn positive_object(raw: &str) -> Result<i32, String> {
    match raw.parse::<i32>() {
        Ok(number) if number >= 1 => Ok(number),
        _ => Err(format!(
            "{raw:?} is not a waitlist object number (1 or more)"
        )),
    }
}

#[tokio::main]
async fn main() -> std::process::ExitCode {
    install_logging();
    match run(Cli::parse()).await {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            std::process::ExitCode::FAILURE
        }
    }
}

fn install_logging() {
    use tracing_subscriber::layer::SubscriberExt;
    use tracing_subscriber::util::SubscriberInitExt;
    use tracing_subscriber::{fmt, EnvFilter};

    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,tokio_postgres=warn,rustls=warn"));
    tracing_subscriber::registry()
        .with(filter)
        .with(fmt::layer().json().with_target(true))
        .init();
    if let Ok(endpoint) = std::env::var(OTLP_ENDPOINT_ENV) {
        if !endpoint.is_empty() {
            tracing::info!(
                target: "ufo_control::main",
                "control.logs.otlp endpoint={}/v1/logs",
                endpoint.trim_end_matches('/')
            );
        }
    }
}

async fn run(cli: Cli) -> Result<(), String> {
    match cli.verb {
        Verb::Gateway => gateway().await,
        Verb::Migrate => migrate().await,
        Verb::Invite {
            email,
            business,
            goals,
            object_number,
        } => invite(email, business, goals, object_number).await,
        Verb::SlackConnectRetry { email_domain } => slack_connect_retry(email_domain).await,
        Verb::InviteDeliveryRetry {
            workspace_id,
            email,
        } => invite_delivery_retry(workspace_id, email).await,
        Verb::RlsBootstrap => rls_bootstrap().await,
        Verb::ServeDsn {
            postgres_host,
            app_database,
        } => {
            let dsn =
                serve_dsn(&postgres_host, &app_database).map_err(|error| error.to_string())?;
            println!("{dsn}");
            Ok(())
        }
    }
}

async fn gateway() -> Result<(), String> {
    let dsn = db::gateway_dsn_from_env().map_err(|error| error.to_string())?;
    let ca_bundle = db::ca_bundle_from_env().map(PathBuf::from);
    let token_secret = require_env(TOKEN_SECRET_ENV)?;
    let control_token = require_env(ONBOARD_CONTROL_TOKEN_ENV)?;
    let serve_internal_url = require_env(SERVE_INTERNAL_URL_ENV)?;
    let workspace_url = require_env(WORKSPACE_BASE_URL_ENV)?;
    let public_base_url =
        std::env::var(PUBLIC_BASE_URL_ENV).unwrap_or_else(|_| DEFAULT_PUBLIC_BASE_URL.to_string());
    let apex = apex_host(&public_base_url).map_err(|error| error.to_string())?;
    let verifier = workos::verifier_from_env().map_err(|error| error.to_string())?;
    let console_mode = workos::workos_console_mode().map_err(|error| error.to_string())?;
    let invite_required =
        parse_invite_required(&std::env::var(INVITE_REQUIRED_ENV).unwrap_or_default())?;
    if !invite_required {
        tracing::warn!(target: "ufo_control::main", "gateway.invite_gate.disabled");
    }
    let signup_key = std::env::var(SIGNUP_KEY_ENV)
        .ok()
        .filter(|key| !key.is_empty());
    if signup_key.is_some() {
        tracing::info!(target: "ufo_control::main", "gateway.signup_key.configured");
    }

    let pool = db::connect(&dsn, ca_bundle.as_deref())
        .await
        .map_err(|error| error.to_string())?;
    {
        let client = pool.get().await.map_err(|error| error.to_string())?;
        require_control_schema(&client)
            .await
            .map_err(|error| error.to_string())?;
    }
    let store = OnboardStore::new(pool.clone());
    let pool_for_slack = pool.clone();
    let pool_for_invites = pool.clone();
    let apex_for_slack = apex.clone();
    let apex_for_invites = apex.clone();
    let core_for_invites = SharedWorkspaces {
        workspace_url: workspace_url.clone(),
        serve_internal_url: serve_internal_url.clone(),
        control_token: control_token.clone(),
    };
    let sender = email_sender_from_env().map_err(|error| error.to_string())?;
    let state = GatewayState {
        onboarding: Onboarding {
            claims: ClaimWorkflow::new(store.clone(), verifier.clone()),
            store,
            workspaces: SharedWorkspaces {
                workspace_url,
                serve_internal_url,
                control_token,
            },
            invites: InviteCodes::new(pool),
            verifier,
            token_secret,
            apex_host: apex,
            invite_required,
            signup_key,
        },
        stamped_script: stamped_script(&public_base_url),
        client_bin_dir: std::env::var(CLIENT_BIN_DIR_ENV).ok(),
        client_version: std::env::var(CLIENT_VERSION_ENV).unwrap_or_default(),
        console_mode,
    };

    let worker_id = format!(
        "{}.{}",
        hostname::get()
            .ok()
            .and_then(|name| name.into_string().ok())
            .unwrap_or_else(|| "gateway".to_string()),
        std::process::id()
    );
    match slack_connect_from_env(pool_for_slack, apex_for_slack, worker_id.clone()) {
        Ok(Some(inviter)) => {
            tokio::spawn(inviter.run());
        }
        Ok(None) => tracing::info!(target: "ufo_control::main", "gateway.slack_connect.disabled"),
        Err(error) => return Err(error.to_string()),
    }

    tokio::spawn(
        InviteDeliveries {
            pool: pool_for_invites,
            core: core_for_invites,
            sender,
            apex_host: apex_for_invites,
            worker_id,
            poll_interval: std::time::Duration::from_secs(POLL_INTERVAL_SECONDS),
        }
        .run(),
    );

    let port: u16 = std::env::var(GATEWAY_PORT_ENV)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(DEFAULT_PORT);
    let address: SocketAddr = format!("{DEFAULT_HOST}:{port}")
        .parse()
        .map_err(|_| format!("{DEFAULT_HOST}:{port} is not an address"))?;
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .map_err(|error| format!("could not bind {address}: {error}"))?;
    tracing::info!(target: "ufo_control::main", "gateway.listening address={address}");
    axum::serve(listener, router(state))
        .with_graceful_shutdown(shutdown())
        .await
        .map_err(|error| error.to_string())
}

async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}

async fn migrate() -> Result<(), String> {
    let dsn = owner_dsn().map_err(|error| error.to_string())?;
    let mut client = owner_client(&dsn).await?;
    shape_control_schema(&mut client)
        .await
        .map_err(|error| error.to_string())?;
    println!("control schema at head");
    Ok(())
}

async fn invite(
    email: String,
    business: Option<String>,
    goals: Option<String>,
    object_number: Option<i32>,
) -> Result<(), String> {
    if business.is_some() != goals.is_some() {
        return Err("--business and --goals are given together or not".to_string());
    }
    let profile = match (business, goals) {
        (Some(business), Some(goals)) => Some(SignupProfile { business, goals }),
        _ => None,
    };
    let apex = public_apex_host_from_env().map_err(|error| error.to_string())?;
    let workspace_url = require_env(WORKSPACE_BASE_URL_ENV)?;
    let dsn = db::gateway_dsn_from_env().map_err(|error| error.to_string())?;
    {
        let client = ledger_client(&dsn).await?;
        require_control_schema(&client)
            .await
            .map_err(|error| error.to_string())?;
    }
    let sender = email_sender_from_env().map_err(|error| error.to_string())?;
    let pool = db::connect(&dsn, db::ca_bundle_from_env().map(PathBuf::from).as_deref())
        .await
        .map_err(|error| error.to_string())?;
    let minted = InviteCodes::new(pool)
        .mint(object_number, &email, profile.as_ref())
        .await
        .map_err(|error| error.to_string())?;

    let expires = minted.expires_at.format("%Y-%m-%d %H:%M");
    let approved = match minted.object_number {
        Some(number) => format!("object #{number}"),
        None => minted.email.clone(),
    };
    let message = invite_email(&minted.email, minted.expires_at, &apex, &workspace_url)
        .map_err(|error| error.to_string())?;
    if let Err(error) = sender
        .send(
            &minted.email,
            &message.subject,
            &message.text,
            Some(&message.html),
        )
        .await
    {
        return Err(format!(
            "{} is granted, but the invitation could not be emailed ({error}); the grant stands \
             — tell them to run the installer",
            minted.email
        ));
    }
    println!(
        "{approved} granted to {}, expires {expires} UTC",
        minted.email
    );
    Ok(())
}

async fn slack_connect_retry(email_domain: String) -> Result<(), String> {
    let domain = email_domain.trim().to_lowercase();
    let dsn = db::gateway_dsn_from_env().map_err(|error| error.to_string())?;
    {
        let client = ledger_client(&dsn).await?;
        require_control_schema(&client)
            .await
            .map_err(|error| error.to_string())?;
    }
    let pool = db::connect(&dsn, db::ca_bundle_from_env().map(PathBuf::from).as_deref())
        .await
        .map_err(|error| error.to_string())?;
    let failed_at = rearm_failed_delivery(&pool, &domain)
        .await
        .map_err(|error| error.to_string())?;
    match failed_at {
        None => Err(format!("no failed slack connect delivery for {domain}")),
        Some(failed_at) => {
            println!(
                "slack connect delivery for {domain} re-armed, failed since {} UTC",
                failed_at.format("%Y-%m-%d %H:%M")
            );
            Ok(())
        }
    }
}

async fn invite_delivery_retry(workspace_id: Uuid, email: String) -> Result<(), String> {
    let recipient = email.trim().to_lowercase();
    let dsn = db::gateway_dsn_from_env().map_err(|error| error.to_string())?;
    {
        let client = ledger_client(&dsn).await?;
        require_control_schema(&client)
            .await
            .map_err(|error| error.to_string())?;
    }
    let pool = db::connect(&dsn, db::ca_bundle_from_env().map(PathBuf::from).as_deref())
        .await
        .map_err(|error| error.to_string())?;
    let failed_at = invite_delivery::rearm_failed_delivery(&pool, workspace_id, &recipient)
        .await
        .map_err(|error| error.to_string())?;
    match failed_at {
        None => Err(format!(
            "no failed invitation delivery for {recipient} in {workspace_id}"
        )),
        Some(failed_at) => {
            println!(
                "invitation delivery for {recipient} in {workspace_id} re-armed, failed since \
                 {} UTC",
                failed_at.format("%Y-%m-%d %H:%M")
            );
            Ok(())
        }
    }
}

async fn rls_bootstrap() -> Result<(), String> {
    require_env(PG_ROLE_SEED_ENV)?;
    let dsn = owner_dsn().map_err(|error| error.to_string())?;
    let mut client = owner_client(&dsn).await?;
    bootstrap_policies(&mut client)
        .await
        .map_err(|error| error.to_string())?;
    ensure_serve_role(&client)
        .await
        .map_err(|error| error.to_string())?;
    ensure_control_role(&client)
        .await
        .map_err(|error| error.to_string())?;
    println!("rls policies at head");
    Ok(())
}

async fn owner_client(dsn: &str) -> Result<tokio_postgres::Client, String> {
    connect(dsn).await
}

async fn ledger_client(dsn: &str) -> Result<tokio_postgres::Client, String> {
    connect(dsn).await
}

async fn connect(dsn: &str) -> Result<tokio_postgres::Client, String> {
    db::client(dsn, db::ca_bundle_from_env().map(PathBuf::from).as_deref())
        .await
        .map_err(|error| error.to_string())
}

fn require_env(name: &str) -> Result<String, String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{name} is unset — required by the gateway"))
}
