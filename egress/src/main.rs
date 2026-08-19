use std::sync::Arc;

use ufo_egress::config::Config;
use ufo_egress::control::Control;
use ufo_egress::meter::Meter;
use ufo_egress::server::{EgressProxy, ServiceDaemons};
use ufo_egress::tls;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_env("UFO_EGRESS_LOG")
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let config = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            tracing::error!(error = %e, "invalid configuration");
            std::process::exit(2);
        }
    };

    let (ca_cert, ca_key) = match (&config.ca_cert, &config.ca_key) {
        (Some(cert), Some(key)) => (cert.clone(), key.clone()),
        _ => tls::generate_ca().expect("mint ephemeral CA"),
    };
    let leaves = tls::LeafStore::new(&ca_cert, &ca_key).expect("load egress CA");

    let control = Arc::new(Control::new(&config.control_url, &config.control_token));
    let meter = Meter::start(control.clone());
    let proxy = EgressProxy::new(
        control,
        Arc::new(leaves),
        meter.sink(),
        config.token_secret.clone(),
        ServiceDaemons {
            cache: config.cache_daemon.clone(),
            preview: config.preview_daemon.clone(),
        },
        config.public_url.clone(),
        config.graceful_shutdown,
    );
    tracing::info!(bind = %config.bind, "ufo-egress starting");
    // Kubernetes drains a pod with SIGTERM; a proxy that watched only SIGINT would ignore it and be
    // SIGKILLed after the grace window, cutting live tunnels rather than draining them.
    let shutdown = async {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("install SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = term.recv() => {}
        }
    };
    let result = proxy.serve(config.bind, shutdown).await;
    // The connection drain is done; drop the proxy so every meter sink but the batcher's own is
    // gone, then await the batcher so its last window posts before exit — a SIGTERM must not discard
    // queued egress and token records.
    drop(proxy);
    meter.shutdown().await;
    if let Err(error) = result {
        tracing::error!(error = %error, "serve failed");
        std::process::exit(1);
    }
}
