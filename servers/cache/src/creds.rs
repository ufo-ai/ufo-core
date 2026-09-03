use std::collections::HashMap;
use std::time::{Duration, Instant};

use serde::Deserialize;
use tokio::sync::Mutex;

/// The credential and isolation principal the control plane returns for one upstream. The daemon
/// mints nothing; it can only ask for the credential of the principal the proxy stamped on the
/// request — workspace, user, and the run or probe token the sandbox presented — so it can never
/// obtain another customer's token.
#[derive(Clone, Debug)]
pub struct Resolved {
    /// Basic-auth username and secret to use upstream. `None` => fetch anonymously.
    pub username: Option<String>,
    pub token: Option<String>,
    /// Mirror-isolation key. Opaque path segment dictated by the control plane (`public`, or
    /// `w<ws>-<account>`). Never interpreted here.
    pub principal: String,
}

#[derive(Deserialize)]
struct CallbackBody {
    #[serde(default)]
    username: Option<String>,
    #[serde(default)]
    token: Option<String>,
    principal: String,
}

/// TTL for a cached credential. The control plane is re-asked for the principal's connector OAuth
/// token once this elapses; the principal is deterministic, so a re-resolve never moves a repo's
/// mirror.
const CREDENTIAL_TTL: Duration = Duration::from_secs(240);

/// Asks the control plane's `/internal/git-credential` for the credential of the principal the
/// proxy stamped — workspace, user, token — and holds each answer for `CREDENTIAL_TTL`. The only
/// secret the daemon carries itself is the control token this callback authenticates with.
pub struct CredentialClient {
    http: reqwest::Client,
    endpoint: String,
    control_token: String,
    cache: Mutex<HashMap<String, (Resolved, Instant)>>,
}

impl CredentialClient {
    pub fn new(control_url: &str, control_token: String) -> Self {
        Self {
            http: reqwest::Client::new(),
            endpoint: format!(
                "{}/internal/git-credential",
                control_url.trim_end_matches('/')
            ),
            control_token,
            cache: Mutex::new(HashMap::new()),
        }
    }

    pub async fn resolve(
        &self,
        workspace: &str,
        user: &str,
        proxy_auth: &str,
        host: &str,
        repo_path: &str,
    ) -> Result<Resolved, String> {
        let key = format!("{workspace}\0{user}\0{proxy_auth}\0{host}\0{repo_path}");
        if let Some((resolved, at)) = self.cache.lock().await.get(&key) {
            if at.elapsed() < CREDENTIAL_TTL {
                return Ok(resolved.clone());
            }
        }
        let resolved = self
            .fetch(workspace, user, proxy_auth, host, repo_path)
            .await?;
        self.cache
            .lock()
            .await
            .insert(key, (resolved.clone(), Instant::now()));
        Ok(resolved)
    }

    async fn fetch(
        &self,
        workspace: &str,
        user: &str,
        proxy_auth: &str,
        host: &str,
        repo_path: &str,
    ) -> Result<Resolved, String> {
        let response = self
            .http
            .post(&self.endpoint)
            .bearer_auth(&self.control_token)
            .json(&serde_json::json!({
                "workspace_id": workspace,
                "user_id": user,
                "proxy_auth": proxy_auth,
                "host": host,
                "repo_path": repo_path,
            }))
            .send()
            .await
            .map_err(|e| format!("credential callback: {e}"))?;
        if !response.status().is_success() {
            return Err(format!("credential callback: HTTP {}", response.status()));
        }
        let body: CallbackBody = response
            .json()
            .await
            .map_err(|e| format!("credential callback body: {e}"))?;
        Ok(Resolved {
            username: body.username,
            token: body.token,
            principal: body.principal,
        })
    }
}
