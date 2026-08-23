//! Exercises the `Durable::S3` adapter against a real S3-compatible store (minio in CI). The rest of
//! the suite tests the durable tier against a filesystem backend; this proves the S3 code paths
//! (`put_object`, `get_object`, `head_object`, streamed restore) that only a live endpoint reaches.
//! Skips when `UFO_TEST_S3_ENDPOINT` is unset, so `cargo test` stays green without a running minio.

use ufo_cache::durable::Durable;

fn endpoint() -> Option<String> {
    std::env::var("UFO_TEST_S3_ENDPOINT")
        .ok()
        .filter(|value| !value.is_empty())
}

async fn ensure_bucket(endpoint: &str, bucket: &str) {
    let cfg = aws_config::load_defaults(aws_config::BehaviorVersion::latest()).await;
    let client = aws_sdk_s3::Client::from_conf(
        aws_sdk_s3::config::Builder::from(&cfg)
            .endpoint_url(endpoint)
            .force_path_style(true)
            .build(),
    );
    // Idempotent: a re-run finds the bucket already owned, which is not an error for this test.
    let _ = client.create_bucket().bucket(bucket).send().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn s3_durable_put_get_and_absence_round_trip() {
    let Some(endpoint) = endpoint() else {
        eprintln!("skipping s3_durable: UFO_TEST_S3_ENDPOINT unset");
        return;
    };
    let bucket = "ufo-cache-test";
    ensure_bucket(&endpoint, bucket).await;
    std::env::set_var("UFO_CACHE_S3_BUCKET", bucket);
    std::env::set_var("UFO_CACHE_S3_ENDPOINT", &endpoint);
    let durable = Durable::from_env().await;

    let tmp = tempfile::tempdir().unwrap();
    let src = tmp.path().join("bundle");
    std::fs::write(&src, b"DURABLE-BUNDLE-BYTES").unwrap();
    let key = "git/public/github.com/octocat/Hello-World.bundle";

    durable.put_file(key, &src).await;
    assert!(
        durable.exists(key).await,
        "the put object must be reported present"
    );

    let restored = tmp.path().join("restored");
    assert!(
        durable.get_file(key, &restored).await,
        "get_file must restore the object"
    );
    assert_eq!(std::fs::read(&restored).unwrap(), b"DURABLE-BUNDLE-BYTES");

    // An absent key is a clean miss (the cold-clone fallback), not an error.
    let missing = tmp.path().join("missing");
    assert!(
        !durable
            .get_file("git/public/github.com/absent/repo.bundle", &missing)
            .await
    );
    assert!(!missing.exists());
    assert!(
        !durable
            .exists("git/public/github.com/absent/repo.bundle")
            .await
    );
}
