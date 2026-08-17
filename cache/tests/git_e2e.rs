mod common;

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use base64::Engine;
use sha2::Digest;
use ufo_cache::durable::Durable;
use ufo_cache::Config;

async fn git(args: &[&str], cwd: &Path) {
    git_with_env(args, cwd, &[]).await
}

async fn git_with_env(args: &[&str], cwd: &Path, envs: &[(&str, &str)]) {
    let mut cmd = tokio::process::Command::new("git");
    cmd.args(args)
        .current_dir(cwd)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@t")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@t");
    for (name, value) in envs {
        cmd.env(name, value);
    }
    let out = cmd.output().await.unwrap();
    assert!(
        out.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&out.stderr)
    );
}

/// Clone through the daemon the way the egress proxy drives it: with the trusted identity headers
/// the proxy stamps on every relayed request.
async fn clone_via_daemon(url: &str, dest: &Path, cwd: &Path) {
    git(
        &[
            "-c",
            "http.extraHeader=x-ufo-workspace: w1",
            "-c",
            "http.extraHeader=x-ufo-user: alice",
            "clone",
            "-q",
            url,
            dest.to_str().unwrap(),
        ],
        cwd,
    )
    .await;
}

/// Build a bare upstream repo at `<uproot>/acme/widget` containing hello.txt=world.
async fn seed_upstream(base: &Path) -> PathBuf {
    let work = base.join("work");
    tokio::fs::create_dir_all(&work).await.unwrap();
    git(&["init", "-q", "-b", "main", "."], &work).await;
    tokio::fs::write(work.join("hello.txt"), b"world")
        .await
        .unwrap();
    git(&["add", "."], &work).await;
    git(&["commit", "-q", "-m", "seed"], &work).await;

    let uproot = base.join("upstream");
    let bare = uproot.join("acme").join("widget");
    tokio::fs::create_dir_all(bare.parent().unwrap())
        .await
        .unwrap();
    git(
        &[
            "clone",
            "-q",
            "--bare",
            work.to_str().unwrap(),
            bare.to_str().unwrap(),
        ],
        base,
    )
    .await;
    uproot
}

/// Both levers off: every request fetches upstream and no pack is cached. Each lever test overrides
/// the one knob it exercises with struct-update syntax, so the tests that predate the levers keep
/// asserting the behaviour the daemon had without them.
fn config(state: PathBuf, control_url: String, allowed: &str) -> Config {
    Config {
        listen: "127.0.0.1:0".parse().unwrap(),
        state_root: state,
        control_url,
        control_token: "test".into(),
        disk_limit_bytes: 1 << 30,
        upstream_scheme: "http".into(),
        allowed_git_hosts: vec![allowed.to_string()],
        allowed_pkg_hosts: vec![],
        pkg_disk_limit_bytes: 1 << 30,
        git_fresh_ttl_secs: 0,
        pack_cache_bytes: 0,
        lfs_cache_bytes: 0,
    }
}

/// Push a new commit onto the bare upstream at `<uproot>/acme/widget` via a throwaway work tree.
async fn push_commit(uproot: &Path, workdir: &Path, file: &str, content: &str) {
    let bare = uproot.join("acme").join("widget");
    git(
        &[
            "clone",
            "-q",
            bare.to_str().unwrap(),
            workdir.to_str().unwrap(),
        ],
        uproot,
    )
    .await;
    tokio::fs::write(workdir.join(file), content).await.unwrap();
    git(&["add", "."], workdir).await;
    git(&["commit", "-q", "-m", "two"], workdir).await;
    git(&["push", "-q", "origin", "main"], workdir).await;
}

async fn git_stdout(args: &[&str], cwd: &Path) -> String {
    let out = tokio::process::Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .await
        .unwrap();
    assert!(out.status.success(), "git {args:?} failed");
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

async fn upstream_head(uproot: &Path) -> String {
    git_stdout(&["rev-parse", "HEAD"], &uproot.join("acme").join("widget")).await
}

fn pkt(line: &str) -> String {
    format!("{:04x}{line}", line.len() + 4)
}

/// One `git-upload-pack` request body: protocol v0, a single `want` for `sha` and no `have`, then
/// `done`. Built by hand so a test can send the exact same negotiation twice — which is what the pack
/// cache keys on, and what two clones of one head really send.
fn upload_pack_body(sha: &str) -> String {
    format!("{}0000{}", pkt(&format!("want {sha}\n")), pkt("done\n"))
}

/// One protocol-v2 `ls-refs` request body: the ref advertisement, which protocol v2 asks for over
/// `git-upload-pack` instead of over the `info/refs` GET.
fn ls_refs_body() -> String {
    format!(
        "{}{}{}0001{}{}0000",
        pkt("command=ls-refs\n"),
        pkt("agent=git/2.47.3\n"),
        pkt("object-format=sha1\n"),
        pkt("peel\n"),
        pkt("symrefs\n")
    )
}

struct PackResponse {
    status: reqwest::StatusCode,
    cache: String,
    body: bytes::Bytes,
}

/// POST one upload-pack negotiation to the daemon as `user` would, with the identity headers the
/// egress proxy stamps.
async fn post_upload_pack(daemon: &str, host: &str, user: &str, body: &str) -> PackResponse {
    post_upload_pack_with(daemon, host, user, body, None).await
}

/// The same POST carrying the `git-protocol` header a protocol-v2 client sends, which is what makes
/// `http-backend` run the v2 command the body names.
async fn post_upload_pack_with(
    daemon: &str,
    host: &str,
    user: &str,
    body: &str,
    protocol: Option<&str>,
) -> PackResponse {
    let mut request = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .post(format!(
            "http://{daemon}/git/{host}/acme/widget/git-upload-pack"
        ))
        .header("content-type", "application/x-git-upload-pack-request")
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", user)
        .body(body.to_string());
    if let Some(version) = protocol {
        request = request.header("git-protocol", version);
    }
    let resp = request.send().await.unwrap();
    let status = resp.status();
    let cache = resp
        .headers()
        .get("x-ufo-cache")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    PackResponse {
        status,
        cache,
        body: resp.bytes().await.unwrap(),
    }
}

/// GET the ref advertisement the way a clone starts, returning the body so a test can read which head
/// the daemon names.
async fn get_info_refs(daemon: &str, host: &str, user: &str) -> String {
    let resp = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get(format!(
            "http://{daemon}/git/{host}/acme/widget/info/refs?service=git-upload-pack"
        ))
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", user)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    String::from_utf8_lossy(&resp.bytes().await.unwrap()).into_owned()
}

/// Every file left in a principal's pack directory: a committed entry, or a capture in flight.
async fn pack_files(state: &Path, principal: &str) -> Vec<String> {
    let dir = state.join("pack").join(principal);
    let mut names = Vec::new();
    let Ok(mut read) = tokio::fs::read_dir(&dir).await else {
        return names;
    };
    while let Ok(Some(entry)) = read.next_entry().await {
        names.push(entry.file_name().to_string_lossy().into_owned());
    }
    names.sort();
    names
}

async fn cached_pack_keys(state: &Path, principal: &str) -> Vec<String> {
    let dir = state.join("pack").join(principal);
    let mut keys = Vec::new();
    let Ok(mut read) = tokio::fs::read_dir(&dir).await else {
        return keys;
    };
    while let Ok(Some(entry)) = read.next_entry().await {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("body") {
            keys.push(path.file_stem().unwrap().to_string_lossy().into_owned());
        }
    }
    keys.sort();
    keys
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn cold_clone_populates_the_mirror_and_a_warm_clone_reflects_a_new_upstream_commit() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;

    let (cp_router, _cp_hits) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &config(
            state.clone(),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Off,
    ))
    .await;

    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    // Cold clone: the daemon has no mirror, so it fetches upstream and populates it.
    let dest1 = tmp.path().join("dest1");
    clone_via_daemon(&repo_url, &dest1, tmp.path()).await;
    assert_eq!(
        tokio::fs::read_to_string(dest1.join("hello.txt"))
            .await
            .unwrap(),
        "world"
    );
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > 0,
        "cold clone hits upstream"
    );
    let mirror = state
        .join("git")
        .join("public")
        .join(format!("{}", up_addr).replace(':', "_"))
        .join("acme")
        .join("widget.git");
    assert!(
        mirror.join("HEAD").exists(),
        "mirror not created at {mirror:?}"
    );

    // A new commit lands on the upstream; a warm clone re-discovers refs, so it must reflect it —
    // ref discovery refreshes every time, never serving a stale mirror.
    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let dest2 = tmp.path().join("dest2");
    clone_via_daemon(&repo_url, &dest2, tmp.path()).await;
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("second.txt"))
            .await
            .unwrap(),
        "again",
        "warm clone must reflect the new upstream commit"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn mirror_is_namespaced_by_the_principal_the_control_plane_returns() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;

    let (up_router, _up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;

    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "w7-u-alice" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &config(
            state.clone(),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Off,
    ))
    .await;

    let dest = tmp.path().join("dest");
    clone_via_daemon(
        &format!("http://{daemon}/git/{up_addr}/acme/widget"),
        &dest,
        tmp.path(),
    )
    .await;

    let isolated = state
        .join("git")
        .join("w7-u-alice")
        .join(format!("{}", up_addr).replace(':', "_"))
        .join("acme")
        .join("widget.git");
    assert!(
        isolated.join("HEAD").exists(),
        "mirror not under principal dir"
    );
    assert!(
        !state.join("git").join("public").exists(),
        "nothing should land under public for a user principal"
    );
}

async fn wait_for(path: &Path) {
    for _ in 0..100 {
        if path.exists() {
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    panic!("timed out waiting for {path:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_fresh_daemon_restores_the_mirror_from_a_durable_snapshot_without_origin() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;

    let (up_router, _) = common::git_upstream(uproot);
    let (up_addr, up_handle) = common::spawn_abortable(up_router).await;

    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let durable = tmp.path().join("durable");
    let host_dir = format!("{}", up_addr).replace(':', "_");

    // First daemon: cold clone from origin, then snapshot to the durable tier.
    let daemon = common::spawn(ufo_cache::app(
        &config(
            tmp.path().join("state1"),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Fs {
            root: durable.clone(),
        },
    ))
    .await;
    clone_via_daemon(
        &format!("http://{daemon}/git/{up_addr}/acme/widget"),
        &tmp.path().join("dest1"),
        tmp.path(),
    )
    .await;

    let bundle = durable
        .join("git")
        .join("public")
        .join(&host_dir)
        .join("acme")
        .join("widget.bundle");
    wait_for(&bundle).await;

    // Pod roll: origin is gone, so only the durable snapshot can serve.
    up_handle.abort();

    // A brand-new daemon with an empty disk restores the mirror from the snapshot and serves it.
    let daemon2 = common::spawn(ufo_cache::app(
        &config(
            tmp.path().join("state2"),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Fs {
            root: durable.clone(),
        },
    ))
    .await;
    let dest2 = tmp.path().join("dest2");
    clone_via_daemon(
        &format!("http://{daemon2}/git/{up_addr}/acme/widget"),
        &dest2,
        tmp.path(),
    )
    .await;
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("hello.txt"))
            .await
            .unwrap(),
        "world"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_identical_upload_pack_request_replays_the_cached_pack_byte_for_byte() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let head = upstream_head(&uproot).await;

    let (up_router, _) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            pack_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let body = upload_pack_body(&head);
    let daemon = daemon.to_string();
    let host = up_addr.to_string();

    let first = post_upload_pack(&daemon, &host, "alice", &body).await;
    assert_eq!(first.status, 200);
    assert_eq!(first.cache, "MISS", "the first request generates the pack");
    assert!(!first.body.is_empty(), "the backend returned no pack");

    let second = post_upload_pack(&daemon, &host, "alice", &body).await;
    assert_eq!(second.cache, "HIT", "an identical request must replay");
    assert_eq!(
        second.body, first.body,
        "a replayed pack must be byte-identical"
    );

    assert_eq!(
        cached_pack_keys(&state, "public").await.len(),
        1,
        "one negotiation must leave exactly one cached entry"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_new_upstream_commit_misses_the_pack_cache_even_for_the_same_negotiation() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();
    let head = upstream_head(&uproot).await;

    let (up_router, _) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            pack_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let body = upload_pack_body(&head);
    let daemon = daemon.to_string();
    let host = up_addr.to_string();

    assert_eq!(
        post_upload_pack(&daemon, &host, "a", &body).await.cache,
        "MISS"
    );
    assert_eq!(
        post_upload_pack(&daemon, &host, "a", &body).await.cache,
        "HIT"
    );

    // The refs move upstream. The negotiation bytes are unchanged, so only the mirror's ref-state
    // fingerprint can tell the entry apart — it must, or a clone would replay a pack built against
    // history the mirror no longer matches.
    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let after_push = post_upload_pack(&daemon, &host, "a", &body).await;
    assert_eq!(
        after_push.cache, "MISS",
        "a ref that moved upstream must invalidate the cached pack"
    );
    assert_eq!(after_push.status, 200);

    // The new head is a different negotiation and must serve the commit that just landed.
    let new_head = upstream_head(&uproot_path).await;
    assert_ne!(new_head, head);
    let for_new_head = post_upload_pack(&daemon, &host, "a", &upload_pack_body(&new_head)).await;
    assert_eq!(for_new_head.cache, "MISS");
    assert_eq!(for_new_head.status, 200);
    assert_eq!(
        cached_pack_keys(&state, "public").await.len(),
        3,
        "three distinct (ref state, negotiation) pairs must be three entries"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn one_principal_is_never_served_another_principals_cached_pack() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let head = upstream_head(&uproot).await;

    let (up_router, _) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let cp_addr = common::spawn(common::control_plane_per_user()).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            pack_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let body = upload_pack_body(&head);
    let daemon = daemon.to_string();
    let host = up_addr.to_string();

    // Alice warms the cache: her second identical request replays, so the cache is live.
    assert_eq!(
        post_upload_pack(&daemon, &host, "alice", &body).await.cache,
        "MISS"
    );
    assert_eq!(
        post_upload_pack(&daemon, &host, "alice", &body).await.cache,
        "HIT"
    );

    // Bob sends the byte-identical negotiation for the same repo at the same head. He must never be
    // served from Alice's entry: his response is generated for him, and it lands in his own subtree
    // under his own key.
    let bob = post_upload_pack(&daemon, &host, "bob", &body).await;
    assert_eq!(
        bob.cache, "MISS",
        "a second principal must never hit the first principal's entry"
    );
    assert_eq!(bob.status, 200);

    let alice_keys = cached_pack_keys(&state, "w1-u-alice").await;
    let bob_keys = cached_pack_keys(&state, "w1-u-bob").await;
    assert_eq!(alice_keys.len(), 1, "alice keeps her own entry");
    assert_eq!(
        bob_keys.len(),
        1,
        "bob's entry is written under his own principal"
    );
    assert_ne!(
        alice_keys[0], bob_keys[0],
        "the principal must be part of the key, not only of the path"
    );

    // And Bob's own repeat now replays Bob's entry.
    assert_eq!(
        post_upload_pack(&daemon, &host, "bob", &body).await.cache,
        "HIT"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_upload_pack_post_inside_the_freshness_window_never_reaches_origin() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();
    let head = upstream_head(&uproot).await;

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            git_fresh_ttl_secs: 300,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    clone_via_daemon(&repo_url, &tmp.path().join("dest1"), tmp.path()).await;
    let after_cold = up_hits.load(std::sync::atomic::Ordering::SeqCst);
    assert!(after_cold > 0, "a cold clone must reach origin");

    // A commit lands upstream, then one upload-pack POST arrives inside the window. It must not touch
    // origin at all — which is the whole saving: the POSTs of a clone ride the fetch that the clone's
    // own ref discovery already did. `UFO_CACHE_GIT_FRESH_TTL_SECS=0` gives that up for a refresh per
    // request.
    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let posted = post_upload_pack(
        &daemon.to_string(),
        &up_addr.to_string(),
        "alice",
        &upload_pack_body(&head),
    )
    .await;
    assert_eq!(posted.status, 200);
    assert_eq!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst),
        after_cold,
        "an upload-pack POST inside the freshness window must not fetch upstream"
    );

    // Ref discovery is outside the window, so the very next advertisement fetches and names the commit
    // that just landed.
    let advert = get_info_refs(&daemon.to_string(), &up_addr.to_string(), "alice").await;
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > after_cold,
        "ref discovery must fetch upstream even inside the freshness window"
    );
    let new_head = upstream_head(&uproot_path).await;
    assert_ne!(new_head, head);
    assert!(
        advert.contains(&new_head),
        "ref discovery must advertise the new upstream head, got: {advert}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_protocol_v2_ls_refs_post_refreshes_the_mirror_inside_the_freshness_window() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            git_fresh_ttl_secs: 300,
            ..config(state, format!("http://{cp_addr}"), &up_addr.to_string())
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    clone_via_daemon(&repo_url, &tmp.path().join("dest1"), tmp.path()).await;
    let after_cold = up_hits.load(std::sync::atomic::Ordering::SeqCst);
    assert!(after_cold > 0, "a cold clone must reach origin");

    // Protocol v2 carries the ref advertisement on an upload-pack POST, not on the `info/refs` GET, and
    // the two are separate connections: across replicas the POST can land on a pod whose window is open
    // on a mirror that predates the push. So this POST must refresh like any other advertisement — a
    // window that covered it would name the old head and the client would check it out with no error.
    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let new_head = upstream_head(&uproot_path).await;
    let advert = post_upload_pack_with(
        &daemon.to_string(),
        &up_addr.to_string(),
        "alice",
        &ls_refs_body(),
        Some("version=2"),
    )
    .await;
    assert_eq!(advert.status, 200);
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > after_cold,
        "an `ls-refs` POST is ref discovery: it must refresh the mirror inside the window"
    );
    let body = String::from_utf8_lossy(&advert.body);
    assert!(
        body.contains(&new_head),
        "the advertisement must name the head pushed inside the window, got: {body}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_want_the_mirror_does_not_hold_fetches_inside_the_freshness_window() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            git_fresh_ttl_secs: 300,
            ..config(state, format!("http://{cp_addr}"), &up_addr.to_string())
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    clone_via_daemon(&repo_url, &tmp.path().join("dest1"), tmp.path()).await;
    let after_cold = up_hits.load(std::sync::atomic::Ordering::SeqCst);

    // Another replica's ref discovery advertised the head this push created, and the balancer lands
    // the negotiation here, where the window is open on a mirror that predates the push. The want
    // this mirror cannot back must force its fetch and be answered with the pack — never refused.
    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let new_head = upstream_head(&uproot_path).await;
    let posted = post_upload_pack(
        &daemon.to_string(),
        &up_addr.to_string(),
        "alice",
        &upload_pack_body(&new_head),
    )
    .await;
    assert_eq!(posted.status, 200);
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > after_cold,
        "a want the mirror does not hold must fetch upstream inside the window"
    );
    assert!(
        posted.body.windows(4).any(|w| w == b"PACK"),
        "the negotiation must be answered with a pack carrying the pushed head"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_response_past_the_pack_cap_is_served_whole_and_leaves_nothing_on_the_volume() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let head = upstream_head(&uproot).await;

    let (up_router, _) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    // A one-byte ceiling puts every response past the cap, which is the outsized-pack path: the capture
    // stops at the cap instead of writing a copy the tier could never hold, and the rest of the response
    // comes straight from the backend's pipe.
    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            pack_cache_bytes: 1,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let posted = post_upload_pack(
        &daemon.to_string(),
        &up_addr.to_string(),
        "alice",
        &upload_pack_body(&head),
    )
    .await;
    assert_eq!(posted.status, 200);
    assert_eq!(posted.cache, "MISS");
    assert!(
        !posted.body.is_empty(),
        "a response past the cap must still be served in full"
    );

    // The same path end to end: the clone must complete and check out the real content.
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");
    let dest = tmp.path().join("dest");
    clone_via_daemon(&repo_url, &dest, tmp.path()).await;
    assert_eq!(
        tokio::fs::read_to_string(dest.join("hello.txt"))
            .await
            .unwrap(),
        "world"
    );

    assert!(
        pack_files(&state, "public").await.is_empty(),
        "a response past the cap must leave neither an entry nor a capture behind"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_clone_begun_after_a_new_upstream_commit_sees_the_new_head_inside_the_window() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();

    let (up_router, _) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            git_fresh_ttl_secs: 300,
            pack_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    clone_via_daemon(&repo_url, &tmp.path().join("dest1"), tmp.path()).await;

    // Both levers on, a window far longer than the test: a clone that begins after the push still sees
    // the new head, because its ref discovery refreshes the mirror before it advertises anything.
    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let new_head = upstream_head(&uproot_path).await;
    let dest2 = tmp.path().join("dest2");
    clone_via_daemon(&repo_url, &dest2, tmp.path()).await;
    assert_eq!(
        git_stdout(&["rev-parse", "HEAD"], &dest2).await,
        new_head,
        "a clone begun inside the window must check out the new upstream head"
    );
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("second.txt"))
            .await
            .unwrap(),
        "again",
        "the new commit's file must be present in the clone"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_zero_freshness_window_fetches_upstream_on_every_request() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let uproot_path = uproot.clone();

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        // `config` leaves the window at 0: today's behaviour, stated here as the case under test.
        &config(
            state.clone(),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    clone_via_daemon(&repo_url, &tmp.path().join("dest1"), tmp.path()).await;
    let after_cold = up_hits.load(std::sync::atomic::Ordering::SeqCst);

    push_commit(&uproot_path, &tmp.path().join("wt"), "second.txt", "again").await;
    let dest2 = tmp.path().join("dest2");
    clone_via_daemon(&repo_url, &dest2, tmp.path()).await;
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > after_cold,
        "with the window off every request must refresh from origin"
    );
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("second.txt"))
            .await
            .unwrap(),
        "again",
        "with the window off a warm clone reflects the new upstream commit"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_evicted_mirror_is_re_cloned_even_inside_the_freshness_window() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            git_fresh_ttl_secs: 300,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    clone_via_daemon(&repo_url, &tmp.path().join("dest1"), tmp.path()).await;
    let after_cold = up_hits.load(std::sync::atomic::Ordering::SeqCst);

    // The LRU sweep takes the mirror while the daemon still counts it fresh. A missing mirror must
    // never be served from the window: the request has to clone it again before it reads an object.
    let mirror = state
        .join("git")
        .join("public")
        .join(format!("{up_addr}").replace(':', "_"))
        .join("acme")
        .join("widget.git");
    tokio::fs::remove_dir_all(&mirror).await.unwrap();

    let dest2 = tmp.path().join("dest2");
    clone_via_daemon(&repo_url, &dest2, tmp.path()).await;
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("hello.txt"))
            .await
            .unwrap(),
        "world"
    );
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > after_cold,
        "an evicted mirror must be re-cloned from origin, window or not"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_repeat_clone_of_one_head_replays_and_checks_out_identical_bytes() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;
    let head = upstream_head(&uproot).await;

    let (up_router, up_hits) = common::git_upstream(uproot);
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;

    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            git_fresh_ttl_secs: 300,
            pack_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    // Both levers on, driven by the real git client rather than a hand-built request: a second clone
    // of the same head must produce the identical checkout, which is the correctness gate any speedup
    // has to pass.
    let dest1 = tmp.path().join("dest1");
    clone_via_daemon(&repo_url, &dest1, tmp.path()).await;
    let after_cold = up_hits.load(std::sync::atomic::Ordering::SeqCst);
    let entries_after_first = cached_pack_keys(&state, "public").await;
    assert!(
        !entries_after_first.is_empty(),
        "a clone must leave its upload-pack response cached"
    );

    let dest2 = tmp.path().join("dest2");
    clone_via_daemon(&repo_url, &dest2, tmp.path()).await;
    assert_eq!(
        cached_pack_keys(&state, "public").await,
        entries_after_first,
        "the repeat clone must replay the same entries, not write new ones"
    );
    assert!(
        up_hits.load(std::sync::atomic::Ordering::SeqCst) > after_cold,
        "the repeat clone's ref discovery must still refresh the mirror from origin"
    );
    assert_eq!(git_stdout(&["rev-parse", "HEAD"], &dest1).await, head);
    assert_eq!(git_stdout(&["rev-parse", "HEAD"], &dest2).await, head);
    assert_eq!(
        git_stdout(&["rev-parse", "HEAD^{tree}"], &dest2).await,
        git_stdout(&["rev-parse", "HEAD^{tree}"], &dest1).await,
        "a replayed clone must check out the identical tree"
    );
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("hello.txt"))
            .await
            .unwrap(),
        "world"
    );
}

struct RecordedLfs {
    authorization: String,
    content_type: String,
    accept: String,
    body: bytes::Bytes,
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_lfs_api_call_is_relayed_with_the_principals_credential() {
    let recorded: Arc<Mutex<Option<RecordedLfs>>> = Arc::new(Mutex::new(None));
    let rec = recorded.clone();
    let up_router = axum::Router::new()
        .route(
            "/acme/widget.git/info/lfs/objects/batch",
            axum::routing::post(move |req: axum::extract::Request| {
                let rec = rec.clone();
                async move {
                    let (parts, body) = req.into_parts();
                    let bytes = axum::body::to_bytes(body, 1 << 20).await.unwrap();
                    let h = |name: &str| {
                        parts
                            .headers
                            .get(name)
                            .and_then(|v| v.to_str().ok())
                            .unwrap_or("")
                            .to_string()
                    };
                    *rec.lock().unwrap() = Some(RecordedLfs {
                        authorization: h("authorization"),
                        content_type: h("content-type"),
                        accept: h("accept"),
                        body: bytes,
                    });
                    (
                        [("content-type", "application/vnd.git-lfs+json")],
                        r#"{"transfer":"basic","objects":[]}"#,
                    )
                }
            }),
        )
        .route(
            "/acme/widget.git/info/lfs/locks/verify",
            axum::routing::post(|| async { (reqwest::StatusCode::FORBIDDEN, "lfs forbidden") }),
        );
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({
        "principal": "w1-u-alice",
        "username": "x-access-token",
        "token": "tok123"
    }));
    let cp_addr = common::spawn(cp_router).await;

    let tmp = tempfile::tempdir().unwrap();
    let daemon = common::spawn(ufo_cache::app(
        &config(
            tmp.path().join("state"),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Off,
    ))
    .await;

    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let batch_body = r#"{"operation":"download","objects":[{"oid":"abc","size":3}]}"#;
    let resp = client
        .post(format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/objects/batch"
        ))
        .header("content-type", "application/vnd.git-lfs+json")
        .header("accept", "application/vnd.git-lfs+json")
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", "alice")
        .body(batch_body)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(
        resp.headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok()),
        Some("application/vnd.git-lfs+json"),
        "the origin's response headers must come back with its body"
    );
    assert_eq!(
        resp.bytes().await.unwrap(),
        r#"{"transfer":"basic","objects":[]}"#,
        "the origin's batch response must be relayed byte for byte"
    );

    let seen = recorded.lock().unwrap().take().unwrap();
    let basic = base64::engine::general_purpose::STANDARD.encode("x-access-token:tok123");
    assert_eq!(
        seen.authorization,
        format!("Basic {basic}"),
        "the relay must authenticate upstream with the principal's resolved credential"
    );
    assert_eq!(seen.content_type, "application/vnd.git-lfs+json");
    assert_eq!(seen.accept, "application/vnd.git-lfs+json");
    assert_eq!(
        seen.body, batch_body,
        "the negotiation body must reach the origin unchanged"
    );

    // A non-200 relays as itself: git-lfs reads the status to decide its next step, so a refusal
    // must never be flattened into a daemon error.
    let resp = client
        .post(format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/locks/verify"
        ))
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", "alice")
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 403);
    assert_eq!(resp.bytes().await.unwrap(), "lfs forbidden");
}

/// Push a commit that tracks `*.bin` with the lfs filter and adds `data.bin` as an LFS pointer to
/// `oid`. The filter is neutralized on these commands so the pointer bytes land in git verbatim,
/// whatever git-lfs config the running machine carries.
async fn push_lfs_pointer(uproot: &Path, workdir: &Path, oid: &str, size: usize) {
    let bare = uproot.join("acme").join("widget");
    git(
        &[
            "clone",
            "-q",
            bare.to_str().unwrap(),
            workdir.to_str().unwrap(),
        ],
        uproot,
    )
    .await;
    tokio::fs::write(
        workdir.join(".gitattributes"),
        "*.bin filter=lfs diff=lfs merge=lfs -text\n",
    )
    .await
    .unwrap();
    tokio::fs::write(
        workdir.join("data.bin"),
        format!("version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {size}\n"),
    )
    .await
    .unwrap();
    let neutral = [
        "-c",
        "filter.lfs.clean=cat",
        "-c",
        "filter.lfs.smudge=cat",
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.required=false",
    ];
    git(&[&neutral[..], &["add", "."][..]].concat(), workdir).await;
    git(
        &[&neutral[..], &["commit", "-q", "-m", "lfs"][..]].concat(),
        workdir,
    )
    .await;
    git(
        &[&neutral[..], &["push", "-q", "origin", "main"][..]].concat(),
        workdir,
    )
    .await;
}

/// A git upstream that also answers the git-lfs batch API: real `git http-backend` for the wire
/// protocol, a canned batch answer whose href names the server's own content route, and a counter
/// of content downloads so a test can prove the cache fetched only once. Binds before building the
/// router so the handler can spell its own address in that href.
async fn spawn_lfs_upstream(
    project_root: PathBuf,
    oid: String,
    content: &'static str,
) -> (std::net::SocketAddr, Arc<std::sync::atomic::AtomicUsize>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let content_hits = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let counted = content_hits.clone();
    let (git_router, _) = common::git_upstream(project_root);
    let router = axum::Router::new()
        .route(
            "/acme/widget.git/info/lfs/objects/batch",
            axum::routing::post(move || {
                let oid = oid.clone();
                async move {
                    (
                        [("content-type", "application/vnd.git-lfs+json")],
                        serde_json::json!({
                            "transfer": "basic",
                            "objects": [{
                                "oid": oid,
                                "size": content.len(),
                                "actions": {
                                    "download": {"href": format!("http://{addr}/lfs-content/{oid}")}
                                }
                            }]
                        })
                        .to_string(),
                    )
                }
            }),
        )
        .route(
            "/lfs-content/{oid}",
            axum::routing::get(move || {
                let counted = counted.clone();
                async move {
                    counted.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    content
                }
            }),
        )
        .merge(git_router);
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (addr, content_hits)
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn git_lfs_pull_through_the_daemon_materializes_the_object() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;

    const LFS_CONTENT: &str = "LFS CONTENT";
    let oid = hex::encode(sha2::Sha256::digest(LFS_CONTENT.as_bytes()));
    push_lfs_pointer(&uproot, &tmp.path().join("wt"), &oid, LFS_CONTENT.len()).await;

    let (up_addr, _) = spawn_lfs_upstream(uproot, oid, LFS_CONTENT).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;
    let daemon = common::spawn(ufo_cache::app(
        &config(
            tmp.path().join("state"),
            format!("http://{cp_addr}"),
            &up_addr.to_string(),
        ),
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    // The clone skips the smudge so it needs no LFS round trip; the pull that follows is the LFS
    // client's own flow: batch through the daemon, then the href straight to the origin's storage.
    let dest = tmp.path().join("dest");
    clone_for_lfs(&repo_url, tmp.path(), &dest).await;
    assert!(
        tokio::fs::read_to_string(dest.join("data.bin"))
            .await
            .unwrap()
            .starts_with("version https://git-lfs.github.com/spec/v1"),
        "before the pull the worktree holds the pointer, not the content"
    );

    lfs_pull(&dest).await;

    assert_eq!(
        tokio::fs::read_to_string(dest.join("data.bin"))
            .await
            .unwrap(),
        LFS_CONTENT,
        "git lfs pull through the daemon must materialize the object's content"
    );
}

/// Clone through the daemon with the smudge skipped, and write the identity headers into the
/// clone's config so its own LFS calls carry them.
async fn clone_for_lfs(repo_url: &str, base: &Path, dest: &Path) {
    git_with_env(
        &[
            "-c",
            "http.extraHeader=x-ufo-workspace: w1",
            "-c",
            "http.extraHeader=x-ufo-user: alice",
            "clone",
            "-q",
            repo_url,
            dest.to_str().unwrap(),
        ],
        base,
        &[("GIT_LFS_SKIP_SMUDGE", "1")],
    )
    .await;
    git(
        &["config", "--add", "http.extraHeader", "x-ufo-workspace: w1"],
        dest,
    )
    .await;
    git(
        &["config", "--add", "http.extraHeader", "x-ufo-user: alice"],
        dest,
    )
    .await;
}

async fn lfs_pull(dest: &Path) {
    git(&["lfs", "install", "--local"], dest).await;
    git(&["lfs", "pull"], dest).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_second_lfs_pull_is_served_from_the_content_cache() {
    let tmp = tempfile::tempdir().unwrap();
    let uproot = seed_upstream(tmp.path()).await;

    const LFS_CONTENT: &str = "LFS CONTENT";
    let oid = hex::encode(sha2::Sha256::digest(LFS_CONTENT.as_bytes()));
    push_lfs_pointer(&uproot, &tmp.path().join("wt"), &oid, LFS_CONTENT.len()).await;

    let (up_addr, content_hits) = spawn_lfs_upstream(uproot, oid.clone(), LFS_CONTENT).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;
    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            lfs_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;
    let repo_url = format!("http://{daemon}/git/{up_addr}/acme/widget");

    let dest1 = tmp.path().join("dest1");
    clone_for_lfs(&repo_url, tmp.path(), &dest1).await;
    lfs_pull(&dest1).await;
    assert_eq!(
        tokio::fs::read_to_string(dest1.join("data.bin"))
            .await
            .unwrap(),
        LFS_CONTENT
    );
    assert_eq!(
        content_hits.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "the first pull fetches the object from the origin once"
    );
    assert!(
        state
            .join("lfs")
            .join("public")
            .join(format!("{oid}.body"))
            .exists(),
        "the fetched object must land in the principal's content tier"
    );

    // A rebuilt sandbox pulling the same repo is a fresh clone and the same objects: the content
    // must come from the tier, not another trip to the origin's storage.
    let dest2 = tmp.path().join("dest2");
    clone_for_lfs(&repo_url, tmp.path(), &dest2).await;
    lfs_pull(&dest2).await;
    assert_eq!(
        tokio::fs::read_to_string(dest2.join("data.bin"))
            .await
            .unwrap(),
        LFS_CONTENT
    );
    assert_eq!(
        content_hits.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "the second pull must be served from the content tier"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn the_batch_answer_names_the_daemon_for_downloads() {
    let tmp = tempfile::tempdir().unwrap();
    const LFS_CONTENT: &str = "LFS CONTENT";
    let oid = hex::encode(sha2::Sha256::digest(LFS_CONTENT.as_bytes()));

    let (up_addr, _) = spawn_lfs_upstream(tmp.path().join("empty"), oid.clone(), LFS_CONTENT).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            lfs_cache_bytes: 1 << 30,
            ..config(
                tmp.path().join("state"),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let resp = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .post(format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/objects/batch"
        ))
        .header("content-type", "application/vnd.git-lfs+json")
        .header("accept", "application/vnd.git-lfs+json")
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", "alice")
        .body(format!(
            r#"{{"operation":"download","objects":[{{"oid":"{oid}","size":11}}]}}"#
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let answer: serde_json::Value = resp.json().await.unwrap();
    let download = &answer["objects"][0]["actions"]["download"];
    assert_eq!(
        download["href"],
        format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/objects/content/{oid}?size=11"
        ),
        "the batch answer must point the download at the daemon's content route"
    );
    assert!(
        download.get("header").is_none(),
        "the origin's pre-signed headers stay with the daemon, never the client"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn lfs_content_past_the_ceiling_streams_and_leaves_nothing() {
    let tmp = tempfile::tempdir().unwrap();
    const LFS_CONTENT: &str = "LFS CONTENT";
    let oid = hex::encode(sha2::Sha256::digest(LFS_CONTENT.as_bytes()));

    let (up_addr, _) = spawn_lfs_upstream(tmp.path().join("empty"), oid.clone(), LFS_CONTENT).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;
    let state = tmp.path().join("state");
    // A one-byte ceiling puts every object past the cap: the fetch must stream through whole and
    // write nothing.
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            lfs_cache_bytes: 1,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let resp = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get(format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/objects/content/{oid}?size=11"
        ))
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", "alice")
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(resp.bytes().await.unwrap(), LFS_CONTENT);
    let leftovers = match tokio::fs::read_dir(state.join("lfs").join("public")).await {
        Ok(mut dir) => dir.next_entry().await.unwrap().is_some(),
        Err(_) => false,
    };
    assert!(
        !leftovers,
        "content past the ceiling must leave nothing on the volume"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn lfs_content_that_fails_its_digest_is_refused() {
    let tmp = tempfile::tempdir().unwrap();
    const LFS_CONTENT: &str = "LFS CONTENT";
    let oid = hex::encode(sha2::Sha256::digest(LFS_CONTENT.as_bytes()));

    // The origin serves different bytes than the oid names — a truncated or substituted object
    // must never be cached or handed to the client as the real one.
    let (up_addr, _) =
        spawn_lfs_upstream(tmp.path().join("empty"), oid.clone(), "WRONG BYTES").await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;
    let state = tmp.path().join("state");
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            lfs_cache_bytes: 1 << 30,
            ..config(
                state.clone(),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let resp = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get(format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/objects/content/{oid}?size=11"
        ))
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", "alice")
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 502);
    let lfs_dir = state.join("lfs").join("public");
    let empty = match tokio::fs::read_dir(&lfs_dir).await {
        Ok(mut dir) => dir.next_entry().await.unwrap().is_none(),
        Err(_) => true,
    };
    assert!(empty, "a failed digest must leave nothing in the tier");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_lfs_href_at_a_private_address_is_refused() {
    let tmp = tempfile::tempdir().unwrap();
    const LFS_CONTENT: &str = "LFS CONTENT";
    let oid = hex::encode(sha2::Sha256::digest(LFS_CONTENT.as_bytes()));

    // The origin's batch answer points inside a private range. The daemon fetches hrefs with its
    // own network position, so it must refuse what the sandbox's egress would have refused.
    let batch_oid = oid.clone();
    let up_router = axum::Router::new().route(
        "/acme/widget.git/info/lfs/objects/batch",
        axum::routing::post(move || {
            let oid = batch_oid.clone();
            async move {
                (
                    [("content-type", "application/vnd.git-lfs+json")],
                    serde_json::json!({
                        "transfer": "basic",
                        "objects": [{
                            "oid": oid,
                            "size": 11,
                            "actions": {
                                "download": {"href": format!("http://10.255.255.1:9/lfs-content/{oid}")}
                            }
                        }]
                    })
                    .to_string(),
                )
            }
        }),
    );
    let up_addr = common::spawn(up_router).await;
    let (cp_router, _) = common::control_plane(serde_json::json!({ "principal": "public" }));
    let cp_addr = common::spawn(cp_router).await;
    let daemon = common::spawn(ufo_cache::app(
        &Config {
            lfs_cache_bytes: 1 << 30,
            ..config(
                tmp.path().join("state"),
                format!("http://{cp_addr}"),
                &up_addr.to_string(),
            )
        },
        Durable::Off,
    ))
    .await;

    let resp = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get(format!(
            "http://{daemon}/git/{up_addr}/acme/widget.git/info/lfs/objects/content/{oid}?size=11"
        ))
        .header("x-ufo-workspace", "w1")
        .header("x-ufo-user", "alice")
        .send()
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        502,
        "an href inside a private range must be refused, never fetched"
    );
}
