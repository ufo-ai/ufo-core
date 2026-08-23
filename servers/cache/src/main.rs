use ufo_cache::durable::Durable;
use ufo_cache::Config;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_env("UFO_CACHE_LOG")
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
    tracing::info!(listen = %config.listen, state = ?config.state_root, "ufo-cache starting");

    let listener = match tokio::net::TcpListener::bind(config.listen).await {
        Ok(l) => l,
        Err(e) => {
            tracing::error!(error = %e, "bind failed");
            std::process::exit(1);
        }
    };
    let durable = Durable::from_env().await;
    axum::serve(listener, ufo_cache::app(&config, durable))
        .with_graceful_shutdown(shutdown())
        .await
        .expect("server error");
}

async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}
