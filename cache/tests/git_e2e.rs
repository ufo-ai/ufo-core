mod common;

use std::path::{Path, PathBuf};

use ufo_cache::durable::Durable;
use ufo_cache::Config;

async fn git(args: &[&str], cwd: &Path) {
    let out = tokio::process::Command::new("git")
        .args(args)
        .current_dir(cwd)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@t")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@t")
        .output()
        .await
        .unwrap();
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
