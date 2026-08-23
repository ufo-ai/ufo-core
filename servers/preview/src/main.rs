use std::sync::Arc;

use ufo_preview::Config;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_env("UFO_PREVIEW_LOG")
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
    tracing::info!(listen = %config.listen, "ufo-preview starting");

    let listener = match tokio::net::TcpListener::bind(config.listen).await {
        Ok(l) => l,
        Err(e) => {
            tracing::error!(error = %e, "bind failed");
            std::process::exit(1);
        }
    };
    axum::serve(listener, ufo_preview::app(Arc::new(config)))
        .with_graceful_shutdown(shutdown())
        .await
        .expect("server error");
}

async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}
