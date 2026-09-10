use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use chrono::{TimeZone, Utc};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use ufo_control::email::{
    apex_host, email_sender_from_env, invite_email, normalize_email, parse_assume_role_credentials,
    sigv4_headers, AwsCall, AwsEndpoints, EmailError, EmailSender, SesCredentials, SesEmailSender,
    SignupEmailPolicy, AWS_ROLE_ARN_ENV, AWS_TIMEOUT_SECONDS, AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    CONSOLE_EMAIL_MODE, DEFAULT_SES_REGION, DISPOSABLE_EMAIL_DOMAINS, EMAIL_MODE_ENV,
    FREE_EMAIL_DOMAINS, JSON_CONTENT_TYPE, SEND_EMAIL, SES_REGION_ENV, SES_SENDER_ENV, SES_SERVICE,
};

const STS_RESPONSE: &str = r#"<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
  <AssumeRoleWithWebIdentityResult>
    <SubjectFromWebIdentityToken>system:serviceaccount:ufo-system:ufo-gateway</SubjectFromWebIdentityToken>
    <AssumedRoleUser>
      <Arn>arn:aws:sts::111122223333:assumed-role/ufo-testing-gateway-ses/ufo-gateway-email</Arn>
      <AssumedRoleId>AROAEXAMPLE:ufo-gateway-email</AssumedRoleId>
    </AssumedRoleUser>
    <Credentials>
      <SessionToken>IQoJb3JpZ2luX2VjEXAMPLETOKEN</SessionToken>
      <SecretAccessKey>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</SecretAccessKey>
      <Expiration>2026-07-10T18:00:00Z</Expiration>
      <AccessKeyId>ASIAEXAMPLE</AccessKeyId>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
  <ResponseMetadata>
    <RequestId>c6104cbe-af31-11e0-8154-cbc7ccf896c7</RequestId>
  </ResponseMetadata>
</AssumeRoleWithWebIdentityResponse>
"#;

#[test]
fn apex_host_strips_scheme_and_trailing_path() {
    assert_eq!(
        apex_host("https://testing.flyingobject.ai/").unwrap(),
        "testing.flyingobject.ai"
    );
    assert_eq!(
        apex_host("https://flyingobject.ai").unwrap(),
        "flyingobject.ai"
    );
}

#[test]
fn apex_host_rejects_a_schemeless_value() {
    let refused = apex_host("flyingobject.ai").unwrap_err();
    assert!(
        refused.to_string().contains("UFO_PUBLIC_BASE_URL"),
        "{refused}"
    );
}

#[test]
fn invite_email_names_the_granted_address_and_carries_no_secret() {
    let expires = Utc.with_ymd_and_hms(2026, 7, 26, 18, 45, 0).unwrap();
    let email = invite_email(
        "founder@acme.com",
        expires,
        "flyingobject.ai",
        "https://app.flyingobject.ai",
    )
    .unwrap();
    assert_eq!(email.subject, "Your invitation");
    assert_eq!(
        email.text,
        "Sign in: https://app.flyingobject.ai/surface/web#/first-run\n\n\
         Or install it: curl -fsSL https://flyingobject.ai/ufo | sh\n\n\
         Sign in as founder@acme.com. This invitation expires 2026-07-26 18:45 UTC.\n"
    );
    assert!(email
        .html
        .contains("https://app.flyingobject.ai/login/logo.png"));
    assert!(email
        .html
        .contains("https://app.flyingobject.ai/surface/web#/first-run"));
    assert!(!email.html.contains("Anyone at"));
    assert!(email.html.contains(">Sign in</a>"));
}

#[test]
fn invite_email_escapes_markup_fields() {
    let expires = Utc.with_ymd_and_hms(2026, 7, 26, 18, 45, 0).unwrap();
    let email = invite_email(
        r#"founder&"<@acme.com"#,
        expires,
        "flyingobject.ai",
        "https://app.flyingobject.ai",
    )
    .unwrap();
    assert!(email.html.contains("founder&amp;&quot;&lt;@acme.com"));
    assert!(!email.html.contains(r#"founder&"<@acme.com"#));
}

#[test]
fn assume_role_credentials_parse() {
    assert_eq!(
        parse_assume_role_credentials(STS_RESPONSE).unwrap(),
        SesCredentials {
            access_key: "ASIAEXAMPLE".to_string(),
            secret_key: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY".to_string(),
            session_token: "IQoJb3JpZ2luX2VjEXAMPLETOKEN".to_string(),
        }
    );
}

#[test]
fn a_missing_credential_field_raises() {
    let truncated = STS_RESPONSE.replace("<AccessKeyId>ASIAEXAMPLE</AccessKeyId>", "");
    let refused = parse_assume_role_credentials(&truncated).unwrap_err();
    assert!(
        refused.to_string().contains("missing AccessKeyId"),
        "{refused}"
    );
}

#[test]
fn sigv4_headers_sign_the_session_token() {
    let credentials = SesCredentials {
        access_key: "ASIAEXAMPLE".to_string(),
        secret_key: "secret".to_string(),
        session_token: "token".to_string(),
    };
    let headers = sigv4_headers(
        &AwsCall {
            service: SES_SERVICE,
            operation: SEND_EMAIL,
            url: "https://email.us-east-1.amazonaws.com/v2/email/outbound-emails",
            content_type: JSON_CONTENT_TYPE,
            target: None,
            timeout_seconds: AWS_TIMEOUT_SECONDS,
        },
        br#"{"FromEmailAddress": "no-reply@ufo.ai"}"#,
        "us-east-1",
        &credentials,
        Utc.with_ymd_and_hms(2026, 7, 10, 12, 0, 0).unwrap(),
    );
    assert_eq!(headers["x-amz-security-token"], "token");
    assert_eq!(headers["x-amz-date"], "20260710T120000Z");
    assert!(headers["authorization"].starts_with(
        "AWS4-HMAC-SHA256 Credential=ASIAEXAMPLE/20260710/us-east-1/ses/aws4_request, "
    ));
    let signed = headers["authorization"]
        .split("SignedHeaders=")
        .nth(1)
        .unwrap();
    assert!(signed.contains("x-amz-security-token"), "{signed}");
}

#[test]
fn a_personal_domain_uses_the_exact_normalized_address_as_its_subject() {
    let policy = SignupEmailPolicy::default();
    for (email, subject) in [
        ("someone@gmail.com", "someone@gmail.com"),
        ("Someone@GMAIL.com", "someone@gmail.com"),
        ("someone@gmail.com.", "someone@gmail.com"),
        ("someone+tag@gmail.com", "someone+tag@gmail.com"),
    ] {
        let signup = policy.validate(email).unwrap();
        assert_eq!(signup.domain, "gmail.com");
        assert_eq!(signup.subject, subject);
    }
}

#[test]
fn a_disposable_domain_is_refused() {
    let refused = SignupEmailPolicy::default()
        .validate("founder@mailinator.com.")
        .unwrap_err();
    assert_eq!(
        refused,
        EmailError::Disposable("mailinator.com".to_string())
    );
}

#[test]
fn a_malformed_address_is_refused_before_any_send() {
    let policy = SignupEmailPolicy::default();
    for email in [
        "someone@gmail.com..", // an empty label
        "<someone@gmail.com>", // a stray bracket in the domain
        "someone@gmаil.com",   // a cyrillic homograph, not an LDH label
        "someone@ gmail.com",  // a space in the domain
        "someone@@gmail.com",  // two @
        "@gmail.com",          // an empty local part
        "someone@localhost",   // no dot
    ] {
        assert_eq!(
            policy.validate(email),
            Err(EmailError::Malformed),
            "{email} was not refused as malformed"
        );
    }
}

#[test]
fn a_work_domain_passes_with_its_root_dot_normalized() {
    let policy = SignupEmailPolicy::default();
    for (email, domain) in [
        ("founder@acme.io", "acme.io"),
        ("founder@sub.acme.io", "sub.acme.io"),
        ("founder@acme.io.", "acme.io"),
        ("founder@xn--80ak6aa92e.com", "xn--80ak6aa92e.com"),
    ] {
        let signup = policy.validate(email).unwrap();
        assert_eq!(signup.domain, domain);
        assert_eq!(signup.subject, domain);
        assert_eq!(normalize_email(email).unwrap().1, domain);
    }
}

struct Exchange {
    path: String,
    body: String,
    authorization: Option<String>,
}

async fn serve(responses: Vec<(u16, String)>) -> (String, Arc<Mutex<Vec<Exchange>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let log: Arc<Mutex<Vec<Exchange>>> = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&log);
    tokio::spawn(async move {
        for (status, payload) in responses {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let mut raw = Vec::new();
            let mut chunk = [0_u8; 4096];
            loop {
                let read = match stream.read(&mut chunk).await {
                    Ok(0) | Err(_) => break,
                    Ok(read) => read,
                };
                raw.extend_from_slice(&chunk[..read]);
                let text = String::from_utf8_lossy(&raw).to_string();
                if let Some((head, body)) = text.split_once("\r\n\r\n") {
                    let length = head
                        .lines()
                        .find_map(|line| {
                            let (name, value) = line.split_once(':')?;
                            name.trim()
                                .eq_ignore_ascii_case("content-length")
                                .then(|| value.trim().parse::<usize>().ok())?
                        })
                        .unwrap_or(0);
                    if body.len() >= length {
                        break;
                    }
                }
            }
            let text = String::from_utf8_lossy(&raw).to_string();
            let (head, body) = text.split_once("\r\n\r\n").unwrap_or((text.as_str(), ""));
            let path = head
                .lines()
                .next()
                .and_then(|line| line.split_whitespace().nth(1))
                .unwrap_or_default()
                .to_string();
            let authorization = head.lines().find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.trim()
                    .eq_ignore_ascii_case("authorization")
                    .then(|| value.trim().to_string())
            });
            sink.lock().unwrap().push(Exchange {
                path,
                body: body.to_string(),
                authorization,
            });
            let reply = format!(
                "HTTP/1.1 {status} X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                payload.len()
            );
            let _ = stream.write_all(reply.as_bytes()).await;
            let _ = stream.flush().await;
        }
    });
    (base, log)
}

fn sender(base: &str, token_file: PathBuf) -> SesEmailSender {
    SesEmailSender {
        source: "no-reply@flyingobject.ai".to_string(),
        region: "us-east-1".to_string(),
        role_arn: "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses".to_string(),
        token_file,
        endpoints: AwsEndpoints {
            ses: base.to_string(),
            sts: format!("{base}/"),
        },
    }
}

fn projected_token() -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("token");
    std::fs::write(&path, "  projected-web-identity-token\n").unwrap();
    (directory, path)
}

#[tokio::test]
async fn send_exchanges_the_projected_token_then_posts_the_rendered_message() {
    let (base, log) = serve(vec![
        (200, STS_RESPONSE.to_string()),
        (200, "{}".to_string()),
    ])
    .await;
    let (_directory, token_file) = projected_token();
    sender(&base, token_file)
        .send(
            "founder@acme.com",
            "Your invitation",
            "body text",
            Some("<p>body markup</p>"),
        )
        .await
        .unwrap();

    let exchanges = log.lock().unwrap();
    assert_eq!(exchanges.len(), 2, "one STS exchange, then one SES send");

    let sts = &exchanges[0];
    assert!(
        sts.body.contains("Action=AssumeRoleWithWebIdentity"),
        "{}",
        sts.body
    );
    assert!(
        sts.body
            .contains("WebIdentityToken=projected-web-identity-token"),
        "{}",
        sts.body
    );
    assert!(sts.body.contains("DurationSeconds=900"), "{}", sts.body);

    let ses = &exchanges[1];
    assert_eq!(ses.path, "/v2/email/outbound-emails");
    let payload: serde_json::Value = serde_json::from_str(&ses.body).unwrap();
    assert_eq!(payload["FromEmailAddress"], "no-reply@flyingobject.ai");
    assert_eq!(payload["Destination"]["ToAddresses"][0], "founder@acme.com");
    assert_eq!(
        payload["Content"]["Simple"]["Subject"]["Data"],
        "Your invitation"
    );
    assert_eq!(
        payload["Content"]["Simple"]["Body"]["Text"]["Data"],
        "body text"
    );
    assert_eq!(
        payload["Content"]["Simple"]["Body"]["Html"]["Data"],
        "<p>body markup</p>"
    );
    let authorization = ses.authorization.as_deref().unwrap_or_default();
    assert!(
        authorization.contains("Credential=ASIAEXAMPLE/"),
        "{authorization}"
    );
}

#[tokio::test]
async fn a_send_with_no_html_alternative_posts_a_text_only_body() {
    let (base, log) = serve(vec![
        (200, STS_RESPONSE.to_string()),
        (200, "{}".to_string()),
    ])
    .await;
    let (_directory, token_file) = projected_token();
    sender(&base, token_file)
        .send("founder@acme.com", "Your ufo invite", "body text", None)
        .await
        .unwrap();

    let exchanges = log.lock().unwrap();
    let payload: serde_json::Value = serde_json::from_str(&exchanges[1].body).unwrap();
    assert_eq!(
        payload["Content"]["Simple"]["Body"]["Text"]["Data"],
        "body text"
    );
    assert!(
        payload["Content"]["Simple"]["Body"]["Html"].is_null(),
        "{}",
        exchanges[1].body
    );
}

#[tokio::test]
async fn send_surfaces_the_ses_denial_body() {
    let denial = r#"{"message":"Email address is not verified."}"#;
    let (base, _log) = serve(vec![
        (200, STS_RESPONSE.to_string()),
        (400, denial.to_string()),
    ])
    .await;
    let (_directory, token_file) = projected_token();
    let refused = sender(&base, token_file)
        .send("founder@acme.com", "s", "t", Some("<p>t</p>"))
        .await
        .unwrap_err()
        .to_string();
    assert!(refused.contains("SendEmail returned 400"), "{refused}");
    assert!(refused.contains("not verified"), "{refused}");
}

#[tokio::test]
async fn assume_role_surfaces_the_sts_error_body() {
    let denial = "<ErrorResponse><Error><Message>Not authorized</Message></Error></ErrorResponse>";
    let (base, _log) = serve(vec![(403, denial.to_string())]).await;
    let (_directory, token_file) = projected_token();
    let refused = sender(&base, token_file)
        .send("founder@acme.com", "s", "t", Some("<p>t</p>"))
        .await
        .unwrap_err()
        .to_string();
    assert!(
        refused.contains("STS AssumeRoleWithWebIdentity returned 403"),
        "{refused}"
    );
    assert!(refused.contains("Not authorized"), "{refused}");
}

#[tokio::test]
async fn an_unreadable_token_file_fails_loud_before_any_call() {
    let (base, log) = serve(vec![(200, STS_RESPONSE.to_string())]).await;
    let refused = sender(&base, PathBuf::from("/nonexistent/token"))
        .send("founder@acme.com", "s", "t", Some("<p>t</p>"))
        .await
        .unwrap_err()
        .to_string();
    assert!(refused.contains("web identity token"), "{refused}");
    assert!(log.lock().unwrap().is_empty(), "nothing was sent");
}

static ENV: Mutex<()> = Mutex::new(());

fn with_env<T>(pairs: &[(&str, Option<&str>)], body: impl FnOnce() -> T) -> T {
    let _guard = ENV.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let restore: Vec<(String, Option<String>)> = pairs
        .iter()
        .map(|(name, _)| (name.to_string(), std::env::var(name).ok()))
        .collect();
    for (name, value) in pairs {
        match value {
            Some(value) => std::env::set_var(name, value),
            None => std::env::remove_var(name),
        }
    }
    let outcome = body();
    for (name, value) in restore {
        match value {
            Some(value) => std::env::set_var(&name, value),
            None => std::env::remove_var(&name),
        }
    }
    outcome
}

#[test]
fn the_sender_is_built_from_the_pods_irsa_identity() {
    let built = with_env(
        &[
            (EMAIL_MODE_ENV, None),
            (SES_SENDER_ENV, Some("no-reply@flyingobject.ai")),
            (SES_REGION_ENV, None),
            (AWS_ROLE_ARN_ENV, Some("arn:aws:iam::111122223333:role/ses")),
            (AWS_WEB_IDENTITY_TOKEN_FILE_ENV, Some("/var/run/token")),
        ],
        email_sender_from_env,
    )
    .unwrap();
    let EmailSender::Ses(sender) = built else {
        panic!("the default mode is SES");
    };
    assert_eq!(sender.source, "no-reply@flyingobject.ai");
    assert_eq!(sender.region, DEFAULT_SES_REGION);
    assert_eq!(sender.role_arn, "arn:aws:iam::111122223333:role/ses");
    assert_eq!(sender.token_file, PathBuf::from("/var/run/token"));
    assert_eq!(
        sender.endpoints.ses,
        format!("https://email.{DEFAULT_SES_REGION}.amazonaws.com")
    );
}

#[test]
fn the_sender_fails_loud_without_irsa() {
    let refused = with_env(
        &[
            (EMAIL_MODE_ENV, None),
            (SES_SENDER_ENV, Some("no-reply@flyingobject.ai")),
            (AWS_ROLE_ARN_ENV, None),
            (AWS_WEB_IDENTITY_TOKEN_FILE_ENV, None),
        ],
        email_sender_from_env,
    )
    .unwrap_err()
    .to_string();
    assert!(refused.contains(AWS_ROLE_ARN_ENV), "{refused}");
}

#[tokio::test]
async fn console_mode_needs_no_ses_configuration() {
    let built = with_env(
        &[
            (EMAIL_MODE_ENV, Some(CONSOLE_EMAIL_MODE)),
            (SES_SENDER_ENV, None),
            (AWS_ROLE_ARN_ENV, None),
            (AWS_WEB_IDENTITY_TOKEN_FILE_ENV, None),
        ],
        email_sender_from_env,
    )
    .unwrap();
    assert!(matches!(built, EmailSender::Console));
    built
        .send(
            "founder@acme.com",
            "Your invitation",
            "body",
            Some("<p>body</p>"),
        )
        .await
        .unwrap();
}

#[test]
fn an_unknown_mode_fails_loud_rather_than_defaulting() {
    let refused = with_env(&[(EMAIL_MODE_ENV, Some("smtp"))], email_sender_from_env)
        .unwrap_err()
        .to_string();
    assert!(refused.contains("smtp"), "{refused}");
    assert!(refused.contains("not a valid email mode"), "{refused}");
}

#[test]
fn a_shared_consumer_domain_never_becomes_the_workspace_subject() {
    let policy = SignupEmailPolicy::default();
    for email in [
        "someone@web.de",
        "someone@gmx.net",
        "someone@t-online.de",
        "someone@mail.ru",
        "someone@yandex.ru",
        "someone@qq.com",
        "someone@163.com",
        "someone@naver.com",
        "someone@hanmail.net",
        "someone@orange.fr",
        "someone@laposte.net",
        "someone@libero.it",
        "someone@uol.com.br",
        "someone@seznam.cz",
        "someone@wp.pl",
        "someone@hotmail.co.uk",
        "someone@yahoo.co.jp",
        "someone@live.ca",
        "someone@outlook.com.br",
        "someone@comcast.net",
        "someone@btinternet.com",
        "someone@bigpond.com",
        "someone@rediffmail.com",
        "someone@tutanota.com",
        "someone@protonmail.ch",
        "someone@gmx.co.uk",
        "someone@aol.co.uk",
        "someone@hotmail.ch",
        "someone@yahoo.co.za",
        "someone@outlook.co.nz",
    ] {
        let signup = policy.validate(email).unwrap();
        assert_eq!(signup.subject, email, "{email} claimed its provider domain");
    }
}

#[test]
fn the_domain_lists_name_each_domain_once() {
    let mut seen = std::collections::HashSet::new();
    for domain in FREE_EMAIL_DOMAINS.iter().chain(DISPOSABLE_EMAIL_DOMAINS) {
        assert!(seen.insert(*domain), "{domain} is listed twice");
        assert_eq!(
            *domain,
            domain.to_lowercase(),
            "{domain} is not lowercase, so no address can ever match it"
        );
        assert!(
            domain.contains('.') && !domain.starts_with('.') && !domain.ends_with('.'),
            "{domain} is not a domain the pattern can produce"
        );
    }
}
