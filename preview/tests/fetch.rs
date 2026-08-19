use std::time::Duration;

use axum::routing::{get, put};
use ufo_preview::fetch::{fetch_source, put_result};
use ufo_preview::refusal::Refusal;

async fn serve(app: axum::Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{addr}")
}

#[tokio::test]
async fn fetches_within_cap() {
    let base = serve(axum::Router::new().route("/doc", get(|| async { "hello bytes" }))).await;
    let got = fetch_source(&format!("{base}/doc"), 1024, true, Duration::from_secs(5))
        .await
        .unwrap();
    assert_eq!(got, b"hello bytes");
}

#[tokio::test]
async fn oversize_body_is_too_large() {
    let base = serve(axum::Router::new().route("/big", get(|| async { "x".repeat(4096) }))).await;
    let err = fetch_source(&format!("{base}/big"), 100, true, Duration::from_secs(5))
        .await
        .unwrap_err();
    assert!(matches!(err, Refusal::TooLarge(_)));
}

#[tokio::test]
async fn redirect_is_refused() {
    let base = serve(axum::Router::new().route(
        "/moved",
        get(|| async { axum::response::Redirect::temporary("https://example.com/") }),
    ))
    .await;
    let err = fetch_source(&format!("{base}/moved"), 1024, true, Duration::from_secs(5))
        .await
        .unwrap_err();
    assert!(matches!(err, Refusal::FetchRefused(_)));
}

#[tokio::test]
async fn puts_bytes_with_media_type() {
    let (tx, rx) = tokio::sync::oneshot::channel::<(String, Vec<u8>)>();
    let tx = std::sync::Arc::new(std::sync::Mutex::new(Some(tx)));
    let base = serve(axum::Router::new().route(
        "/sink",
        put(
            move |headers: axum::http::HeaderMap, body: axum::body::Bytes| {
                let tx = tx.clone();
                async move {
                    let ct = headers
                        .get("content-type")
                        .unwrap()
                        .to_str()
                        .unwrap()
                        .to_string();
                    tx.lock()
                        .unwrap()
                        .take()
                        .unwrap()
                        .send((ct, body.to_vec()))
                        .unwrap();
                    "ok"
                }
            },
        ),
    ))
    .await;
    put_result(
        &format!("{base}/sink"),
        b"\x89PNGdata".to_vec(),
        "image/png",
        true,
        Duration::from_secs(5),
    )
    .await
    .unwrap();
    let (ct, body) = rx.await.unwrap();
    assert_eq!(ct, "image/png");
    assert_eq!(body, b"\x89PNGdata");
}

#[tokio::test]
async fn non_success_status_refuses_fetch() {
    let base = serve(axum::Router::new().route(
        "/broken",
        get(|| async { (axum::http::StatusCode::INTERNAL_SERVER_ERROR, "boom") }),
    ))
    .await;
    let err = fetch_source(
        &format!("{base}/broken"),
        1024,
        true,
        Duration::from_secs(5),
    )
    .await
    .unwrap_err();
    assert!(matches!(err, Refusal::FetchRefused(_)));
}

#[tokio::test]
async fn non_success_status_refuses_put() {
    let base = serve(axum::Router::new().route(
        "/broken",
        put(|| async { (axum::http::StatusCode::INTERNAL_SERVER_ERROR, "boom") }),
    ))
    .await;
    let err = put_result(
        &format!("{base}/broken"),
        b"data".to_vec(),
        "application/octet-stream",
        true,
        Duration::from_secs(5),
    )
    .await
    .unwrap_err();
    assert!(matches!(err, Refusal::FetchRefused(_)));
}
