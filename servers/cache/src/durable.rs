use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use aws_sdk_s3::primitives::ByteStream;
use tokio::io::AsyncWriteExt;

static RESTORE_SEQ: AtomicU64 = AtomicU64::new(0);

/// Appended rather than `with_extension`, which would collapse `<hash>.meta` and `<hash>.body` onto
/// one `<hash>.restoring`, and sequenced so two restores never share a temp.
fn restore_temp(dest: &Path) -> PathBuf {
    let seq = RESTORE_SEQ.fetch_add(1, Ordering::Relaxed);
    let mut name = dest.as_os_str().to_owned();
    name.push(format!(".{}.{}.restoring", std::process::id(), seq));
    PathBuf::from(name)
}

/// The durable tier behind the local disk. Survives pod rolls and is shared across replicas, so a
/// cold daemon restores instead of re-fetching from origin. Durability is best-effort: every method
/// swallows and logs its own errors, because a durable-tier fault must never break serving from the
/// hot disk.
pub enum Durable {
    Off,
    Fs {
        root: PathBuf,
    },
    S3 {
        client: aws_sdk_s3::Client,
        bucket: String,
    },
}

impl Durable {
    /// `S3` when a bucket is named; else a local directory when one is named (dev/tests); else off.
    pub async fn from_env() -> Self {
        if let Ok(bucket) = std::env::var("UFO_CACHE_S3_BUCKET") {
            let cfg = aws_config::load_defaults(aws_config::BehaviorVersion::latest()).await;
            let mut builder = aws_sdk_s3::config::Builder::from(&cfg);
            // A custom endpoint (an S3-compatible store, or minio in tests) needs path-style
            // addressing — its host does not answer the per-bucket virtual hosts real S3 does.
            if let Ok(endpoint) = std::env::var("UFO_CACHE_S3_ENDPOINT") {
                builder = builder.endpoint_url(endpoint).force_path_style(true);
            }
            return Self::S3 {
                client: aws_sdk_s3::Client::from_conf(builder.build()),
                bucket,
            };
        }
        match std::env::var("UFO_CACHE_DURABLE_DIR") {
            Ok(dir) => Self::Fs {
                root: PathBuf::from(dir),
            },
            Err(_) => Self::Off,
        }
    }

    pub async fn exists(&self, key: &str) -> bool {
        match self {
            Self::Off => false,
            Self::Fs { root } => root.join(key).exists(),
            Self::S3 { client, bucket } => client
                .head_object()
                .bucket(bucket)
                .key(key)
                .send()
                .await
                .is_ok(),
        }
    }

    /// Fetch `key` into `dest`. Returns true when the object existed and was written.
    pub async fn get_file(&self, key: &str, dest: &Path) -> bool {
        match self {
            Self::Off => false,
            Self::Fs { root } => {
                let src = root.join(key);
                if !src.exists() {
                    return false;
                }
                copy_into(&src, dest).await
            }
            Self::S3 { client, bucket } => {
                let resp = client.get_object().bucket(bucket).key(key).send().await;
                match resp {
                    Ok(obj) => write_bytestream(obj.body, dest).await,
                    Err(e) => {
                        if !e.as_service_error().is_some_and(|s| s.is_no_such_key()) {
                            tracing::warn!(error = %e, key, "durable get failed");
                        }
                        false
                    }
                }
            }
        }
    }

    pub async fn put_file(&self, key: &str, src: &Path) {
        match self {
            Self::Off => {}
            Self::Fs { root } => {
                let dest = root.join(key);
                if let Some(parent) = dest.parent() {
                    let _ = tokio::fs::create_dir_all(parent).await;
                }
                copy_into(src, &dest).await;
            }
            Self::S3 { client, bucket } => match ByteStream::from_path(src).await {
                Ok(body) => {
                    if let Err(e) = client
                        .put_object()
                        .bucket(bucket)
                        .key(key)
                        .body(body)
                        .send()
                        .await
                    {
                        tracing::warn!(error = %e, key, "durable put failed");
                    }
                }
                Err(e) => tracing::warn!(error = %e, "durable put: read source failed"),
            },
        }
    }
}

/// Copy through a temp sibling + rename so a reader never sees a partial destination.
async fn copy_into(src: &Path, dest: &Path) -> bool {
    if let Some(parent) = dest.parent() {
        let _ = tokio::fs::create_dir_all(parent).await;
    }
    let tmp = restore_temp(dest);
    match tokio::fs::copy(src, &tmp).await {
        Ok(_) => tokio::fs::rename(&tmp, dest).await.is_ok(),
        Err(e) => {
            tracing::warn!(error = %e, "durable copy failed");
            false
        }
    }
}

async fn write_bytestream(body: ByteStream, dest: &Path) -> bool {
    if let Some(parent) = dest.parent() {
        let _ = tokio::fs::create_dir_all(parent).await;
    }
    let tmp = restore_temp(dest);
    let mut reader = body.into_async_read();
    let mut file = match tokio::fs::File::create(&tmp).await {
        Ok(f) => f,
        Err(e) => {
            tracing::warn!(error = %e, "durable restore: create failed");
            return false;
        }
    };
    if let Err(e) = tokio::io::copy(&mut reader, &mut file).await {
        tracing::warn!(error = %e, "durable restore: stream failed");
        return false;
    }
    let _ = file.flush().await;
    tokio::fs::rename(&tmp, dest).await.is_ok()
}
