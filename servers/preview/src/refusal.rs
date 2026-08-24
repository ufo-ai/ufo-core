use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;

/// A typed refusal answered to the caller; the one error shape the API speaks.
#[derive(Debug)]
pub enum Refusal {
    UnsupportedType(String),
    KindMismatch(String),
    TooLarge(String),
    RenderTimeout(String),
    FetchRefused(String),
}

impl Refusal {
    fn parts(&self) -> (StatusCode, &'static str, String) {
        match self {
            Refusal::UnsupportedType(d) => (
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
                "unsupported_type",
                d.clone(),
            ),
            Refusal::KindMismatch(d) => {
                (StatusCode::UNPROCESSABLE_ENTITY, "kind_mismatch", d.clone())
            }
            Refusal::TooLarge(d) => (StatusCode::PAYLOAD_TOO_LARGE, "too_large", d.clone()),
            Refusal::RenderTimeout(d) => (StatusCode::GATEWAY_TIMEOUT, "render_timeout", d.clone()),
            Refusal::FetchRefused(d) => (StatusCode::BAD_GATEWAY, "fetch_refused", d.clone()),
        }
    }
}

impl IntoResponse for Refusal {
    fn into_response(self) -> Response {
        let (status, code, detail) = self.parts();
        (
            status,
            Json(serde_json::json!({"error": code, "detail": detail})),
        )
            .into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::response::IntoResponse;

    #[test]
    fn refusal_maps_status_and_code() {
        let r = Refusal::TooLarge("input 999 bytes over cap".into()).into_response();
        assert_eq!(r.status(), axum::http::StatusCode::PAYLOAD_TOO_LARGE);
    }
}
