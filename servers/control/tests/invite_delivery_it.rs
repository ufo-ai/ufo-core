//! The teammate invitation delivery: what it sends, what it refuses to send twice, and where each
//! fault lands.
//!
//! Driven against a real Postgres and two local servers — one standing in for core's onboarding RPC,
//! one for STS and SES. The invariants under test are the ones that decide whether a person gets a
//! second copy of the same message, so the lease, the attempt marker, and the verdict split all run
//! against the real thing.

mod harness;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{DateTime, Utc};
use harness::ledger_pool;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::oneshot;
use ufo_control::email::{AwsEndpoints, EmailSender, SesEmailSender};
use ufo_control::gateway::INVITATION_LOGIN_PATH;
use ufo_control::invite_delivery::{
    rearm_failed_delivery, InviteDeliveries, ERROR_CHARS, INVITATIONS_PER_WORKSPACE_PER_DAY,
    STATE_DELIVERED, STATE_FAILED, STATE_PENDING,
};
use ufo_control::shared::SharedWorkspaces;
use uuid::Uuid;

const STS_RESPONSE: &str = r#"<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
  <AssumeRoleWithWebIdentityResult>
    <Credentials>
      <SessionToken>IQoJb3JpZ2luX2VjEXAMPLETOKEN</SessionToken>
      <SecretAccessKey>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</SecretAccessKey>
      <Expiration>2026-07-10T18:00:00Z</Expiration>
      <AccessKeyId>ASIAEXAMPLE</AccessKeyId>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
</AssumeRoleWithWebIdentityResponse>
"#;

const SES_PATH: &str = "/v2/email/outbound-emails";
const APEX: &str = "flyingobject.ai";
const WORKER: &str = "test-worker";

struct Exchange {
    path: String,
    body: String,
}

/// One canned answer: a status, the headers that ride with it, a body, and an optional gate the
/// server waits on before replying. The header list is what the shared harness has no room for, and
/// `Retry-After` is exactly the header a throttle carries; the gate is what holds a send in flight
/// long enough for a second worker to act on the same row.
struct Answer {
    status: u16,
    headers: Vec<(String, String)>,
    body: String,
    hold: Option<oneshot::Receiver<()>>,
}

fn ok(body: &str) -> Answer {
    Answer {
        status: 200,
        headers: Vec::new(),
        body: body.to_string(),
        hold: None,
    }
}

fn refused(status: u16, body: &str) -> Answer {
    Answer {
        status,
        headers: Vec::new(),
        body: body.to_string(),
        hold: None,
    }
}

fn throttled(retry_after: &str) -> Answer {
    Answer {
        status: 429,
        headers: vec![("Retry-After".to_string(), retry_after.to_string())],
        body: r#"{"message":"Maximum sending rate exceeded."}"#.to_string(),
        hold: None,
    }
}

fn held(gate: oneshot::Receiver<()>) -> Answer {
    Answer {
        status: 200,
        headers: Vec::new(),
        body: "{}".to_string(),
        hold: Some(gate),
    }
}

async fn serve(answers: Vec<Answer>) -> (String, Arc<Mutex<Vec<Exchange>>>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let log = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&log);
    tokio::spawn(async move {
        for answer in answers {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let (status, answered) = (answer.status, answer.body.clone());
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
            sink.lock().unwrap().push(Exchange {
                path,
                body: body.to_string(),
            });
            if let Some(gate) = answer.hold {
                let _ = gate.await;
            }
            let extra: String = answer
                .headers
                .iter()
                .map(|(name, value)| format!("{name}: {value}\r\n"))
                .collect();
            let reply = format!(
                "HTTP/1.1 {status} X\r\n{extra}Content-Length: {}\r\nConnection: close\r\n\r\n{answered}",
                answered.len()
            );
            let _ = stream.write_all(reply.as_bytes()).await;
            let _ = stream.flush().await;
        }
    });
    (base, log)
}

fn projected_token() -> (tempfile::TempDir, PathBuf) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("token");
    std::fs::write(&path, "projected-web-identity-token\n").unwrap();
    (directory, path)
}

struct Fleet {
    deliveries: InviteDeliveries,
    source: Source,
    ses: Arc<Mutex<Vec<Exchange>>>,
    _token: tempfile::TempDir,
}

async fn fleet(
    pool: deadpool_postgres::Pool,
    invitations: Vec<Listed>,
    ses_answers: Vec<Answer>,
) -> Fleet {
    worker(pool, invitations, ses_answers, WORKER).await
}

async fn worker(
    pool: deadpool_postgres::Pool,
    invitations: Vec<Listed>,
    ses_answers: Vec<Answer>,
    worker_id: &str,
) -> Fleet {
    let (core_base, source) = Source::spawn(invitations).await;
    let (ses_base, ses) = serve(ses_answers).await;
    let (directory, token_file) = projected_token();
    Fleet {
        deliveries: InviteDeliveries {
            pool,
            core: SharedWorkspaces {
                workspace_url: "https://app.flyingobject.ai".to_string(),
                serve_internal_url: core_base,
                control_token: "onboard-control-token".to_string(),
            },
            sender: EmailSender::Ses(Box::new(SesEmailSender {
                source: "no-reply@flyingobject.ai".to_string(),
                region: "us-east-1".to_string(),
                role_arn: "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses".to_string(),
                token_file,
                endpoints: AwsEndpoints {
                    ses: ses_base.clone(),
                    sts: format!("{ses_base}/"),
                },
            })),
            apex_host: APEX.to_string(),
            worker_id: worker_id.to_string(),
            poll_interval: std::time::Duration::from_millis(10),
        },
        source,
        ses,
        _token: directory,
    }
}

/// One invitation core holds.
#[derive(Debug, Clone)]
struct Listed {
    workspace_id: Uuid,
    email: String,
    invited_by: String,
    workspace_label: String,
    invited_at: DateTime<Utc>,
}

fn listed(workspace: Uuid, email: &str, inviter: &str, label: &str, stamp: &str) -> Listed {
    Listed {
        workspace_id: workspace,
        email: email.to_string(),
        invited_by: inviter.to_string(),
        workspace_label: label.to_string(),
        invited_at: stamp.parse().unwrap(),
    }
}

/// What orders the read, and what the page cursor is taken from.
fn key(row: &Listed) -> (DateTime<Utc>, Uuid, String) {
    (row.invited_at, row.workspace_id, row.email.clone())
}

fn decoded(value: &str) -> String {
    let raw = value.as_bytes();
    let mut out = Vec::with_capacity(raw.len());
    let mut at = 0;
    while at < raw.len() {
        match raw[at] {
            b'%' if at + 2 < raw.len() => {
                let hex = std::str::from_utf8(&raw[at + 1..at + 3]).unwrap();
                out.push(u8::from_str_radix(hex, 16).unwrap());
                at += 3;
            }
            byte => {
                out.push(byte);
                at += 1;
            }
        }
    }
    String::from_utf8(out).unwrap()
}

fn parameter(query: &str, name: &str) -> Option<String> {
    query.split('&').find_map(|pair| {
        let (asked, value) = pair.split_once('=')?;
        (asked == name).then(|| decoded(value))
    })
}

/// Core's invitation read, standing in for `serve`: the invitations it holds, answered oldest first
/// in pages of `SOURCE_PAGE` strictly after the cursor the caller sent — the contract core's SQL
/// keeps. Rows are added to it between sweeps, which is how a seat write that commits after a
/// later-stamped one is put in front of the poller.
#[derive(Clone)]
struct Source {
    rows: Arc<Mutex<Vec<Listed>>>,
    asked: Arc<Mutex<Vec<String>>>,
}

const SOURCE_PAGE: usize = 2;

impl Source {
    async fn spawn(rows: Vec<Listed>) -> (String, Source) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let source = Source {
            rows: Arc::new(Mutex::new(rows)),
            asked: Arc::new(Mutex::new(Vec::new())),
        };
        let serving = source.clone();
        tokio::spawn(async move {
            loop {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let mut raw = Vec::new();
                let mut chunk = [0_u8; 4096];
                loop {
                    match stream.read(&mut chunk).await {
                        Ok(0) | Err(_) => break,
                        Ok(read) => raw.extend_from_slice(&chunk[..read]),
                    }
                    if String::from_utf8_lossy(&raw).contains("\r\n\r\n") {
                        break;
                    }
                }
                let text = String::from_utf8_lossy(&raw).to_string();
                let path = text
                    .lines()
                    .next()
                    .and_then(|line| line.split_whitespace().nth(1))
                    .unwrap_or_default()
                    .to_string();
                let body = serving.answer(path.split_once('?').map_or("", |(_, query)| query));
                serving.asked.lock().unwrap().push(path);
                let reply = format!(
                    "HTTP/1.1 200 X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(reply.as_bytes()).await;
                let _ = stream.flush().await;
            }
        });
        (base, source)
    }

    fn add(&self, row: Listed) {
        self.rows.lock().unwrap().push(row);
    }

    fn answer(&self, query: &str) -> String {
        let after = parameter(query, "after_invited_at").map(|stamp| {
            (
                stamp.parse::<DateTime<Utc>>().unwrap(),
                parameter(query, "after_workspace_id")
                    .unwrap()
                    .parse::<Uuid>()
                    .unwrap(),
                parameter(query, "after_email").unwrap(),
            )
        });
        let mut rows = self.rows.lock().unwrap().clone();
        rows.sort_by_key(key);
        let page: Vec<String> = rows
            .iter()
            .filter(|row| match &after {
                None => true,
                Some(cursor) => key(row) > *cursor,
            })
            .take(SOURCE_PAGE)
            .map(|row| {
                format!(
                    r#"{{"workspace_id":"{}","email":"{}","invited_by":"{}",
                        "workspace_label":"{}","invited_at":"{}"}}"#,
                    row.workspace_id,
                    row.email,
                    row.invited_by,
                    row.workspace_label,
                    row.invited_at.to_rfc3339()
                )
            })
            .collect();
        format!(r#"{{"invitations":[{}]}}"#, page.join(","))
    }
}

fn sent_ok() -> Vec<Answer> {
    vec![ok(STS_RESPONSE), ok("{}")]
}

async fn row(pool: &deadpool_postgres::Pool, workspace: Uuid, email: &str) -> tokio_postgres::Row {
    pool.get()
        .await
        .unwrap()
        .query_one(
            "select *, extract(epoch from (next_attempt_at - now()))::float8 as delay \
             from ufo_control.invite_delivery where workspace_id = $1 and email = $2",
            &[&workspace, &email],
        )
        .await
        .unwrap()
}

async fn insert_pending(pool: &deadpool_postgres::Pool, workspace: Uuid, email: &str) {
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at) \
             values ($1, $2, 'pending', 'admin@acme.com', 'acme.com', now())",
            &[&workspace, &email],
        )
        .await
        .unwrap();
}

fn ses_sends(log: &Arc<Mutex<Vec<Exchange>>>) -> usize {
    log.lock()
        .unwrap()
        .iter()
        .filter(|exchange| exchange.path == SES_PATH)
        .count()
}

#[tokio::test]
async fn a_stamped_invitation_materializes_one_row_carrying_the_three_facts() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    // No SES answer is queued, so the connection is refused: the materialization and the claim are
    // what this asserts, and a refused connection proves nothing was accepted.
    let fleet = fleet(
        pool.clone(),
        vec![listed(
            workspace,
            "teammate@acme.com",
            "admin@acme.com",
            "acme.com",
            "2026-08-18T10:00:00Z",
        )],
        vec![],
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let row = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(row.get::<_, String>("invited_by"), "admin@acme.com");
    assert_eq!(row.get::<_, String>("workspace_label"), "acme.com");
    assert_eq!(row.get::<_, String>("state"), STATE_PENDING);
    assert_eq!(row.get::<_, i32>("attempts"), 1);
    assert!(
        row.get::<_, Option<chrono::DateTime<chrono::Utc>>>("sent_at")
            .is_none(),
        "a connection that never opened is proof nothing was sent"
    );
}

#[tokio::test]
async fn a_delivery_states_who_added_them_which_workspace_and_where_to_sign_in() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    let fleet = fleet(
        pool.clone(),
        vec![listed(
            workspace,
            "teammate@acme.com",
            "admin@acme.com",
            "acme.com",
            "2026-08-18T10:00:00Z",
        )],
        sent_ok(),
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let payload: serde_json::Value = {
        let sent = fleet.ses.lock().unwrap();
        let message = sent
            .iter()
            .find(|one| one.path == SES_PATH)
            .expect("a send");
        serde_json::from_str(&message.body).unwrap()
    };
    assert_eq!(
        payload["Destination"]["ToAddresses"][0],
        "teammate@acme.com"
    );
    let body = payload["Content"]["Simple"]["Body"]["Text"]["Data"]
        .as_str()
        .unwrap();
    assert!(body.contains("admin@acme.com added you"), "{body}");
    assert!(body.contains("the acme.com workspace"), "{body}");
    assert!(
        body.contains(&format!("https://{APEX}{INVITATION_LOGIN_PATH}")),
        "{body}"
    );

    // The HTML alternative states the same three facts and carries the sign-in page's own look:
    // every rule inline, the card laid out in tables, and the sign-in link drawn as its button.
    let markup = payload["Content"]["Simple"]["Body"]["Html"]["Data"]
        .as_str()
        .expect("the text alternative is not the whole message");
    assert!(markup.contains("admin@acme.com added you"), "{markup}");
    assert!(markup.contains("the acme.com workspace"), "{markup}");
    assert!(markup.contains("Sign in as teammate@acme.com"), "{markup}");
    assert!(
        markup.contains(&format!(r#"href="https://{APEX}{INVITATION_LOGIN_PATH}""#)),
        "{markup}"
    );
    assert!(markup.contains("background:#FAF9F7"), "{markup}");
    assert!(markup.contains("border:1px solid #EBEAE9"), "{markup}");
    assert!(markup.contains(r##"bgcolor="#191A1A""##), "{markup}");
    assert!(markup.contains("system-ui,sans-serif"), "{markup}");
    // A mail client keeps no head, resolves no custom property, and lays out no flexbox, so a body
    // holding any of the three is a message somebody opens broken.
    assert!(!markup.contains("<style"), "{markup}");
    assert!(!markup.contains("var(--"), "{markup}");
    assert!(!markup.contains("light-dark("), "{markup}");
    assert!(!markup.contains("display:flex"), "{markup}");
    // SVG is the one image format clients reliably refuse, so the mark is drawn from the raster the
    // gateway serves beside it, at an absolute URL a mail client can reach, and carries its own alt
    // text for the many clients that block the fetch until a reader asks for it.
    assert!(!markup.contains(".svg"), "{markup}");
    assert!(
        markup.contains(&format!(r##"src="https://{APEX}/login/logo.png""##)),
        "{markup}"
    );
    assert!(markup.contains(r##"alt="ufo""##), "{markup}");

    let row = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_DELIVERED);
    assert!(row
        .get::<_, Option<chrono::DateTime<chrono::Utc>>>("delivered_at")
        .is_some());
}

#[tokio::test]
async fn markup_in_an_inviting_address_reaches_the_recipient_as_text() {
    // An address's local part accepts characters that would close a tag, and the workspace label is
    // read off a member's own domain. Both land in the HTML body, so both are escaped there.
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    let fleet = fleet(
        pool.clone(),
        vec![listed(
            workspace,
            "teammate@acme.com",
            "<b>admin</b>@acme.com",
            "a&b<script>.com",
            "2026-08-18T10:00:00Z",
        )],
        sent_ok(),
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let payload: serde_json::Value = {
        let sent = fleet.ses.lock().unwrap();
        let message = sent
            .iter()
            .find(|one| one.path == SES_PATH)
            .expect("a send");
        serde_json::from_str(&message.body).unwrap()
    };
    let markup = payload["Content"]["Simple"]["Body"]["Html"]["Data"]
        .as_str()
        .unwrap();
    assert!(
        markup.contains("&lt;b&gt;admin&lt;/b&gt;@acme.com"),
        "{markup}"
    );
    assert!(markup.contains("a&amp;b&lt;script&gt;.com"), "{markup}");
    assert!(!markup.contains("<b>"), "{markup}");
    assert!(!markup.contains("<script>"), "{markup}");
    // The plain-text alternative is read as text, so it carries the address as it stands.
    let body = payload["Content"]["Simple"]["Body"]["Text"]["Data"]
        .as_str()
        .unwrap();
    assert!(body.contains("<b>admin</b>@acme.com added you"), "{body}");
}

#[tokio::test]
async fn the_same_invitation_read_again_sends_nothing_more() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    let fleet = fleet(
        pool.clone(),
        vec![listed(
            workspace,
            "teammate@acme.com",
            "admin@acme.com",
            "acme.com",
            "2026-08-18T10:00:00Z",
        )],
        sent_ok(),
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());
    let delivered = row(&pool, workspace, "teammate@acme.com").await;
    assert!(
        !fleet.deliveries.poll().await.unwrap(),
        "a delivered row is not due again"
    );
    assert_eq!(ses_sends(&fleet.ses), 1);

    // Every sweep re-materializes the whole source, so the settled row meets its own insert once a
    // cycle. It keeps the state, the stamp, and the attempt count it settled on.
    let again = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(again.get::<_, String>("state"), STATE_DELIVERED);
    assert_eq!(
        again.get::<_, Option<DateTime<Utc>>>("delivered_at"),
        delivered.get::<_, Option<DateTime<Utc>>>("delivered_at")
    );
    assert_eq!(again.get::<_, i32>("attempts"), 1);
}

#[tokio::test]
async fn two_workers_racing_one_row_hand_ses_one_message() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "teammate@acme.com").await;
    let first = worker(pool.clone(), vec![], sent_ok(), "worker-a").await;
    let second = worker(pool.clone(), vec![], sent_ok(), "worker-b").await;

    let (left, right) = tokio::join!(first.deliveries.poll(), second.deliveries.poll());
    assert!(left.unwrap() || right.unwrap(), "one of them took the row");
    assert_eq!(
        ses_sends(&first.ses) + ses_sends(&second.ses),
        1,
        "the lease is what keeps one person from getting two copies"
    );
    assert_eq!(
        row(&pool, workspace, "teammate@acme.com")
            .await
            .get::<_, String>("state"),
        STATE_DELIVERED
    );
}

#[tokio::test]
async fn a_row_another_transaction_holds_is_skipped_rather_than_waited_on() {
    // `for update skip locked` is what lets both replicas poll one table. A row a transaction
    // already holds is passed over, so a sweep that finds only that row ends at once instead of
    // queueing behind the worker that took it.
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "teammate@acme.com").await;
    let mut holder = pool.get().await.unwrap();
    let held_row = holder.transaction().await.unwrap();
    held_row
        .execute(
            "select 1 from ufo_control.invite_delivery where workspace_id = $1 for update",
            &[&workspace],
        )
        .await
        .unwrap();

    let fleet = fleet(pool.clone(), vec![], sent_ok()).await;
    let swept = tokio::time::timeout(Duration::from_secs(5), fleet.deliveries.poll()).await;
    assert!(
        matches!(swept, Ok(Ok(false))),
        "a locked row is skipped, never waited on"
    );
    assert_eq!(ses_sends(&fleet.ses), 0);
    held_row.rollback().await.unwrap();
}

#[tokio::test]
async fn a_worker_whose_lease_was_taken_mid_send_settles_nothing() {
    // The compare-and-set on `worker_id` is what stops two workers settling one row. SES is held
    // open until the row has been taken, so the writeback lands on a row this worker no longer
    // owns and leaves it exactly as the second worker left it.
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "teammate@acme.com").await;
    let (release, gate) = oneshot::channel();
    let fleet = fleet(pool.clone(), vec![], vec![ok(STS_RESPONSE), held(gate)]).await;
    let deliveries = fleet.deliveries.clone();
    let sweeping = tokio::spawn(async move { deliveries.poll().await.unwrap() });

    // The attempt marker is written before the send, so it is this worker reaching SES.
    let reached = tokio::time::timeout(Duration::from_secs(5), async {
        while row(&pool, workspace, "teammate@acme.com")
            .await
            .get::<_, Option<DateTime<Utc>>>("sent_at")
            .is_none()
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await;
    assert!(reached.is_ok(), "the worker reached its send");

    pool.get()
        .await
        .unwrap()
        .execute(
            "update ufo_control.invite_delivery set worker_id = 'thief', state = 'claimed', \
             claim_expires_at = now() + interval '2 minutes' where workspace_id = $1",
            &[&workspace],
        )
        .await
        .unwrap();
    release.send(()).unwrap();
    assert!(sweeping.await.unwrap());

    let row = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(
        row.get::<_, Option<String>>("worker_id").as_deref(),
        Some("thief")
    );
    assert_ne!(
        row.get::<_, String>("state"),
        STATE_DELIVERED,
        "a row a second worker took is left as that worker left it"
    );
}

#[tokio::test]
async fn a_live_lease_held_elsewhere_is_left_untouched() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at, worker_id, \
              claim_expires_at) \
             values ($1, 'teammate@acme.com', 'claimed', 'admin@acme.com', 'acme.com', now(), \
                     'other-worker', now() + interval '2 minutes')",
            &[&workspace],
        )
        .await
        .unwrap();
    let fleet = fleet(pool.clone(), vec![], sent_ok()).await;
    assert!(
        !fleet.deliveries.poll().await.unwrap(),
        "a live lease held elsewhere is not claimable"
    );
    assert_eq!(ses_sends(&fleet.ses), 0);
    assert_eq!(
        row(&pool, workspace, "teammate@acme.com")
            .await
            .get::<_, Option<String>>("worker_id")
            .as_deref(),
        Some("other-worker")
    );
}

#[tokio::test]
async fn a_lapsed_lease_is_taken_again() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at, worker_id, \
              claim_expires_at) \
             values ($1, 'teammate@acme.com', 'claimed', 'admin@acme.com', 'acme.com', now(), \
                     'dead-worker', now() - interval '1 minute')",
            &[&workspace],
        )
        .await
        .unwrap();
    let fleet = fleet(pool.clone(), vec![], sent_ok()).await;
    assert!(
        fleet.deliveries.poll().await.unwrap(),
        "the worker holding it did not survive, so the row is taken again"
    );
    assert_eq!(
        row(&pool, workspace, "teammate@acme.com")
            .await
            .get::<_, String>("state"),
        STATE_DELIVERED
    );
}

#[tokio::test]
async fn an_attempt_whose_answer_never_came_is_never_sent_a_second_time() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at, sent_at) \
             values ($1, 'teammate@acme.com', 'pending', 'admin@acme.com', 'acme.com', now(), \
                     now() - interval '5 minutes')",
            &[&workspace],
        )
        .await
        .unwrap();
    let fleet = fleet(pool.clone(), vec![], sent_ok()).await;
    assert!(fleet.deliveries.poll().await.unwrap());

    assert_eq!(ses_sends(&fleet.ses), 0, "nothing is sent on a guess");
    let row = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_FAILED);
    let error = row
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default();
    assert!(error.contains("re-arm"), "{error}");
}

#[tokio::test]
async fn the_days_cap_fails_the_row_over_the_line_and_names_it() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    let connection = pool.get().await.unwrap();
    connection
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at, sent_at, \
              delivered_at) \
             select $1, 'sent-' || n || '@acme.com', 'delivered', 'admin@acme.com', 'acme.com', \
                    now(), now(), now() \
             from generate_series(1, $2::int) as n",
            &[&workspace, &(INVITATIONS_PER_WORKSPACE_PER_DAY as i32)],
        )
        .await
        .unwrap();
    drop(connection);
    insert_pending(&pool, workspace, "teammate@acme.com").await;

    let fleet = fleet(pool.clone(), vec![], sent_ok()).await;
    assert!(fleet.deliveries.poll().await.unwrap());

    assert_eq!(ses_sends(&fleet.ses), 0, "the cap is counted before SES");
    let row = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_FAILED);
    let error = row
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default();
    assert!(
        error.contains(&INVITATIONS_PER_WORKSPACE_PER_DAY.to_string()),
        "{error}"
    );
}

#[tokio::test]
async fn a_throttle_reschedules_behind_the_retry_after_it_names() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "teammate@acme.com").await;
    let fleet = fleet(pool.clone(), vec![], vec![ok(STS_RESPONSE), throttled("7")]).await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let row = row(&pool, workspace, "teammate@acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_PENDING);
    assert!(
        row.get::<_, Option<chrono::DateTime<chrono::Utc>>>("sent_at")
            .is_none(),
        "SES answered, so nothing was accepted and the marker clears"
    );
    let delay: f64 = row.get("delay");
    assert!((4.0..=7.0).contains(&delay), "{delay}");
}

#[tokio::test]
async fn a_retry_after_beyond_the_bound_is_clamped() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "teammate@acme.com").await;
    let fleet = fleet(
        pool.clone(),
        vec![],
        vec![ok(STS_RESPONSE), throttled("86400")],
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let delay: f64 = row(&pool, workspace, "teammate@acme.com")
        .await
        .get("delay");
    assert!(
        delay <= 60.0,
        "a day-long header must not park a row: {delay}"
    );
}

#[tokio::test]
async fn a_documented_transient_ses_error_reschedules_and_a_refused_sender_fails() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "throttled@acme.com").await;
    let throttle = fleet(
        pool.clone(),
        vec![],
        vec![
            ok(STS_RESPONSE),
            refused(400, r#"{"__type":"TooManyRequestsException"}"#),
        ],
    )
    .await;
    assert!(throttle.deliveries.poll().await.unwrap());
    assert_eq!(
        row(&pool, workspace, "throttled@acme.com")
            .await
            .get::<_, String>("state"),
        STATE_PENDING
    );

    insert_pending(&pool, workspace, "unverified@acme.com").await;
    let denial = fleet(
        pool.clone(),
        vec![],
        vec![
            ok(STS_RESPONSE),
            refused(400, r#"{"message":"Email address is not verified."}"#),
        ],
    )
    .await;
    assert!(denial.deliveries.poll().await.unwrap());
    let row = row(&pool, workspace, "unverified@acme.com").await;
    assert_eq!(row.get::<_, String>("state"), STATE_FAILED);
    assert!(row
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default()
        .contains("not verified"));
}

#[tokio::test]
async fn a_row_at_its_attempt_ceiling_fails_instead_of_rescheduling_forever() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at, attempts) \
             values ($1, 'teammate@acme.com', 'pending', 'admin@acme.com', 'acme.com', now(), 8)",
            &[&workspace],
        )
        .await
        .unwrap();
    let fleet = fleet(
        pool.clone(),
        vec![],
        vec![ok(STS_RESPONSE), refused(503, "unavailable")],
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());
    assert_eq!(
        row(&pool, workspace, "teammate@acme.com")
            .await
            .get::<_, String>("state"),
        STATE_FAILED
    );
}

#[tokio::test]
async fn a_refusal_body_is_bounded_where_it_is_stored() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    insert_pending(&pool, workspace, "teammate@acme.com").await;
    let fleet = fleet(
        pool.clone(),
        vec![],
        vec![ok(STS_RESPONSE), refused(400, &"x".repeat(20_000))],
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let error = row(&pool, workspace, "teammate@acme.com")
        .await
        .get::<_, Option<String>>("last_error")
        .unwrap_or_default();
    assert!(error.chars().count() <= ERROR_CHARS, "{}", error.len());
}

#[tokio::test]
async fn a_seat_that_commits_after_a_later_stamped_one_is_still_materialized() {
    // `now()` is fixed when a transaction starts and the seat write then waits for the workspace
    // row lock, so a member stamped earlier can commit after one stamped later. The sweep here
    // lands between the two commits: it sees only the later stamp, and the earlier member reaches
    // the source after it. Nothing else writes this ledger, so a read bounded by what the ledger
    // already holds would never enumerate that member again — no row, no message, no failed row.
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    let fleet = fleet(
        pool.clone(),
        vec![listed(
            workspace,
            "late-stamp@acme.com",
            "admin@acme.com",
            "acme.com",
            "2026-08-18T10:00:05Z",
        )],
        vec![ok(STS_RESPONSE), ok("{}"), ok(STS_RESPONSE), ok("{}")],
    )
    .await;
    assert!(fleet.deliveries.poll().await.unwrap());

    fleet.source.add(listed(
        workspace,
        "early-stamp@acme.com",
        "admin@acme.com",
        "acme.com",
        "2026-08-18T10:00:00Z",
    ));
    assert!(
        fleet.deliveries.poll().await.unwrap(),
        "the late-committing member's row is materialized and due"
    );

    assert_eq!(
        row(&pool, workspace, "early-stamp@acme.com")
            .await
            .get::<_, String>("state"),
        STATE_DELIVERED,
        "the member whose seat committed late is enumerated and sent to"
    );
    assert_eq!(ses_sends(&fleet.ses), 2);
}

#[tokio::test]
async fn a_sweep_walks_the_source_to_its_last_page() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    let invitations: Vec<Listed> = (0..SOURCE_PAGE * 2 + 1)
        .map(|which| {
            listed(
                workspace,
                &format!("teammate-{which}@acme.com"),
                "admin@acme.com",
                "acme.com",
                "2026-08-18T10:00:00Z",
            )
        })
        .collect();
    let expected = invitations.len() as i64;
    let fleet = fleet(pool.clone(), invitations, sent_ok()).await;
    assert!(fleet.deliveries.poll().await.unwrap());

    let materialized: i64 = pool
        .get()
        .await
        .unwrap()
        .query_one(
            "select count(*) from ufo_control.invite_delivery where workspace_id = $1",
            &[&workspace],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        materialized, expected,
        "a source longer than one page is walked to its end, not truncated at the first"
    );
}

#[tokio::test]
async fn every_sweep_asks_for_the_whole_source() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at) \
             values ($1, 'teammate@acme.com', 'delivered', 'admin@acme.com', 'acme.com', \
                     '2026-08-18T10:00:00Z')",
            &[&workspace],
        )
        .await
        .unwrap();
    let fleet = fleet(pool.clone(), vec![], vec![]).await;
    assert!(!fleet.deliveries.poll().await.unwrap());

    assert_eq!(
        fleet.source.asked.lock().unwrap()[0],
        "/internal/onboard/invitations",
        "a sweep carries no mark from what this ledger already holds"
    );
}

#[tokio::test]
async fn rearming_returns_a_failed_row_and_leaves_a_delivered_one() {
    let pool = ledger_pool().await;
    let workspace = Uuid::new_v4();
    pool.get()
        .await
        .unwrap()
        .execute(
            "insert into ufo_control.invite_delivery \
             (workspace_id, email, state, invited_by, workspace_label, invited_at, sent_at, \
              attempts, last_error) \
             values ($1, 'failed@acme.com', 'failed', 'admin@acme.com', 'acme.com', now(), now(), \
                     8, 'not verified'), \
                    ($1, 'done@acme.com', 'delivered', 'admin@acme.com', 'acme.com', now(), \
                     now(), 1, null)",
            &[&workspace],
        )
        .await
        .unwrap();

    assert!(
        rearm_failed_delivery(&pool, workspace, "failed@acme.com")
            .await
            .unwrap()
            .is_some(),
        "the failed row is re-armed"
    );
    let armed = row(&pool, workspace, "failed@acme.com").await;
    assert_eq!(armed.get::<_, String>("state"), STATE_PENDING);
    assert_eq!(armed.get::<_, i32>("attempts"), 0);
    assert!(
        armed
            .get::<_, Option<chrono::DateTime<chrono::Utc>>>("sent_at")
            .is_none(),
        "an operator re-arms having decided the message never landed"
    );

    assert!(
        rearm_failed_delivery(&pool, workspace, "done@acme.com")
            .await
            .unwrap()
            .is_none(),
        "a delivered row is untouchable"
    );
    assert_eq!(
        row(&pool, workspace, "done@acme.com")
            .await
            .get::<_, String>("state"),
        STATE_DELIVERED
    );
}
