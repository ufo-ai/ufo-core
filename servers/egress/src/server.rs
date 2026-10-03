//! The egress proxy wire: accept CONNECTs, verify the token, gate on the control authorize RPC,
//! resolve rules (cached by generation), and dispatch — opaque tunnel, TLS-terminated MITM
//! (inject), cache-daemon relay, or live-turn tool bridge — metering off the relay path.
//! This is the whole data plane; every policy decision comes from `Control`.
//!
//! Names resolve through the pod's own resolver (`/etc/resolv.conf` → CoreDNS), never the DNS
//! library default's public servers: resolved sandbox hostnames stay inside the cluster,
//! split-horizon and CoreDNS caching apply, and the OS resolver the cache connect uses answers the
//! same way, so one name never resolves two ways. A box with no readable resolv.conf falls back to
//! the default. The MITM resolves nothing at all — it connects to the address the pin vetted. An
//! exact scope admits its hosts by name and resolves nothing, except a pinned one, whose host a
//! workspace admin wrote rather than this deploy: that name is resolved as an internet host is and
//! refused when it answers a private address, since an exact scope is otherwise the one path
//! around the check.
//!
//! A service host that fronts real origins (the cache) re-originates by the request's own path
//! when its daemon is down or unconfigured — the git host lives in `/git/<host>/…`. A service that
//! IS the origin (preview) fronts nothing public and never re-originates: an absent daemon is
//! answered 502 inside the tunnel the client already opened. Gating on the daemon rather than the
//! path is what keeps the preview host from becoming a second, ungated route to `github.com`.
//!
//! A `Residential` rule's host leaves through the provider gateway `UFO_EGRESS_RESIDENTIAL_PROXY`
//! names rather than the cluster's own address: the wire opens a CONNECT to the gateway and carries
//! the tunnel (or the MITM's upstream leg) inside it. The gateway resolves the origin on its own
//! network, so such a host is pinned to no address here — the private-address check guards a dial
//! this process makes, and this one leaves through a consumer exit that reaches nothing of ours. A
//! rule whose deploy configures no gateway is answered 502, as the preview host is: the origin that
//! refuses a datacenter address is why the rule named the host, so falling back to the direct exit
//! would send the request from exactly the address it must not come from.
//!
//! A sentinel keeps its prefix through the swap only under the schemes `SWAPPABLE_SCHEMES` names,
//! decided here and never by the sandbox; `keyed_connectors` declares the same set. `Basic` is not
//! a prefix scheme: a sentinel rides as the password half of the encoded `user:password`, matched
//! by its decoded password and re-encoded with the same user around the real secret, so the
//! credential keeps its shape on any host, whichever client composed it.
//!
//! SIGTERM stops accepting, then lets in-flight tunnels finish within the deploy's
//! `[serve] graceful_shutdown_seconds` (300s testing, 600s prod) before aborting any straggler, so
//! a rollout drains sandbox egress as long as serve's own turns. The relay mirrors Python's
//! FIRST_COMPLETED: the client-first branch hands the downstream its idle-timeout window, and the
//! upstream-first branch abandons the client->upstream pump so a half-open client cannot park the
//! connection.

use std::collections::HashMap;
use std::future::Future;
use std::net::{Ipv4Addr, SocketAddr};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use base64::Engine;
use hickory_resolver::config::{ResolverConfig, ResolverOpts};
use hickory_resolver::TokioAsyncResolver;
use rustls::pki_types::ServerName;
use rustls::ClientConfig;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;
use tokio::task::JoinSet;
use tokio::time::timeout;
use tokio_rustls::{TlsAcceptor, TlsConnector};
use uuid::Uuid;

use crate::config::ResidentialExit;
use crate::control::Control;
use crate::meter::MeterSink;
use crate::tls::{upstream_client_config, LeafStore};
use crate::token::principal_from_proxy_auth;
use crate::types::{
    MeterRecord, Principal, Rule, RunActor, ToolBridgeResponse, REQUEST_METER_DIMENSION,
    TOKENS_DIMENSION,
};
use crate::usage::HttpTokenUsage;

const RELAY_CHUNK_BYTES: usize = 65536;
const MAX_HEADER_BYTES: usize = 65536;
const PROXY_HEADER_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_PROXY_CONNECTIONS: usize = 512;
const MAX_PROXY_CONNECTIONS_PER_WORKSPACE: usize = 64;
const CONNECT_UPSTREAM_TIMEOUT: Duration = Duration::from_secs(30);
const RELAY_RESPONSE_IDLE_TIMEOUT: Duration = Duration::from_secs(300);
const DEFAULT_HTTPS_PORT: u16 = 443;
const RULE_CACHE_MAX: usize = 4096;
const RULE_CACHE_TTL: Duration = Duration::from_secs(240);
const MAX_TOOL_BRIDGE_BODY_BYTES: usize = 1_048_576;
const MAX_REFUSAL_DRAIN_BYTES: usize = 8 * 1_048_576;
const REFUSAL_DRAIN_TIMEOUT: Duration = Duration::from_secs(5);
const EGRESS_AUTHORIZATION_UNAVAILABLE: &str = "egress authorization unavailable";

/// The synthetic git-cache origins the daemon mirrors; a git-path fall-through refuses any other.
const CACHE_GIT_HOSTS: [&str; 1] = ["github.com"];

/// The preview service's host, the other end of core's `sandbox/preview.py` constant: the one
/// `Service` rule the preview daemon owns. Every other service host is the cache's.
const PREVIEW_HOST: &str = "preview.ufo.internal";
const TOOL_BRIDGE_HOST: &str = "tools.ufo.internal";

const SERVICE_STRIPPED: [&[u8]; 8] = [
    b"x-ufo-workspace",
    b"x-ufo-user",
    b"x-ufo-proxy-auth",
    b"x-forwarded-proto",
    b"connection",
    b"keep-alive",
    b"proxy-connection",
    b"proxy-authorization",
];
const DIRECT_STRIPPED: [&[u8]; 8] = [
    b"host",
    b"x-ufo-workspace",
    b"x-ufo-user",
    b"x-ufo-proxy-auth",
    b"connection",
    b"keep-alive",
    b"proxy-connection",
    b"proxy-authorization",
];

/// The local daemons a matched `Service` rule relays to, each owning the hosts it serves: the cache
/// fronts public git and package origins, the preview service owns `PREVIEW_HOST` alone. Unset means
/// this deploy runs that daemon nowhere — the cache's hosts then dispatch as ordinary egress, while
/// the preview host, which nothing else serves, answers 502.
#[derive(Clone, Debug, Default)]
pub struct ServiceDaemons {
    pub cache: Option<String>,
    pub preview: Option<String>,
}

pub struct EgressProxy {
    control: Arc<Control>,
    leaves: Arc<LeafStore>,
    meter: MeterSink,
    token_secret: Vec<u8>,
    daemons: ServiceDaemons,
    public_url: Option<String>,
    graceful_shutdown: Duration,
    upstream_tls: Arc<ClientConfig>,
    dns: Option<Arc<Dns>>,
    residential: Option<Arc<ResidentialExit>>,
}

impl EgressProxy {
    pub fn new(
        control: Arc<Control>,
        leaves: Arc<LeafStore>,
        meter: MeterSink,
        token_secret: Vec<u8>,
        daemons: ServiceDaemons,
        public_url: Option<String>,
        graceful_shutdown: Duration,
    ) -> EgressProxy {
        EgressProxy {
            control,
            leaves,
            meter,
            token_secret,
            daemons,
            public_url,
            graceful_shutdown,
            upstream_tls: upstream_client_config(),
            dns: None,
            residential: None,
        }
    }

    /// Carry every `Residential` rule's host through this provider gateway. A deploy that
    /// configures none leaves it unset, and such a host is refused rather than dialed direct.
    pub fn through_residential(mut self, exit: ResidentialExit) -> EgressProxy {
        self.residential = Some(Arc::new(exit));
        self
    }

    /// Point the re-originating (upstream) TLS at a caller-supplied trust root. Tests trust a local
    /// origin's CA here; production keeps the webpki roots `new` installs.
    pub fn trust_upstream(mut self, config: Arc<ClientConfig>) -> EgressProxy {
        self.upstream_tls = config;
        self
    }

    /// Answer the DNS pin from a caller-supplied source instead of the pod's resolver. Tests pin a
    /// name to a fixed address here; production keeps the system resolver `serve_listener` builds.
    pub fn resolve_with(mut self, dns: Dns) -> EgressProxy {
        self.dns = Some(Arc::new(dns));
        self
    }

    /// Bind `bind` and serve until `shutdown` resolves.
    pub async fn serve(
        &self,
        bind: SocketAddr,
        shutdown: impl Future<Output = ()>,
    ) -> anyhow::Result<()> {
        let listener = TcpListener::bind(bind).await?;
        tracing::info!(addr = %listener.local_addr()?, "ufo-egress listening");
        self.serve_listener(listener, shutdown).await
    }

    /// Serve on an already-bound listener — the seam tests use to learn the ephemeral port before
    /// driving a real CONNECT.
    pub async fn serve_listener(
        &self,
        listener: TcpListener,
        shutdown: impl Future<Output = ()>,
    ) -> anyhow::Result<()> {
        // Resolve through the pod's own resolver; the DNS library default's public servers would
        // leave the cluster. See the module doc.
        let dns = self.dns.clone().unwrap_or_else(|| {
            Arc::new(Dns::System(Box::new(
                TokioAsyncResolver::tokio_from_system_conf().unwrap_or_else(|error| {
                    tracing::warn!(error = %error, "egress.resolv_conf_unreadable");
                    TokioAsyncResolver::tokio(ResolverConfig::default(), ResolverOpts::default())
                }),
            )))
        });
        let shared = Arc::new(Shared {
            control: self.control.clone(),
            leaves: self.leaves.clone(),
            meter: self.meter.clone(),
            token_secret: self.token_secret.clone(),
            daemons: self.daemons.clone(),
            upstream_tls: self.upstream_tls.clone(),
            dns,
            caps: Arc::new(Caps::new()),
            rule_cache: Mutex::new(HashMap::new()),
            residential: self.residential.clone(),
        });
        let mut tasks: JoinSet<()> = JoinSet::new();
        tokio::pin!(shutdown);
        loop {
            tokio::select! {
                _ = &mut shutdown => break,
                accepted = listener.accept() => {
                    match accepted {
                        Ok((stream, _addr)) => {
                            let _ = stream.set_nodelay(true);
                            let shared = shared.clone();
                            tasks.spawn(async move { handle_connection(shared, stream).await });
                            while tasks.try_join_next().is_some() {}
                        }
                        Err(error) => tracing::warn!(error = %error, "egress.accept_failed"),
                    }
                }
            }
        }
        // Stop accepting, then drain in-flight tunnels within the deploy's grace window before
        // aborting any straggler. See the module doc.
        let deadline = tokio::time::Instant::now() + self.graceful_shutdown;
        // Drain in-flight tunnels: each `Ok(Some)` is one finishing; `Ok(None)` (all done) or `Err`
        // (grace elapsed) ends the wait, after which `shutdown` aborts whatever is still live.
        while let Ok(Some(_)) = tokio::time::timeout_at(deadline, tasks.join_next()).await {}
        tasks.shutdown().await;
        Ok(())
    }
}

/// The `'static` slice of `EgressProxy` each connection task shares — the control client, the leaf
/// store, the metering channel, the local DNS resolver, and the connection-cap and rule-cache state.
struct Shared {
    control: Arc<Control>,
    leaves: Arc<LeafStore>,
    meter: MeterSink,
    token_secret: Vec<u8>,
    daemons: ServiceDaemons,
    upstream_tls: Arc<ClientConfig>,
    dns: Arc<Dns>,
    caps: Arc<Caps>,
    rule_cache: Mutex<HashMap<RuleKey, CachedRules>>,
    residential: Option<Arc<ResidentialExit>>,
}

struct CachedRules {
    expires_at: Instant,
    generation: i64,
    rules: Arc<Vec<Rule>>,
}

/// The rule cache key: a run token is its own key (workspace, turn and member are exactly what
/// its rules derive from); a probe keys on the things its rules depend on so two execs of one
/// watch share a resolution rather than churning the cache per single-use token.
#[derive(Clone, PartialEq, Eq, Hash)]
enum RuleKey {
    Run {
        workspace_id: Uuid,
        turn_id: Uuid,
        acts_for: RunActor,
    },
    Probe {
        workspace_id: Uuid,
        conversation_id: Uuid,
        member_id: Option<Uuid>,
        internet_access: bool,
    },
}

fn rule_key(principal: &Principal) -> RuleKey {
    match principal {
        Principal::Run(t) => RuleKey::Run {
            workspace_id: t.workspace_id,
            turn_id: t.turn_id,
            acts_for: t.acts_for,
        },
        Principal::Probe(t) => RuleKey::Probe {
            workspace_id: t.workspace_id,
            conversation_id: t.conversation_id,
            member_id: t.member_id,
            internet_access: t.internet_access,
        },
    }
}

/// Global and per-workspace connection admission. Acquisition hands back a guard that releases the
/// slot on drop, so a connection frees what it took by any return path.
struct Caps {
    active: AtomicUsize,
    per_workspace: Mutex<HashMap<Uuid, usize>>,
}

struct GlobalGuard(Arc<Caps>);
struct WorkspaceGuard {
    caps: Arc<Caps>,
    workspace_id: Uuid,
}

impl Caps {
    fn new() -> Caps {
        Caps {
            active: AtomicUsize::new(0),
            per_workspace: Mutex::new(HashMap::new()),
        }
    }

    fn acquire_global(self: &Arc<Self>) -> Option<GlobalGuard> {
        let prev = self.active.fetch_add(1, Ordering::SeqCst);
        if prev >= MAX_PROXY_CONNECTIONS {
            self.active.fetch_sub(1, Ordering::SeqCst);
            return None;
        }
        Some(GlobalGuard(self.clone()))
    }

    fn acquire_workspace(self: &Arc<Self>, workspace_id: Uuid) -> Option<WorkspaceGuard> {
        let mut map = self.per_workspace.lock().unwrap();
        let count = map.entry(workspace_id).or_insert(0);
        if *count >= MAX_PROXY_CONNECTIONS_PER_WORKSPACE {
            return None;
        }
        *count += 1;
        Some(WorkspaceGuard {
            caps: self.clone(),
            workspace_id,
        })
    }
}

impl Drop for GlobalGuard {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::SeqCst);
    }
}

impl Drop for WorkspaceGuard {
    fn drop(&mut self) {
        let mut map = self.caps.per_workspace.lock().unwrap();
        if let Some(count) = map.get_mut(&self.workspace_id) {
            *count -= 1;
            if *count == 0 {
                map.remove(&self.workspace_id);
            }
        }
    }
}

async fn handle_connection(shared: Arc<Shared>, mut stream: TcpStream) {
    let _global = match shared.caps.acquire_global() {
        Some(guard) => guard,
        None => {
            let _ = respond(&mut stream, 503, "proxy connection capacity reached").await;
            return;
        }
    };
    let (line, headers) = match read_head(&mut stream).await {
        ReadHead::Ok { line, headers, .. } => (line, headers),
        ReadHead::Refuse { status, message } => {
            let _ = respond(&mut stream, status, message).await;
            return;
        }
        ReadHead::Closed => return,
    };
    let line_text = String::from_utf8_lossy(&line);
    let parts: Vec<&str> = line_text.split_whitespace().collect();
    if parts.len() != 3 {
        let _ = respond(&mut stream, 400, "malformed proxy request line").await;
        return;
    }
    let (method, target) = (parts[0], parts[1]);
    if method != "CONNECT" {
        let _ = respond(&mut stream, 405, "only CONNECT is proxied").await;
        return;
    }
    let (host, port_text) = match target.split_once(':') {
        Some((h, p)) => (h, p),
        None => (target, ""),
    };
    let port: u16 = if port_text.is_empty() {
        DEFAULT_HTTPS_PORT
    } else {
        match port_text.parse() {
            Ok(p) => p,
            Err(_) => {
                let _ = respond(&mut stream, 400, "invalid CONNECT port").await;
                return;
            }
        }
    };
    if port == 0 {
        let _ = respond(&mut stream, 400, "invalid CONNECT port").await;
        return;
    }
    let host = host.to_string();
    let proxy_auth = proxy_authorization(&headers);
    let refused = format!("egress to {host} is not permitted");

    let principal = match principal_from_proxy_auth(&shared.token_secret, &proxy_auth) {
        Some(p) => p,
        None => {
            let _ = respond(&mut stream, 403, &refused).await;
            return;
        }
    };
    let generation = match shared.control.authorize(&proxy_auth).await {
        Ok(Some(g)) => g,
        Ok(None) => {
            let _ = respond(&mut stream, 403, &refused).await;
            return;
        }
        Err(error) => {
            tracing::error!(error = %error, "egress.authorize_failed");
            let _ = respond(&mut stream, 503, EGRESS_AUTHORIZATION_UNAVAILABLE).await;
            return;
        }
    };
    let _workspace = match shared.caps.acquire_workspace(principal.workspace_id()) {
        Some(guard) => guard,
        None => {
            let _ = respond(
                &mut stream,
                429,
                "workspace proxy connection capacity reached",
            )
            .await;
            return;
        }
    };
    let rules = match rules_for(&shared, &proxy_auth, &principal, generation).await {
        Ok(rules) => rules,
        Err(error) => {
            tracing::error!(error = %error, "egress.resolve_failed");
            let _ = respond(&mut stream, 503, EGRESS_AUTHORIZATION_UNAVAILABLE).await;
            return;
        }
    };

    // The preview service originates its own host, so it takes the service path with no daemon
    // configured; the cache fronts real origins, so its host dispatches as ordinary egress.
    if host == TOOL_BRIDGE_HOST && find_service(&rules, &host).is_some() {
        tool_bridge(&shared, stream, &host, &proxy_auth).await;
        return;
    }
    if let Some(daemon_prefix) = find_service(&rules, &host) {
        let injections = injections_for(&rules, &host);
        if host == PREVIEW_HOST {
            let daemon = shared.daemons.preview.clone();
            service(
                &shared,
                stream,
                host,
                principal,
                &proxy_auth,
                daemon_prefix,
                ServiceTarget::Preview(daemon),
                injections,
            )
            .await;
            return;
        }
        if let Some(daemon) = shared.daemons.cache.clone() {
            service(
                &shared,
                stream,
                host,
                principal,
                &proxy_auth,
                daemon_prefix,
                ServiceTarget::Cache(daemon),
                injections,
            )
            .await;
            return;
        }
    }

    // A pinned scope's host was written by a workspace admin, so it is resolved here and refused on
    // a private address. See the module doc.
    let scopes: Vec<bool> = rules
        .iter()
        .filter_map(|r| match r {
            Rule::Scope {
                allowed_hosts,
                pinned,
            } if allowed_hosts.contains(&host) => Some(*pinned),
            _ => None,
        })
        .collect();
    let exactly_scoped = !scopes.is_empty();
    let pinned_scope = scopes.iter().any(|pinned| *pinned);
    if !exactly_scoped && !rules.iter().any(|r| matches!(r, Rule::Internet)) {
        let _ = respond(&mut stream, 403, &refused).await;
        return;
    }
    let residential = rules
        .iter()
        .any(|r| matches!(r, Rule::Residential { host: h } if *h == host));
    let exit = match (residential, &shared.residential) {
        (false, _) => None,
        (true, Some(exit)) => Some(exit.clone()),
        (true, None) => {
            tracing::warn!(host = %host, "egress.residential_exit_unconfigured");
            let _ = respond(
                &mut stream,
                502,
                &format!("residential egress for {host} is not configured"),
            )
            .await;
            return;
        }
    };
    let mut connect_host = host.clone();
    if (!exactly_scoped || pinned_scope) && exit.is_none() {
        match resolve_public(&shared.dns, &host).await {
            Ok(pinned) => connect_host = pinned,
            Err(ResolveError::Forbidden) => {
                let _ = respond(&mut stream, 403, &refused).await;
                return;
            }
            Err(ResolveError::Unreachable) => {
                let _ = respond(&mut stream, 502, &format!("cannot reach {host}")).await;
                return;
            }
        }
    }
    // Every dimension `sandbox_egress_total` meters this host under: each matching `Meter` rule,
    // plus the `requests` the internet path adds when the host is not exactly scoped.
    let mut metric_dims: Vec<String> = rules
        .iter()
        .filter_map(|r| match r {
            Rule::Meter { host: h, dimension } if *h == host => Some(dimension.clone()),
            _ => None,
        })
        .collect();
    if !exactly_scoped {
        metric_dims.push(REQUEST_METER_DIMENSION.to_string());
    }
    let metering = Metering {
        egress: !exactly_scoped
            || rules.iter().any(|r| {
                matches!(r, Rule::Meter { host: h, dimension } if *h == host && dimension != TOKENS_DIMENSION)
            }),
        tokens: rules.iter().any(|r| {
            matches!(r, Rule::Meter { host: h, dimension } if *h == host && dimension == TOKENS_DIMENSION)
        }),
        metric_dims,
    };
    let injections = injections_for(&rules, &host);
    let target = ConnectTarget {
        host: &host,
        connect_host: &connect_host,
        port,
        exit: exit.as_deref(),
    };

    if injections.is_empty() {
        tunnel(&shared, stream, target, principal, &metering).await;
    } else {
        mitm(&shared, stream, target, &injections, principal, &metering).await;
    }
}

/// The egress `requests` ledger charge, the model-`tokens` charge accumulated off the wire, and the
/// `sandbox_egress_total{host, dimension}` counter that fires once per metered CONNECT.
struct Metering {
    egress: bool,
    tokens: bool,
    metric_dims: Vec<String>,
}

async fn emit_metrics(shared: &Arc<Shared>, host: &str, dimensions: &[String], workspace_id: Uuid) {
    for dimension in dimensions {
        shared
            .meter
            .enqueue(MeterRecord::Metric {
                host: host.to_string(),
                dimension: dimension.clone(),
                workspace_id,
            })
            .await;
    }
}

async fn rules_for(
    shared: &Arc<Shared>,
    proxy_auth: &str,
    principal: &Principal,
    generation: i64,
) -> anyhow::Result<Arc<Vec<Rule>>> {
    let key = rule_key(principal);
    {
        let cache = shared.rule_cache.lock().unwrap();
        if let Some(hit) = cache.get(&key) {
            if hit.expires_at > Instant::now() && hit.generation == generation {
                return Ok(hit.rules.clone());
            }
        }
    }
    let rules = Arc::new(shared.control.resolve(proxy_auth).await?);
    let mut cache = shared.rule_cache.lock().unwrap();
    if cache.len() >= RULE_CACHE_MAX {
        if let Some(evict) = cache.keys().next().cloned() {
            cache.remove(&evict);
        }
    }
    cache.insert(
        key,
        CachedRules {
            expires_at: Instant::now() + RULE_CACHE_TTL,
            generation,
            rules: rules.clone(),
        },
    );
    Ok(rules)
}

/// One host's injection candidates (header, sentinel, real), borrowed straight off the resolved
/// rule set.
struct Inj<'a> {
    header: &'a str,
    sentinel: &'a str,
    real: &'a str,
}

fn injections_for<'a>(rules: &'a [Rule], host: &str) -> Vec<Inj<'a>> {
    rules
        .iter()
        .filter_map(|r| match r {
            Rule::Injection {
                host: h,
                header,
                sentinel,
                real,
            } if h == host => Some(Inj {
                header,
                sentinel,
                real,
            }),
            _ => None,
        })
        .collect()
}

fn find_service(rules: &[Rule], host: &str) -> Option<Option<String>> {
    rules.iter().find_map(|r| match r {
        Rule::Service {
            host: h,
            daemon_prefix,
        } if h == host => Some(daemon_prefix.clone()),
        _ => None,
    })
}

#[derive(Clone, Copy)]
struct ConnectTarget<'a> {
    host: &'a str,
    connect_host: &'a str,
    port: u16,
    exit: Option<&'a ResidentialExit>,
}

/// The upstream socket a tunnel or a MITM carries: the dispatch-vetted address dialed straight out,
/// or the origin reached by name through the residential gateway, which dials it on its own network.
async fn dial_upstream(target: ConnectTarget<'_>) -> Option<TcpStream> {
    let dial = async {
        match target.exit {
            None => TcpStream::connect((target.connect_host, target.port))
                .await
                .map_err(anyhow::Error::from),
            Some(exit) => residential_connect(exit, target.host, target.port).await,
        }
    };
    match timeout(CONNECT_UPSTREAM_TIMEOUT, dial).await {
        Ok(Ok(sock)) => Some(sock),
        Ok(Err(error)) => {
            tracing::warn!(error = %error, host = %target.host, "egress.upstream_dial_failed");
            None
        }
        Err(_) => None,
    }
}

async fn residential_connect(
    exit: &ResidentialExit,
    host: &str,
    port: u16,
) -> anyhow::Result<TcpStream> {
    let mut sock = TcpStream::connect(exit.address.as_str()).await?;
    let mut request = format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n");
    if let Some(credential) = &exit.credential {
        request.push_str(&format!("Proxy-Authorization: {credential}\r\n"));
    }
    request.push_str("\r\n");
    sock.write_all(request.as_bytes()).await?;
    match read_head(&mut sock).await {
        ReadHead::Ok { line, leftover, .. } => {
            let answered = String::from_utf8_lossy(&line).to_string();
            match status_code(&line) {
                Some(200..=299) if leftover.is_empty() => Ok(sock),
                Some(200..=299) => Err(anyhow::anyhow!(
                    "residential proxy sent {} bytes before the tunnel",
                    leftover.len()
                )),
                _ => Err(anyhow::anyhow!("residential proxy answered {answered}")),
            }
        }
        _ => Err(anyhow::anyhow!(
            "residential proxy closed before it answered the tunnel"
        )),
    }
}

fn status_code(line: &[u8]) -> Option<u16> {
    String::from_utf8_lossy(line)
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}

async fn tunnel(
    shared: &Arc<Shared>,
    mut stream: TcpStream,
    target: ConnectTarget<'_>,
    principal: Principal,
    metering: &Metering,
) {
    let upstream = match dial_upstream(target).await {
        Some(sock) => sock,
        None => {
            let _ = respond(&mut stream, 502, &format!("cannot reach {}", target.host)).await;
            return;
        }
    };
    if stream
        .write_all(b"HTTP/1.1 200 Connection established\r\n\r\n")
        .await
        .is_err()
    {
        return;
    }
    emit_metrics(shared, target.host, &metering.metric_dims, principal.workspace_id()).await;
    if metering.egress {
        enqueue_egress(shared, &principal).await;
    }
    let (client_read, client_write) = stream.into_split();
    let (upstream_read, upstream_write) = upstream.into_split();
    relay(
        client_read,
        client_write,
        upstream_read,
        upstream_write,
        None,
    )
    .await;
}

/// Connect to the dispatch-vetted address while TLS uses the original host; resolving again could
/// send an injected secret to a private answer the pin never vetted.
async fn mitm(
    shared: &Arc<Shared>,
    stream: TcpStream,
    target: ConnectTarget<'_>,
    injections: &[Inj<'_>],
    principal: Principal,
    metering: &Metering,
) {
    let server_config = match shared.leaves.server_config(target.host).await {
        Ok(config) => config,
        Err(error) => {
            tracing::error!(error = %error, host = %target.host, "egress.leaf_failed");
            return;
        }
    };
    let mut stream = stream;
    if stream
        .write_all(b"HTTP/1.1 200 Connection established\r\n\r\n")
        .await
        .is_err()
    {
        return;
    }
    let mut client = match TlsAcceptor::from(server_config).accept(stream).await {
        Ok(tls) => tls,
        Err(_) => return,
    };
    let (line, headers, leftover) = match read_head(&mut client).await {
        ReadHead::Ok {
            line,
            headers,
            leftover,
        } => (line, headers, leftover),
        ReadHead::Refuse { status, message } => {
            let _ = respond(&mut client, status, message).await;
            return;
        }
        ReadHead::Closed => return,
    };

    // The counter fires once the tunnel is up and the request head is read — for a token-metered
    // host, before any usage is teed off the wire.
    emit_metrics(shared, target.host, &metering.metric_dims, principal.workspace_id()).await;

    let tcp = match dial_upstream(target).await {
        Some(sock) => sock,
        None => return,
    };
    let server_name = match ServerName::try_from(target.host.to_string()) {
        Ok(name) => name,
        Err(_) => return,
    };
    let upstream = match TlsConnector::from(shared.upstream_tls.clone())
        .connect(server_name, tcp)
        .await
    {
        Ok(tls) => tls,
        Err(_) => return,
    };
    let (upstream_read, mut upstream_write) = tokio::io::split(upstream);
    let mut head = Vec::new();
    head.extend_from_slice(&line);
    head.extend_from_slice(b"\r\n");
    head.extend_from_slice(&inject(&headers, injections));
    head.extend_from_slice(b"\r\n");
    head.extend_from_slice(&leftover);
    if upstream_write.write_all(&head).await.is_err() {
        return;
    }
    if metering.egress {
        enqueue_egress(shared, &principal).await;
    }
    let (client_read, client_write) = tokio::io::split(client);
    let accumulator = metering.tokens.then(|| HttpTokenUsage::new(target.host));
    let accumulator = relay(
        client_read,
        client_write,
        upstream_read,
        upstream_write,
        accumulator,
    )
    .await;
    if let Some(usage) = accumulator {
        meter_tokens(shared, target.host, &principal, usage).await;
    }
}

async fn tool_bridge(shared: &Arc<Shared>, stream: TcpStream, host: &str, proxy_auth: &str) {
    let server_config = match shared.leaves.server_config(host).await {
        Ok(config) => config,
        Err(error) => {
            tracing::error!(error = %error, "egress.tool_bridge_leaf_failed");
            return;
        }
    };
    let mut stream = stream;
    if stream
        .write_all(b"HTTP/1.1 200 Connection established\r\n\r\n")
        .await
        .is_err()
    {
        return;
    }
    let mut client = match TlsAcceptor::from(server_config).accept(stream).await {
        Ok(tls) => tls,
        Err(_) => return,
    };
    let (line, headers, leftover) = match read_head(&mut client).await {
        ReadHead::Ok {
            line,
            headers,
            leftover,
        } => (line, headers, leftover),
        ReadHead::Refuse { status, message } => {
            let _ = respond(&mut client, status, message).await;
            return;
        }
        ReadHead::Closed => return,
    };
    if line != b"POST /request HTTP/1.1" {
        let _ = respond(&mut client, 404, "tool bridge route not found").await;
        let _ = client.shutdown().await;
        return;
    }
    let body = match read_request_body(&mut client, &headers, leftover).await {
        Ok(body) => body,
        Err(refusal) => {
            let _ = respond(&mut client, refusal.status, &refusal.message).await;
            drain_refused(&mut client, refusal.pending).await;
            let _ = client.shutdown().await;
            return;
        }
    };
    let response = match shared.control.tool_bridge(proxy_auth, &body).await {
        Ok(response) => response,
        Err(error) => {
            tracing::info!(error = %error, "egress.tool_bridge_failed");
            let _ = respond(&mut client, 502, "tool bridge failed").await;
            let _ = client.shutdown().await;
            return;
        }
    };
    let _ = client
        .write_all(&tool_bridge_response_bytes(&response))
        .await;
    let _ = client.shutdown().await;
}

enum ServiceTarget {
    Cache(String),
    Preview(Option<String>),
}

#[allow(clippy::too_many_arguments)]
async fn service(
    shared: &Arc<Shared>,
    stream: TcpStream,
    host: String,
    principal: Principal,
    proxy_auth: &str,
    daemon_prefix: Option<String>,
    target: ServiceTarget,
    injections: Vec<Inj<'_>>,
) {
    let (daemon, allow_origin_fallthrough) = match target {
        ServiceTarget::Cache(daemon) => (Some(daemon), true),
        ServiceTarget::Preview(daemon) => (daemon, false),
    };
    let server_config = match shared.leaves.server_config(&host).await {
        Ok(config) => config,
        Err(_) => return,
    };
    let mut stream = stream;
    if stream
        .write_all(b"HTTP/1.1 200 Connection established\r\n\r\n")
        .await
        .is_err()
    {
        return;
    }
    let mut client = match TlsAcceptor::from(server_config).accept(stream).await {
        Ok(tls) => tls,
        Err(_) => return,
    };
    let (line, headers, leftover) = match read_head(&mut client).await {
        ReadHead::Ok {
            line,
            headers,
            leftover,
        } => (line, headers, leftover),
        ReadHead::Refuse { status, message } => {
            let _ = respond(&mut client, status, message).await;
            return;
        }
        ReadHead::Closed => return,
    };
    let (daemon_line, billed_host, fallthrough) = match &daemon_prefix {
        // A host that fronts real origins re-originates by the request's own path; a host that IS
        // the origin fails inside the tunnel. See the module doc.
        None if allow_origin_fallthrough => match service_origin(&line) {
            Some((origin_host, origin_line)) => {
                let fall = CACHE_GIT_HOSTS
                    .contains(&origin_host.as_str())
                    .then(|| (origin_host.clone(), origin_line.clone()));
                (line.clone(), Some(origin_host), fall)
            }
            None => (line.clone(), None, None),
        },
        None => (line.clone(), None, None),
        Some(prefix) => (
            prefix_target(&line, prefix),
            Some(host.clone()),
            Some((host.clone(), line.clone())),
        ),
    };
    // The daemon's address is a name resolved here, per connect, so a cluster DNS record that moves
    // is followed rather than pinned at boot.
    let connected = match &daemon {
        Some(address) => timeout(
            CONNECT_UPSTREAM_TIMEOUT,
            TcpStream::connect(address.as_str()),
        )
        .await
        .ok()
        .and_then(Result::ok),
        None => None,
    };
    let daemon_conn = match connected {
        Some(sock) => sock,
        None => {
            // The cache fronts a public origin and re-originates; a service that IS the origin
            // fails the request in the tunnel.
            match fallthrough {
                Some(origin) => {
                    service_direct(shared, client, origin, &headers, leftover, principal).await
                }
                None => {
                    let _ = respond(&mut client, 502, &format!("cannot reach {host}")).await;
                    drain_refused(&mut client, MAX_REFUSAL_DRAIN_BYTES).await;
                }
            }
            return;
        }
    };
    if let Some(host) = &billed_host {
        emit_metrics(shared, host, &[REQUEST_METER_DIMENSION.to_string()], principal.workspace_id()).await;
        enqueue_egress(shared, &principal).await;
    }
    let mut daemon_conn = daemon_conn;
    let mut head = Vec::new();
    head.extend_from_slice(&daemon_line);
    head.extend_from_slice(b"\r\n");
    head.extend_from_slice(&service_headers(
        &headers,
        &principal,
        proxy_auth,
        &injections,
    ));
    head.extend_from_slice(b"\r\n");
    head.extend_from_slice(&leftover);
    if daemon_conn.write_all(&head).await.is_err() {
        return;
    }
    let (client_read, client_write) = tokio::io::split(client);
    let (daemon_read, daemon_write) = daemon_conn.into_split();
    relay(client_read, client_write, daemon_read, daemon_write, None).await;
}

async fn service_direct(
    shared: &Arc<Shared>,
    mut client: tokio_rustls::server::TlsStream<TcpStream>,
    origin: (String, Vec<u8>),
    headers: &[Vec<u8>],
    leftover: Vec<u8>,
    principal: Principal,
) {
    let (host, origin_line) = origin;
    let tcp = match timeout(
        CONNECT_UPSTREAM_TIMEOUT,
        TcpStream::connect((host.as_str(), DEFAULT_HTTPS_PORT)),
    )
    .await
    {
        Ok(Ok(sock)) => sock,
        _ => {
            let _ = respond(&mut client, 502, "cache unavailable").await;
            return;
        }
    };
    let server_name = match ServerName::try_from(host.clone()) {
        Ok(name) => name,
        Err(_) => {
            let _ = respond(&mut client, 502, "cache unavailable").await;
            return;
        }
    };
    let upstream = match TlsConnector::from(shared.upstream_tls.clone())
        .connect(server_name, tcp)
        .await
    {
        Ok(tls) => tls,
        Err(_) => {
            let _ = respond(&mut client, 502, "cache unavailable").await;
            return;
        }
    };
    emit_metrics(shared, &host, &[REQUEST_METER_DIMENSION.to_string()], principal.workspace_id()).await;
    enqueue_egress(shared, &principal).await;
    let (upstream_read, mut upstream_write) = tokio::io::split(upstream);
    let mut head = Vec::new();
    head.extend_from_slice(&origin_line);
    head.extend_from_slice(b"\r\n");
    head.extend_from_slice(&direct_headers(headers, &host));
    head.extend_from_slice(b"\r\n");
    head.extend_from_slice(&leftover);
    if upstream_write.write_all(&head).await.is_err() {
        return;
    }
    let (client_read, client_write) = tokio::io::split(client);
    relay(
        client_read,
        client_write,
        upstream_read,
        upstream_write,
        None,
    )
    .await;
}

async fn enqueue_egress(shared: &Arc<Shared>, principal: &Principal) {
    let turn_id = match principal {
        Principal::Run(t) => Some(t.turn_id),
        Principal::Probe(_) => None,
    };
    shared
        .meter
        .enqueue(MeterRecord::Egress {
            workspace_id: principal.workspace_id(),
            turn_id,
        })
        .await;
}

async fn meter_tokens(
    shared: &Arc<Shared>,
    host: &str,
    principal: &Principal,
    mut usage: HttpTokenUsage,
) {
    match principal {
        Principal::Probe(t) => {
            tracing::warn!(host = %host, probe_id = %t.probe_id, "egress.probe_model_usage");
        }
        Principal::Run(t) => match usage.usage() {
            Some((model, split)) => {
                shared
                    .meter
                    .enqueue(MeterRecord::Tokens {
                        workspace_id: t.workspace_id,
                        turn_id: t.turn_id,
                        model,
                        usage: split,
                    })
                    .await;
            }
            None => tracing::info!(host = %host, "egress.tokens_usage_absent"),
        },
    }
}

// --- request head parsing ---------------------------------------------------------------------

enum ReadHead {
    Ok {
        line: Vec<u8>,
        headers: Vec<Vec<u8>>,
        leftover: Vec<u8>,
    },
    Refuse {
        status: u16,
        message: &'static str,
    },
    Closed,
}

async fn read_head<R: AsyncRead + Unpin>(reader: &mut R) -> ReadHead {
    match timeout(PROXY_HEADER_TIMEOUT, read_head_inner(reader)).await {
        Ok(head) => head,
        Err(_) => ReadHead::Refuse {
            status: 408,
            message: "request headers timed out",
        },
    }
}

async fn read_head_inner<R: AsyncRead + Unpin>(reader: &mut R) -> ReadHead {
    let mut buf: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        if let Some(pos) = find(&buf, b"\r\n\r\n") {
            let leftover = buf[pos + 4..].to_vec();
            return parse_head(&buf[..pos], leftover);
        }
        if buf.len() > MAX_HEADER_BYTES {
            return ReadHead::Refuse {
                status: 431,
                message: "request headers exceed the proxy limit",
            };
        }
        match reader.read(&mut chunk).await {
            Ok(0) => {
                if buf.is_empty() {
                    return ReadHead::Closed;
                }
                return parse_head(&buf, Vec::new());
            }
            Ok(n) => buf.extend_from_slice(&chunk[..n]),
            Err(_) => return ReadHead::Closed,
        }
    }
}

fn parse_head(head: &[u8], leftover: Vec<u8>) -> ReadHead {
    let mut lines = split_crlf(head).into_iter();
    let line = lines.next().unwrap_or_default();
    let headers: Vec<Vec<u8>> = lines.filter(|l| !l.is_empty()).collect();
    ReadHead::Ok {
        line,
        headers,
        leftover,
    }
}

fn proxy_authorization(headers: &[Vec<u8>]) -> String {
    let mut value = String::new();
    for line in headers {
        if let Some(colon) = line.iter().position(|&b| b == b':') {
            let name = String::from_utf8_lossy(&line[..colon]);
            if name.trim().eq_ignore_ascii_case("proxy-authorization") {
                value = String::from_utf8_lossy(&line[colon + 1..])
                    .trim()
                    .to_string();
            }
        }
    }
    value
}

// --- header rewriting -------------------------------------------------------------------------

/// The auth schemes a sentinel may ride behind and keep its prefix through the swap;
/// `keyed_connectors` declares the same set. See the module doc for `Basic`.
const SWAPPABLE_SCHEMES: [&str; 3] = ["bearer", "token", "api-key"];

fn inject(headers: &[Vec<u8>], candidates: &[Inj<'_>]) -> Vec<u8> {
    let mut out = Vec::new();
    for line in headers {
        let (name_raw, value_raw) = match line.iter().position(|&b| b == b':') {
            Some(colon) => (&line[..colon], &line[colon + 1..]),
            None => (line.as_slice(), &b""[..]),
        };
        let name = ascii_lower(btrim(name_raw));
        if name == b"connection" || name == b"proxy-connection" {
            continue;
        }
        let supplied = btrim(value_raw);
        let parts = split_ws_once(supplied);
        let basic = basic_credential(&parts);
        let chosen = candidates.iter().find(|c| {
            name == ascii_lower(c.header.as_bytes())
                && match &basic {
                    Some((_, password)) => password == c.sentinel.as_bytes(),
                    None => {
                        supplied == c.sentinel.as_bytes()
                            || (parts.len() == 2
                                && SWAPPABLE_SCHEMES
                                    .iter()
                                    .any(|scheme| parts[0].eq_ignore_ascii_case(scheme.as_bytes()))
                                && parts[1] == c.sentinel.as_bytes())
                    }
                }
        });
        match chosen {
            Some(c) => {
                let mut real = c.real.as_bytes().to_vec();
                if let Some((user, _)) = &basic {
                    let mut pair = user.clone();
                    pair.push(b':');
                    pair.extend_from_slice(&real);
                    real = format!(
                        "Basic {}",
                        base64::engine::general_purpose::STANDARD.encode(pair)
                    )
                    .into_bytes();
                } else if parts.len() == 2 && !real.contains(&b' ') {
                    let mut scoped = parts[0].to_vec();
                    scoped.push(b' ');
                    scoped.extend_from_slice(&real);
                    real = scoped;
                }
                out.extend_from_slice(c.header.as_bytes());
                out.extend_from_slice(b": ");
                out.extend_from_slice(&real);
                out.extend_from_slice(b"\r\n");
            }
            None => {
                out.extend_from_slice(line);
                out.extend_from_slice(b"\r\n");
            }
        }
    }
    out.extend_from_slice(b"connection: close\r\n");
    out
}

/// A `Basic` credential's decoded `(user, password)`; None for any other scheme, for base64 that
/// does not decode, and for a payload without a colon.
fn basic_credential(parts: &[&[u8]]) -> Option<(Vec<u8>, Vec<u8>)> {
    if parts.len() != 2 || !parts[0].eq_ignore_ascii_case(b"basic") {
        return None;
    }
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(parts[1])
        .ok()?;
    let colon = decoded.iter().position(|&b| b == b':')?;
    Some((decoded[..colon].to_vec(), decoded[colon + 1..].to_vec()))
}

fn tool_bridge_response_bytes(response: &ToolBridgeResponse) -> Vec<u8> {
    let dropped = [
        "content-length",
        "transfer-encoding",
        "content-encoding",
        "connection",
    ];
    let mut out = Vec::new();
    let status_line = format!(
        "HTTP/1.1 {} {}",
        response.status,
        reason_phrase(response.status)
    );
    out.extend_from_slice(status_line.trim_end().as_bytes());
    out.extend_from_slice(b"\r\n");
    for (name, value) in &response.headers {
        let lower = name.to_ascii_lowercase();
        if dropped.contains(&lower.as_str()) || has_crlf(name) || has_crlf(value) {
            continue;
        }
        out.extend_from_slice(format!("{name}: {value}").as_bytes());
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(format!("content-length: {}\r\n", response.body.len()).as_bytes());
    out.extend_from_slice(b"connection: close\r\n\r\n");
    out.extend_from_slice(&response.body);
    out
}

fn service_headers(
    headers: &[Vec<u8>],
    principal: &Principal,
    proxy_auth: &str,
    candidates: &[Inj<'_>],
) -> Vec<u8> {
    let filtered = headers
        .iter()
        .filter(|line| !SERVICE_STRIPPED.contains(&header_name_lower(line).as_slice()))
        .cloned()
        .collect::<Vec<_>>();
    let mut out = inject(&filtered, candidates);
    // The cache daemon spells absolute hrefs from the forwarded scheme, so the proxy
    // asserts the `https` the sandbox actually spoke rather than relaying a value the container set.
    out.extend_from_slice(b"x-forwarded-proto: https\r\n");
    out.extend_from_slice(
        format!(
            "x-ufo-workspace: {}\r\nx-ufo-proxy-auth: {}\r\n",
            principal.workspace_id(),
            proxy_auth,
        )
        .as_bytes(),
    );
    out
}

fn direct_headers(headers: &[Vec<u8>], host: &str) -> Vec<u8> {
    let mut out = Vec::new();
    for line in headers {
        let name = header_name_lower(line);
        if DIRECT_STRIPPED.contains(&name.as_slice()) {
            continue;
        }
        out.extend_from_slice(line);
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(format!("host: {host}\r\nconnection: close\r\n").as_bytes());
    out
}

fn prefix_target(line: &[u8], prefix: &str) -> Vec<u8> {
    let parts: Vec<&[u8]> = line.splitn(3, |&b| b == b' ').collect();
    if parts.len() != 3 {
        return line.to_vec();
    }
    let mut out = Vec::new();
    out.extend_from_slice(parts[0]);
    out.push(b' ');
    out.extend_from_slice(prefix.as_bytes());
    out.extend_from_slice(parts[1]);
    out.push(b' ');
    out.extend_from_slice(parts[2]);
    out
}

fn service_origin(line: &[u8]) -> Option<(String, Vec<u8>)> {
    let parts: Vec<&[u8]> = line.split(|&b| b == b' ').collect();
    if parts.len() != 3 {
        return None;
    }
    let target = std::str::from_utf8(parts[1]).ok()?;
    let mut segments = target.trim_start_matches('/').splitn(3, '/');
    let kind = segments.next()?;
    let host = segments.next()?;
    let rest = segments.next()?;
    if kind != "git" || host.is_empty() || rest.is_empty() {
        return None;
    }
    let mut origin = Vec::new();
    origin.extend_from_slice(parts[0]);
    origin.extend_from_slice(b" /");
    origin.extend_from_slice(rest.as_bytes());
    origin.push(b' ');
    origin.extend_from_slice(parts[2]);
    Some((host.to_string(), origin))
}

// --- tool bridge request body -----------------------------------------------------------------

struct Refusal {
    status: u16,
    message: String,
    pending: usize,
}

async fn read_request_body<R: AsyncRead + Unpin>(
    reader: &mut R,
    headers: &[Vec<u8>],
    leftover: Vec<u8>,
) -> Result<Vec<u8>, Refusal> {
    let mut length: i64 = 0;
    for line in headers {
        let colon = match line.iter().position(|&b| b == b':') {
            Some(c) => c,
            None => continue,
        };
        let name = ascii_lower(btrim(&line[..colon]));
        let value = btrim(&line[colon + 1..]);
        match name.as_slice() {
            b"content-length" => {
                length = match std::str::from_utf8(value)
                    .ok()
                    .and_then(|v| v.trim().parse().ok())
                {
                    Some(n) => n,
                    None => {
                        return Err(Refusal {
                            status: 411,
                            message: "tool bridge request declares an unparseable content-length"
                                .to_string(),
                            pending: MAX_REFUSAL_DRAIN_BYTES,
                        })
                    }
                };
            }
            b"transfer-encoding" => {
                return Err(Refusal {
                    status: 411,
                    message:
                        "tool bridge request body must declare a content-length; chunked is not \
                              accepted"
                            .to_string(),
                    pending: MAX_REFUSAL_DRAIN_BYTES,
                })
            }
            _ => {}
        }
    }
    if length == 0 {
        return Ok(Vec::new());
    }
    if length < 0 {
        return Err(Refusal {
            status: 400,
            message: format!("tool bridge request declares a negative content-length {length}"),
            pending: MAX_REFUSAL_DRAIN_BYTES,
        });
    }
    let length = length as usize;
    if length > MAX_TOOL_BRIDGE_BODY_BYTES {
        return Err(Refusal {
            status: 413,
            message: format!(
                "tool bridge request body is {length} bytes, over the \
                 {MAX_TOOL_BRIDGE_BODY_BYTES} byte limit"
            ),
            pending: length,
        });
    }
    let mut body = leftover;
    if body.len() > length {
        body.truncate(length);
    }
    while body.len() < length {
        let want = std::cmp::min(RELAY_CHUNK_BYTES, length - body.len());
        let mut chunk = vec![0u8; want];
        match reader.read(&mut chunk).await {
            Ok(0) | Err(_) => {
                return Err(Refusal {
                    status: 400,
                    message: format!(
                        "tool bridge request body ended after {} of {length} bytes",
                        body.len()
                    ),
                    pending: 0,
                })
            }
            Ok(n) => body.extend_from_slice(&chunk[..n]),
        }
    }
    Ok(body)
}

async fn drain_refused<R: AsyncRead + Unpin>(reader: &mut R, pending: usize) {
    let mut remaining = std::cmp::min(pending, MAX_REFUSAL_DRAIN_BYTES);
    let _ = timeout(REFUSAL_DRAIN_TIMEOUT, async {
        let mut chunk = vec![0u8; RELAY_CHUNK_BYTES];
        while remaining > 0 {
            let want = std::cmp::min(remaining, RELAY_CHUNK_BYTES);
            match reader.read(&mut chunk[..want]).await {
                Ok(0) | Err(_) => break,
                Ok(n) => remaining -= n,
            }
        }
    })
    .await;
}

// --- DNS-pin ----------------------------------------------------------------------------------

enum ResolveError {
    Forbidden,
    Unreachable,
}

/// Where the pin's A records come from. Production resolves through the pod's own resolver; a test
/// pins fixed answers so one name can answer a public address here while the system resolver a
/// connect-by-name would use answers a private one.
pub enum Dns {
    System(Box<TokioAsyncResolver>),
    Fixed(HashMap<String, Vec<Ipv4Addr>>),
}

impl Dns {
    async fn ipv4(&self, host: &str) -> Result<Vec<Ipv4Addr>, ResolveError> {
        match self {
            Dns::System(resolver) => match resolver.ipv4_lookup(host).await {
                Ok(lookup) => Ok(lookup.iter().map(|a| a.0).collect()),
                Err(_) => Err(ResolveError::Unreachable),
            },
            Dns::Fixed(answers) => answers.get(host).cloned().ok_or(ResolveError::Unreachable),
        }
    }
}

async fn resolve_public(dns: &Dns, host: &str) -> Result<String, ResolveError> {
    if host.contains(':') {
        return Err(ResolveError::Forbidden);
    }
    let addresses: Vec<Ipv4Addr> = if let Ok(ip) = host.parse::<Ipv4Addr>() {
        vec![ip]
    } else {
        dns.ipv4(host).await?
    };
    if addresses.is_empty() || addresses.iter().any(|a| !is_globally_routable(a)) {
        return Err(ResolveError::Forbidden);
    }
    Ok(addresses[0].to_string())
}

fn is_globally_routable(ip: &Ipv4Addr) -> bool {
    let octets = ip.octets();
    if octets[0] == 0
        || ip.is_loopback()
        || ip.is_private()
        || ip.is_link_local()
        || ip.is_multicast()
        || ip.is_broadcast()
    {
        return false;
    }
    if octets[0] == 100 && (0x40..0x80).contains(&octets[1]) {
        return false; // 100.64.0.0/10 CGNAT
    }
    if octets[0] >= 240 {
        return false; // 240.0.0.0/4 reserved
    }
    if octets[0] == 198 && (octets[1] & 0xfe) == 18 {
        return false; // 198.18.0.0/15 benchmarking (both 198.18.x and 198.19.x)
    }
    // Documentation, protocol-assignment, and relay-anycast /24s `ipaddress.is_global` also
    // excludes (192.88.99.0/24 is kept stricter than is_global, which admits it).
    let blocked_24: [[u8; 3]; 5] = [
        [192, 0, 0],    // 192.0.0.0/24 IETF protocol assignments
        [192, 0, 2],    // 192.0.2.0/24 TEST-NET-1
        [198, 51, 100], // 198.51.100.0/24 TEST-NET-2
        [203, 0, 113],  // 203.0.113.0/24 TEST-NET-3
        [192, 88, 99],  // 192.88.99.0/24 6to4 relay anycast
    ];
    if blocked_24
        .iter()
        .any(|prefix| octets[0] == prefix[0] && octets[1] == prefix[1] && octets[2] == prefix[2])
    {
        return false;
    }
    true
}

// --- relay ------------------------------------------------------------------------------------

async fn relay<CR, CW, UR, UW>(
    client_read: CR,
    client_write: CW,
    upstream_read: UR,
    upstream_write: UW,
    usage: Option<HttpTokenUsage>,
) -> Option<HttpTokenUsage>
where
    CR: AsyncRead + Unpin + Send + 'static,
    CW: AsyncWrite + Unpin + Send + 'static,
    UR: AsyncRead + Unpin + Send + 'static,
    UW: AsyncWrite + Unpin + Send + 'static,
{
    let (done_tx, done_rx) = oneshot::channel();
    let mut down = tokio::spawn(pump_down(upstream_read, client_write, usage, done_rx));
    // Mirror Python's FIRST_COMPLETED relay; the module doc names which branch abandons which pump.
    tokio::select! {
        _ = pump_up(client_read, upstream_write, done_tx) => down.await.ok().flatten(),
        result = &mut down => result.ok().flatten(),
    }
}

async fn pump_up<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    mut reader: R,
    mut writer: W,
    done: oneshot::Sender<()>,
) {
    let mut buf = vec![0u8; RELAY_CHUNK_BYTES];
    loop {
        match reader.read(&mut buf).await {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                if writer.write_all(&buf[..n]).await.is_err() {
                    break;
                }
            }
        }
    }
    let _ = writer.shutdown().await;
    let _ = done.send(());
}

async fn pump_down<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    mut reader: R,
    mut writer: W,
    mut usage: Option<HttpTokenUsage>,
    mut done: oneshot::Receiver<()>,
) -> Option<HttpTokenUsage> {
    let mut buf = vec![0u8; RELAY_CHUNK_BYTES];
    let mut client_finished = false;
    loop {
        let read = if client_finished {
            match timeout(RELAY_RESPONSE_IDLE_TIMEOUT, reader.read(&mut buf)).await {
                Ok(result) => result,
                Err(_) => break,
            }
        } else {
            tokio::select! {
                _ = &mut done => {
                    client_finished = true;
                    continue;
                }
                result = reader.read(&mut buf) => result,
            }
        };
        match read {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                if writer.write_all(&buf[..n]).await.is_err() {
                    break;
                }
                if let Some(accumulator) = usage.as_mut() {
                    accumulator.feed(&buf[..n]);
                }
            }
        }
    }
    let _ = writer.shutdown().await;
    usage
}

async fn respond<W: AsyncWrite + Unpin>(
    writer: &mut W,
    status: u16,
    message: &str,
) -> std::io::Result<()> {
    let head = format!(
        "HTTP/1.1 {status} {}\r\ncontent-type: text/plain; charset=utf-8\r\ncontent-length: {}\r\n\
         connection: close\r\n\r\n",
        reason_phrase(status),
        message.len(),
    );
    writer.write_all(head.as_bytes()).await?;
    writer.write_all(message.as_bytes()).await?;
    writer.flush().await
}

// --- byte helpers -----------------------------------------------------------------------------

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn split_crlf(data: &[u8]) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    let mut rest = data;
    while let Some(pos) = find(rest, b"\r\n") {
        out.push(rest[..pos].to_vec());
        rest = &rest[pos + 2..];
    }
    out.push(rest.to_vec());
    out
}

fn btrim(bytes: &[u8]) -> &[u8] {
    let start = match bytes.iter().position(|b| !b.is_ascii_whitespace()) {
        Some(s) => s,
        None => return &[],
    };
    let end = bytes
        .iter()
        .rposition(|b| !b.is_ascii_whitespace())
        .unwrap();
    &bytes[start..=end]
}

fn ascii_lower(bytes: &[u8]) -> Vec<u8> {
    bytes.iter().map(|b| b.to_ascii_lowercase()).collect()
}

fn header_name_lower(line: &[u8]) -> Vec<u8> {
    let name = match line.iter().position(|&b| b == b':') {
        Some(colon) => &line[..colon],
        None => line,
    };
    ascii_lower(btrim(name))
}

fn split_ws_once(value: &[u8]) -> Vec<&[u8]> {
    let first = match value.iter().position(|b| b.is_ascii_whitespace()) {
        Some(pos) => pos,
        None => {
            return if value.is_empty() {
                Vec::new()
            } else {
                vec![value]
            }
        }
    };
    let head = &value[..first];
    let tail = btrim(&value[first..]);
    if tail.is_empty() {
        vec![head]
    } else {
        vec![head, tail]
    }
}

fn has_crlf(value: &str) -> bool {
    value.contains('\r') || value.contains('\n')
}

fn reason_phrase(status: u16) -> &'static str {
    match status {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        301 => "Moved Permanently",
        302 => "Found",
        304 => "Not Modified",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        411 => "Length Required",
        413 => "Request Entity Too Large",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        500 => "Internal Server Error",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn header_lines(raw: &[&str]) -> Vec<Vec<u8>> {
        raw.iter().map(|l| l.as_bytes().to_vec()).collect()
    }

    #[test]
    fn global_cap_admits_up_to_the_ceiling_then_refuses() {
        let caps = Arc::new(Caps::new());
        let mut guards = Vec::new();
        for _ in 0..MAX_PROXY_CONNECTIONS {
            guards.push(caps.acquire_global().expect("under the ceiling"));
        }
        assert!(caps.acquire_global().is_none(), "admitted past the ceiling");
        drop(guards.pop());
        assert!(caps.acquire_global().is_some(), "a freed slot is reusable");
    }

    #[test]
    fn workspace_cap_is_per_workspace_and_released_on_drop() {
        let caps = Arc::new(Caps::new());
        let one = Uuid::from_u128(1);
        let two = Uuid::from_u128(2);
        let mut held = Vec::new();
        for _ in 0..MAX_PROXY_CONNECTIONS_PER_WORKSPACE {
            held.push(
                caps.acquire_workspace(one)
                    .expect("under the per-ws ceiling"),
            );
        }
        assert!(
            caps.acquire_workspace(one).is_none(),
            "past the per-ws ceiling"
        );
        assert!(
            caps.acquire_workspace(two).is_some(),
            "a different workspace has its own budget"
        );
        held.clear();
        assert!(
            caps.acquire_workspace(one).is_some(),
            "released slots free the workspace"
        );
        assert!(
            !caps.per_workspace.lock().unwrap().contains_key(&two)
                || caps.per_workspace.lock().unwrap()[&two] == 0,
            "a drained workspace leaves no lingering row"
        );
    }

    #[test]
    fn ip_classifier_rejects_private_and_admits_public() {
        for blocked in [
            "10.0.0.1",
            "192.168.1.1",
            "172.16.5.4",
            "127.0.0.1",
            "169.254.1.1",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "255.255.255.255",
            "240.0.0.1",
            "198.18.0.5",
            "203.0.113.9",
        ] {
            assert!(
                !is_globally_routable(&blocked.parse().unwrap()),
                "{blocked} was admitted"
            );
        }
        for allowed in ["8.8.8.8", "1.1.1.1", "140.82.112.3", "93.184.216.34"] {
            assert!(
                is_globally_routable(&allowed.parse().unwrap()),
                "{allowed} was rejected"
            );
        }
    }

    #[test]
    fn inject_swaps_the_exact_sentinel_and_forces_close() {
        let headers = header_lines(&[
            "host: api.example.com",
            "authorization: Bearer SENT",
            "accept: */*",
        ]);
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: "real-key",
        }];
        let out = inject(&headers, &candidates);
        let text = String::from_utf8(out).unwrap();
        assert!(text.contains("authorization: Bearer real-key"), "{text}");
        assert!(text.contains("host: api.example.com"), "{text}");
        assert!(text.ends_with("connection: close\r\n"), "{text}");
    }

    #[test]
    fn inject_passes_a_foreign_sentinel_through_untouched() {
        let headers = header_lines(&["authorization: Bearer OTHER"]);
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: "real-key",
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(text.contains("authorization: Bearer OTHER"), "{text}");
    }

    #[test]
    fn inject_keeps_a_declared_scheme_prefix_that_is_not_bearer() {
        // PandaDoc takes its API key as `Authorization: API-Key <key>`, so the prefix has to
        // survive the swap for the keyed row to reach the wire authenticated.
        let headers = header_lines(&["authorization: API-Key SENT"]);
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: "real-key",
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(text.contains("authorization: API-Key real-key"), "{text}");
    }

    #[test]
    fn inject_leaves_an_undeclared_scheme_prefix_unswapped() {
        let headers = header_lines(&["authorization: Digest SENT"]);
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: "real-key",
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(text.contains("authorization: Digest SENT"), "{text}");
    }

    #[test]
    fn inject_keeps_a_token_scheme_prefix() {
        let headers = header_lines(&["authorization: token SENT"]);
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: "real-key",
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(text.contains("authorization: token real-key"), "{text}");
    }

    #[test]
    fn inject_swaps_a_bare_sentinel_for_a_space_bearing_secret() {
        let headers = header_lines(&["x-api-key: SENT"]);
        let candidates = [Inj {
            header: "x-api-key",
            sentinel: "SENT",
            real: "raw secret",
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(text.contains("x-api-key: raw secret"), "{text}");
    }

    fn basic(credential: &str) -> String {
        format!(
            "Basic {}",
            base64::engine::general_purpose::STANDARD.encode(credential)
        )
    }

    #[test]
    fn inject_swaps_a_sentinel_riding_as_a_basic_password() {
        // `gh auth git-credential` hands git `x-access-token:<token>`, so the sentinel arrives as a
        // Basic password and the swap keeps the user and scheme around the real secret.
        let headers = header_lines(&[
            "host: github.com",
            &format!("authorization: {}", basic("x-access-token:SENT")),
        ]);
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: "real-token",
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(
            text.contains(&format!(
                "authorization: {}\r\n",
                basic("x-access-token:real-token")
            )),
            "{text}"
        );
        assert!(!text.contains("SENT"), "{text}");
    }

    #[test]
    fn inject_passes_a_basic_credential_with_another_password_through_untouched() {
        let supplied = basic("x-access-token:OTHER");
        let headers = header_lines(&[&format!("authorization: {supplied}")]);
        let real = basic("x-access-token:real-token");
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: &real,
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(
            text.contains(&format!("authorization: {supplied}\r\n")),
            "{text}"
        );
    }

    #[test]
    fn inject_leaves_a_basic_value_that_is_not_base64_unswapped() {
        let headers = header_lines(&["authorization: Basic !!not-base64!!"]);
        let real = basic("x-access-token:real-token");
        let candidates = [Inj {
            header: "authorization",
            sentinel: "SENT",
            real: &real,
        }];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(
            text.contains("authorization: Basic !!not-base64!!\r\n"),
            "{text}"
        );
    }

    #[test]
    fn service_origin_rewrites_a_git_cache_path() {
        let (host, line) = service_origin(b"GET /git/github.com/o/r/info/refs HTTP/1.1").unwrap();
        assert_eq!(host, "github.com");
        assert_eq!(line, b"GET /o/r/info/refs HTTP/1.1");
        assert!(service_origin(b"GET /plain HTTP/1.1").is_none());
    }

    #[test]
    fn prefix_target_names_the_package_route() {
        assert_eq!(
            prefix_target(b"GET /lodash HTTP/1.1", "/pkg/registry.npmjs.org"),
            b"GET /pkg/registry.npmjs.org/lodash HTTP/1.1"
        );
    }

    #[test]
    fn service_headers_stamp_the_member_token_and_strip_container_claims() {
        use crate::types::{RunActor, RunToken};
        let principal = Principal::Run(RunToken {
            workspace_id: Uuid::from_u128(7),
            turn_id: Uuid::from_u128(8),
            acts_for: RunActor::Member(Uuid::from_u128(9)),
        });
        let headers = header_lines(&[
            "x-ufo-workspace: forged",
            "x-ufo-proxy-auth: forged",
            "x-forwarded-proto: forged",
            "accept: */*",
        ]);
        let text =
            String::from_utf8(service_headers(&headers, &principal, "Basic cnVu", &[])).unwrap();
        assert!(
            !text.contains("forged"),
            "container identity leaked: {text}"
        );
        assert!(
            text.contains(&format!("x-ufo-workspace: {}", Uuid::from_u128(7))),
            "{text}"
        );
        assert!(!text.contains("x-ufo-user:"), "{text}");
        assert!(text.contains("x-ufo-proxy-auth: Basic cnVu\r\n"), "{text}");
        assert!(text.contains("x-forwarded-proto: https"), "{text}");
        assert!(text.contains("connection: close"), "{text}");
    }

    #[test]
    fn rule_cache_keys_include_the_acting_member() {
        use crate::types::{RunActor, RunToken};

        let workspace_id = Uuid::from_u128(7);
        let turn_id = Uuid::from_u128(8);
        let first = Principal::Run(RunToken {
            workspace_id,
            turn_id,
            acts_for: RunActor::Member(Uuid::from_u128(9)),
        });
        let second = Principal::Run(RunToken {
            workspace_id,
            turn_id,
            acts_for: RunActor::Member(Uuid::from_u128(10)),
        });
        assert!(rule_key(&first) != rule_key(&second));
    }

    #[test]
    fn direct_headers_strip_container_claims_and_rewrite_the_origin() {
        // The cache-down fall-through re-originates at the public host, so the identity stamps a
        // service relay carries — and anything the container forged — never leave the proxy.
        let headers = header_lines(&[
            "host: cache.ufo.internal",
            "x-ufo-workspace: forged",
            "x-ufo-user: forged",
            "x-ufo-proxy-auth: forged",
            "proxy-authorization: Basic forged",
            "accept: */*",
        ]);
        let text = String::from_utf8(direct_headers(&headers, "github.com")).unwrap();
        assert!(
            !text.contains("forged"),
            "container identity leaked: {text}"
        );
        assert!(!text.contains("cache.ufo.internal"), "{text}");
        assert!(text.contains("accept: */*\r\n"), "{text}");
        assert!(text.contains("host: github.com\r\n"), "{text}");
        assert!(text.contains("connection: close\r\n"), "{text}");
    }

    #[test]
    fn inject_swaps_two_keys_on_one_request() {
        let headers = header_lines(&[
            "host: api.example.com",
            "authorization: Bearer SENT_AUTH",
            "x-api-key: SENT_KEY",
        ]);
        let candidates = [
            Inj {
                header: "authorization",
                sentinel: "SENT_AUTH",
                real: "auth-real",
            },
            Inj {
                header: "x-api-key",
                sentinel: "SENT_KEY",
                real: "key-real",
            },
        ];
        let text = String::from_utf8(inject(&headers, &candidates)).unwrap();
        assert!(text.contains("authorization: Bearer auth-real"), "{text}");
        assert!(text.contains("x-api-key: key-real"), "{text}");
    }

    #[test]
    fn ip_classifier_boundaries_track_python_is_global() {
        // The address just outside each blocked range is admitted, the first inside blocked — the
        // exact edges `ipaddress.is_global` draws.
        for admitted in [
            "100.63.255.255",
            "100.128.0.0",
            "172.15.255.255",
            "172.32.0.0",
            "198.20.0.1",
            "192.0.1.1",
        ] {
            assert!(
                is_globally_routable(&admitted.parse().unwrap()),
                "{admitted} was rejected"
            );
        }
        for blocked in [
            "100.64.0.0",
            "100.127.255.255",
            "172.16.0.0",
            "172.31.255.255",
            "224.0.0.0",
            "239.255.255.255",
            "198.18.0.0",
            "198.19.255.255",
            "192.0.0.0",
            "192.0.0.255",
        ] {
            assert!(
                !is_globally_routable(&blocked.parse().unwrap()),
                "{blocked} was admitted"
            );
        }
    }

    #[tokio::test]
    async fn relay_terminates_when_upstream_closes_while_the_client_lingers() {
        // A half-open client that never closes after reading the response: the relay cancels the
        // client->upstream pump so the exchange ends rather than parking.
        let (proxy_client, app) = tokio::io::duplex(1024);
        let (proxy_upstream, origin) = tokio::io::duplex(1024);
        let (client_read, client_write) = tokio::io::split(proxy_client);
        let (upstream_read, upstream_write) = tokio::io::split(proxy_upstream);
        let (mut app_read, _app_write) = tokio::io::split(app);

        tokio::spawn(async move {
            let mut origin = origin;
            origin.write_all(b"hello").await.unwrap();
            origin.shutdown().await.unwrap();
        });

        let relayed = timeout(
            Duration::from_secs(2),
            relay(
                client_read,
                client_write,
                upstream_read,
                upstream_write,
                None,
            ),
        )
        .await;
        assert!(
            relayed.is_ok(),
            "relay parked on the idle client after the upstream closed"
        );

        let mut delivered = Vec::new();
        app_read.read_to_end(&mut delivered).await.unwrap();
        assert_eq!(
            delivered, b"hello",
            "response was truncated before teardown"
        );
    }

    #[test]
    fn tool_bridge_response_bytes_drops_crlf_bearing_headers() {
        let response = ToolBridgeResponse {
            status: 200,
            headers: vec![
                ("x-good".to_string(), "fine".to_string()),
                ("x-evil".to_string(), "a\r\nInjected: 1".to_string()),
            ],
            body: b"hi".to_vec(),
        };
        let text = String::from_utf8(tool_bridge_response_bytes(&response)).unwrap();
        assert!(text.starts_with("HTTP/1.1 200 OK\r\n"), "{text}");
        assert!(text.contains("x-good: fine"), "{text}");
        assert!(
            !text.contains("Injected"),
            "header split slipped through: {text}"
        );
        assert!(text.contains("content-length: 2"), "{text}");
        assert!(text.ends_with("connection: close\r\n\r\nhi"), "{text}");
    }

    #[test]
    fn a_residential_gateway_answer_is_read_off_its_status_line() {
        assert_eq!(
            status_code(b"HTTP/1.1 200 Connection established"),
            Some(200)
        );
        assert_eq!(
            status_code(b"HTTP/1.1 407 Proxy Authentication Required"),
            Some(407)
        );
        assert_eq!(status_code(b"HTTP/1.1"), None);
        assert_eq!(status_code(b"nonsense"), None);
    }
}
