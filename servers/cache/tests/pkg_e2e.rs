mod common;

use std::path::PathBuf;
use std::sync::atomic::Ordering;

use ufo_cache::durable::Durable;
use ufo_cache::Config;

fn config(state: PathBuf, pkg_hosts: Vec<String>) -> Config {
    Config {
        listen: "127.0.0.1:0".parse().unwrap(),
        state_root: state,
        control_url: "http://127.0.0.1:1".into(),
        control_token: "test".into(),
        disk_limit_bytes: 1 << 30,
        upstream_scheme: "http".into(),
        allowed_git_hosts: vec![],
        allowed_pkg_hosts: pkg_hosts,
        pkg_disk_limit_bytes: 1 << 30,
        git_fresh_ttl_secs: 0,
        pack_cache_bytes: 0,
        lfs_cache_bytes: 0,
    }
}

fn client() -> reqwest::Client {
    reqwest::Client::builder().no_proxy().build().unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_immutable_artifact_is_fetched_once_then_served_from_cache() {
    let tmp = tempfile::tempdir().unwrap();
    let (origin, hits) = common::registry();
    let origin_addr = common::spawn(origin).await;
    let daemon = common::spawn(ufo_cache::app(
        &config(tmp.path().join("state"), vec![origin_addr.to_string()]),
        Durable::Off,
    ))
    .await;
    let url = format!("http://{daemon}/pkg/{origin_addr}/pkg/thing.tgz");
    let client = client();

    let r1 = client.get(&url).send().await.unwrap();
    assert_eq!(r1.status(), 200);
    assert_eq!(r1.headers()["x-ufo-cache"], "MISS");
    assert_eq!(r1.text().await.unwrap(), "TARBALL");

    let r2 = client.get(&url).send().await.unwrap();
    assert_eq!(r2.status(), 200);
    assert_eq!(r2.headers()["x-ufo-cache"], "HIT");
    assert_eq!(r2.text().await.unwrap(), "TARBALL");

    assert_eq!(
        hits.load(Ordering::SeqCst),
        1,
        "the immutable artifact must be fetched from origin exactly once"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_cold_misses_for_one_artifact_both_serve_the_correct_body() {
    // Two overlapping cold misses for one URL used to share a fixed temp path, so one truncated the
    // other mid-write and committed a corrupt body. Each download now writes a unique temp.
    let tmp = tempfile::tempdir().unwrap();
    let (origin, _hits) = common::registry();
    let origin_addr = common::spawn(origin).await;
    let daemon = common::spawn(ufo_cache::app(
        &config(tmp.path().join("state"), vec![origin_addr.to_string()]),
        Durable::Off,
    ))
    .await;
    let url = format!("http://{daemon}/pkg/{origin_addr}/slow.tgz");
    let client = client();

    let (a, b) = tokio::join!(client.get(&url).send(), client.get(&url).send());
    let a = a.unwrap();
    let b = b.unwrap();
    assert_eq!(a.status(), 200);
    assert_eq!(b.status(), 200);
    assert_eq!(a.text().await.unwrap(), "SLOW-TARBALL-BODY");
    assert_eq!(b.text().await.unwrap(), "SLOW-TARBALL-BODY");

    // The committed entry is the whole body, and a third read serves it from cache.
    let third = client.get(&url).send().await.unwrap();
    assert_eq!(third.headers()["x-ufo-cache"], "HIT");
    assert_eq!(third.text().await.unwrap(), "SLOW-TARBALL-BODY");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_no_store_document_is_never_cached() {
    let tmp = tempfile::tempdir().unwrap();
    let (origin, hits) = common::registry();
    let origin_addr = common::spawn(origin).await;
    let daemon = common::spawn(ufo_cache::app(
        &config(tmp.path().join("state"), vec![origin_addr.to_string()]),
        Durable::Off,
    ))
    .await;
    let url = format!("http://{daemon}/pkg/{origin_addr}/meta.json");
    let client = client();

    for _ in 0..2 {
        let resp = client.get(&url).send().await.unwrap();
        assert_eq!(resp.status(), 200);
        assert_eq!(resp.headers()["x-ufo-cache"], "MISS");
        assert_eq!(resp.text().await.unwrap(), "META");
    }
    assert_eq!(
        hits.load(Ordering::SeqCst),
        2,
        "a no-store document must reach origin on every request"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_host_off_the_allowlist_is_refused() {
    let tmp = tempfile::tempdir().unwrap();
    let daemon = common::spawn(ufo_cache::app(
        &config(tmp.path().join("state"), vec!["registry.npmjs.org".into()]),
        Durable::Off,
    ))
    .await;
    let resp = client()
        .get(format!(
            "http://{daemon}/pkg/169.254.169.254/latest/meta-data"
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        404,
        "a host the daemon does not front must be refused, not fetched"
    );
}
