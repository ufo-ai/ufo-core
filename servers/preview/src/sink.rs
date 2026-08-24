use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;

use crate::config::Config;
use crate::fetch;
use crate::refusal::Refusal;
use crate::render::{Rendered, SinkSpec};

pub async fn deliver(
    rendered: Rendered,
    sink: &SinkSpec,
    cfg: &Config,
) -> Result<Response, Refusal> {
    let headers = meta_headers(&rendered);
    match sink {
        SinkSpec::Inline { inline: true } => Ok((
            StatusCode::OK,
            headers_with_type(headers, rendered.media_type),
            rendered.bytes,
        )
            .into_response()),
        SinkSpec::Inline { inline: false } => {
            Err(Refusal::UnsupportedType("sink inline must be true".into()))
        }
        SinkSpec::Bundle { bundle: true } => Ok((
            StatusCode::OK,
            headers_with_type(headers, rendered.media_type),
            rendered.bytes,
        )
            .into_response()),
        SinkSpec::Bundle { bundle: false } => {
            Err(Refusal::UnsupportedType("sink bundle must be true".into()))
        }
        SinkSpec::PutUrl { put_url } => {
            let body = serde_json::json!({
                "width": rendered.width,
                "height": rendered.height,
                "page_count": rendered.page_count,
                "size_bytes": rendered.bytes.len() as u64,
                "sha256": &rendered.sha256,
            });
            fetch::put_result(
                put_url,
                rendered.bytes,
                rendered.media_type,
                cfg.allow_local,
                cfg.fetch_timeout,
            )
            .await?;
            Ok((StatusCode::OK, headers, Json(body)).into_response())
        }
    }
}

fn meta_headers(r: &Rendered) -> HeaderMap {
    let mut h = HeaderMap::new();
    h.insert(
        "x-preview-width",
        HeaderValue::from_str(&r.width.to_string()).unwrap(),
    );
    h.insert(
        "x-preview-height",
        HeaderValue::from_str(&r.height.to_string()).unwrap(),
    );
    h.insert(
        "x-preview-page-count",
        HeaderValue::from_str(&r.page_count.to_string()).unwrap(),
    );
    h.insert(
        "x-preview-size-bytes",
        HeaderValue::from_str(&r.bytes.len().to_string()).unwrap(),
    );
    h.insert(
        "x-preview-sha256",
        HeaderValue::from_str(&r.sha256).unwrap(),
    );
    h
}

fn headers_with_type(mut h: HeaderMap, media_type: &str) -> HeaderMap {
    h.insert("content-type", HeaderValue::from_str(media_type).unwrap());
    h
}
