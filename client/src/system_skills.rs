use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read};
use std::path::{Component, Path, PathBuf};

use base64::engine::general_purpose::URL_SAFE;
use base64::Engine as _;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::config::Home;
use crate::wire::{Session, SystemSkillsFetch};

const DIGEST_PREFIX: &str = "sha256:";
const SKILLS_DIR: &str = "skills";
const ARCHIVE_MANIFEST: &str = "manifest.json";
const INSTALLED_MANIFEST: &str = ".system-manifest.json";
const OLD_BUNDLES_DIR: &str = "bundles";
const OLD_CURRENT_FILE: &str = "current";
const SYSTEM_SKILLS_BAKED_ENV: &str = "UFO_SYSTEM_SKILLS_BAKED";

#[derive(Clone, Deserialize, Serialize)]
struct BundleManifest {
    digest: String,
    skills: BTreeMap<String, BundleSkill>,
}

#[derive(Clone, Deserialize, Serialize)]
struct BundleSkill {
    digest: String,
    files: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoadParams {
    system: BTreeMap<String, String>,
    user: BTreeMap<String, WireSkill>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireSkill {
    digest: String,
    files: BTreeMap<String, String>,
}

pub fn sync(home: &Home, session: &Session) -> Result<(), String> {
    if std::env::var_os(SYSTEM_SKILLS_BAKED_ENV).is_some() {
        return Ok(());
    }
    if session.workspace_url.is_none() {
        return Ok(());
    }
    let root = home.root.join(SKILLS_DIR);
    fs::create_dir_all(&root).map_err(|error| error.to_string())?;
    let current = current_etag(&root);
    let archive = root.join(format!(".download-{}.zip", std::process::id()));
    let _ = fs::remove_file(&archive);
    let fetched = match session.fetch_system_skills(current.as_deref(), &archive) {
        Ok(fetched) => fetched,
        Err(error) => {
            let _ = fs::remove_file(archive);
            return Err(error);
        }
    };
    let outcome = match fetched {
        SystemSkillsFetch::Current => prune(&root),
        SystemSkillsFetch::Downloaded(etag) => install_bundle(&root, &archive, &etag),
    };
    let _ = fs::remove_file(archive);
    outcome
}

fn current_etag(root: &Path) -> Option<String> {
    let manifest = installed_manifest(root).ok()?;
    verify_manifest(root, &manifest).ok()?;
    Some(format!("\"{}\"", manifest.digest))
}

fn installed_manifest(root: &Path) -> Result<BundleManifest, String> {
    let bytes = fs::read(root.join(INSTALLED_MANIFEST)).map_err(|error| error.to_string())?;
    serde_json::from_slice(&bytes).map_err(|error| error.to_string())
}

fn install_bundle(root: &Path, archive_path: &Path, etag: &str) -> Result<(), String> {
    let expected = etag.trim().trim_matches('"');
    digest_id(expected).ok_or("system skills response has an invalid etag")?;
    let staging = root.join(format!(".install-{}", std::process::id()));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir(&staging).map_err(|error| error.to_string())?;
    let outcome = extract_bundle(archive_path, &staging, expected).and_then(|manifest| {
        let previous = installed_manifest(root).ok();
        let top_levels = previous
            .iter()
            .flat_map(|old| old.skills.keys())
            .chain(manifest.skills.keys())
            .filter_map(|name| Path::new(name).components().next())
            .map(|part| part.as_os_str().to_owned())
            .collect::<BTreeSet<_>>();
        for name in &top_levels {
            let target = root.join(name);
            remove_path(&target)?;
            let source = staging.join(name);
            if source.exists() {
                fs::rename(source, target).map_err(|error| error.to_string())?;
            }
        }
        fs::rename(
            staging.join(ARCHIVE_MANIFEST),
            root.join(INSTALLED_MANIFEST),
        )
        .map_err(|error| error.to_string())?;
        verify_manifest(root, &manifest)?;
        fs::remove_dir_all(&staging).map_err(|error| error.to_string())?;
        prune(root)
    });
    if outcome.is_err() {
        let _ = fs::remove_dir_all(staging);
    }
    outcome
}

fn extract_bundle(
    archive_path: &Path,
    staging: &Path,
    expected_digest: &str,
) -> Result<BundleManifest, String> {
    let file = File::open(archive_path).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|error| error.to_string())?;
    let mut archived = BTreeSet::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).map_err(|error| error.to_string())?;
        if entry.is_dir() {
            continue;
        }
        let relative = entry
            .enclosed_name()
            .ok_or("system skills archive contains an unsafe path")?
            .to_path_buf();
        safe_archive_path(&relative)?;
        let target = staging.join(&relative);
        fs::create_dir_all(target.parent().ok_or("system skills path has no parent")?)
            .map_err(|error| error.to_string())?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
            .map_err(|error| error.to_string())?;
        std::io::copy(&mut entry, &mut output).map_err(|error| error.to_string())?;
        archived.insert(posix_path(&relative));
    }
    let manifest = installed_manifest_at(&staging.join(ARCHIVE_MANIFEST))?;
    if manifest.digest != expected_digest {
        return Err("system skills manifest does not match its etag".to_string());
    }
    verify_manifest(staging, &manifest)?;
    let declared =
        std::iter::once(ARCHIVE_MANIFEST.to_string())
            .chain(manifest.skills.iter().flat_map(|(name, skill)| {
                skill.files.iter().map(move |path| format!("{name}/{path}"))
            }))
            .collect::<BTreeSet<_>>();
    if archived != declared {
        return Err("system skills archive contains undeclared files".to_string());
    }
    Ok(manifest)
}

fn installed_manifest_at(path: &Path) -> Result<BundleManifest, String> {
    let mut contents = String::new();
    File::open(path)
        .map_err(|error| error.to_string())?
        .read_to_string(&mut contents)
        .map_err(|error| error.to_string())?;
    serde_json::from_str(&contents).map_err(|error| error.to_string())
}

fn verify_manifest(root: &Path, manifest: &BundleManifest) -> Result<(), String> {
    let payload = serde_json::to_vec(&serde_json::json!({"skills": &manifest.skills}))
        .map_err(|error| error.to_string())?;
    let digest = format!("{DIGEST_PREFIX}{:x}", Sha256::digest(payload));
    if digest != manifest.digest {
        return Err("system skills manifest digest is invalid".to_string());
    }
    for (name, skill) in &manifest.skills {
        safe_relative(name)?;
        let files = read_declared_files(root.join(name), &skill.files)?;
        if content_digest(&files) != skill.digest {
            return Err(format!("system skill does not match its digest: {name}"));
        }
    }
    Ok(())
}

fn prune(root: &Path) -> Result<(), String> {
    let top_levels = installed_manifest(root)
        .ok()
        .into_iter()
        .flat_map(|manifest| manifest.skills.into_keys())
        .filter_map(|name| {
            PathBuf::from(name)
                .components()
                .next()
                .map(|part| part.as_os_str().to_owned())
        })
        .collect::<BTreeSet<_>>();
    let bundles = root.join(OLD_BUNDLES_DIR);
    if !top_levels.contains(Path::new(OLD_BUNDLES_DIR).as_os_str()) {
        remove_path(&bundles)?;
    }
    let current = root.join(OLD_CURRENT_FILE);
    if !top_levels.contains(Path::new(OLD_CURRENT_FILE).as_os_str()) {
        remove_path(&current)?;
    }
    for entry in fs::read_dir(root).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with(".download-") {
            fs::remove_file(entry.path()).map_err(|error| error.to_string())?;
        } else if name.starts_with(".bundle-") || name.starts_with(".install-") {
            fs::remove_dir_all(entry.path()).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn safe_archive_path(path: &Path) -> Result<(), String> {
    if path
        .components()
        .all(|part| matches!(part, Component::Normal(_)))
    {
        Ok(())
    } else {
        Err(format!(
            "invalid system skills archive path {}",
            path.display()
        ))
    }
}

pub fn main(args: &[String]) -> i32 {
    if args.len() != 1 {
        eprintln!("usage: ufo fs skills <payload-path>");
        return 2;
    }
    match load_staged(&Home::resolve(), Path::new(&args[0])) {
        Ok(reply) => {
            println!("{}", String::from_utf8_lossy(&reply));
            0
        }
        Err(error) => {
            eprintln!("ufo fs skills: {error}");
            1
        }
    }
}

fn load_staged(home: &Home, path: &Path) -> Result<Vec<u8>, String> {
    let params = fs::read_to_string(path).map_err(|error| error.to_string())?;
    fs::remove_file(path).map_err(|error| error.to_string())?;
    load(home, &params)
}

pub fn load(home: &Home, params: &str) -> Result<Vec<u8>, String> {
    let requested =
        serde_json::from_str::<LoadParams>(params).map_err(|error| error.to_string())?;
    let system_names = requested.system.keys().cloned().collect::<BTreeSet<_>>();
    let root = home.root.join(SKILLS_DIR);
    fs::create_dir_all(&root).map_err(|error| error.to_string())?;
    let manifest = installed_manifest(&root).ok();
    let mut roots = BTreeMap::new();
    for (name, digest) in requested.system {
        safe_relative(&name)?;
        let Some(skill) = manifest.as_ref().and_then(|value| value.skills.get(&name)) else {
            continue;
        };
        if skill.digest != digest {
            continue;
        }
        let files = read_declared_files(root.join(&name), &skill.files)?;
        if content_digest(&files) == digest {
            roots.insert(name.clone(), posix_path(&root.join(name)));
        }
    }
    for (name, skill) in requested.user {
        safe_user_skill_name(&name)?;
        if system_names.iter().any(|system| {
            system == &name
                || system.starts_with(&format!("{name}/"))
                || name.starts_with(&format!("{system}/"))
        }) {
            return Err(format!("user skill conflicts with system skill: {name}"));
        }
        let files = decode_files(&skill.files)?;
        if content_digest(&files) != skill.digest {
            return Err(format!("user skill does not match its digest: {name}"));
        }
        install_user(&root, &name, &files)?;
        roots.insert(name.clone(), posix_path(&root.join(name)));
    }
    serde_json::to_vec(&serde_json::json!({"roots": roots})).map_err(|error| error.to_string())
}

pub fn load_synced(home: &Home, session: &Session, params: &str) -> Result<Vec<u8>, String> {
    let reply = load(home, params)?;
    let requested =
        serde_json::from_str::<LoadParams>(params).map_err(|error| error.to_string())?;
    let loaded =
        serde_json::from_slice::<serde_json::Value>(&reply).map_err(|error| error.to_string())?;
    let roots = loaded
        .get("roots")
        .and_then(serde_json::Value::as_object)
        .ok_or("skill load returned invalid roots")?;
    if !requested
        .system
        .keys()
        .any(|name| !roots.contains_key(name))
    {
        return Ok(reply);
    }
    sync(home, session)?;
    load(home, params)
}

fn install_user(root: &Path, name: &str, files: &[(String, Vec<u8>)]) -> Result<(), String> {
    let staging = root.join(format!(".user-{}", std::process::id()));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir(&staging).map_err(|error| error.to_string())?;
    for (relative, content) in files {
        safe_relative(relative)?;
        let target = staging.join(relative);
        fs::create_dir_all(target.parent().ok_or("user skill path has no parent")?)
            .map_err(|error| error.to_string())?;
        fs::write(target, content).map_err(|error| error.to_string())?;
    }
    let target = root.join(name);
    remove_path(&target)?;
    let outcome = fs::rename(&staging, target).map_err(|error| error.to_string());
    if outcome.is_err() {
        let _ = fs::remove_dir_all(staging);
    }
    outcome
}

fn decode_files(files: &BTreeMap<String, String>) -> Result<Vec<(String, Vec<u8>)>, String> {
    files
        .iter()
        .map(|(path, content)| {
            safe_relative(path)?;
            URL_SAFE
                .decode(content)
                .map(|decoded| (path.clone(), decoded))
                .map_err(|error| error.to_string())
        })
        .collect()
}

fn read_declared_files(root: PathBuf, paths: &[String]) -> Result<Vec<(String, Vec<u8>)>, String> {
    paths
        .iter()
        .map(|relative| {
            safe_relative(relative)?;
            let path = root.join(relative);
            if !fs::symlink_metadata(&path)
                .map_err(|error| error.to_string())?
                .file_type()
                .is_file()
            {
                return Err(format!("{} is not a regular file", path.display()));
            }
            fs::read(path)
                .map(|content| (relative.clone(), content))
                .map_err(|error| error.to_string())
        })
        .collect()
}

fn safe_relative(path: &str) -> Result<(), String> {
    if path.is_empty()
        || Path::new(path)
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(format!("invalid skill path {path:?}"));
    }
    Ok(())
}

fn remove_path(path: &Path) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {
            fs::remove_dir_all(path).map_err(|error| error.to_string())
        }
        Ok(_) => fs::remove_file(path).map_err(|error| error.to_string()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

fn safe_user_skill_name(name: &str) -> Result<(), String> {
    safe_relative(name)?;
    let parts = Path::new(name).components().collect::<Vec<_>>();
    if parts.len() != 1 || parts[0].as_os_str().to_string_lossy().starts_with('.') {
        return Err(format!("invalid skill name {name:?}"));
    }
    Ok(())
}

fn digest_id(digest: &str) -> Option<&str> {
    let value = digest.strip_prefix(DIGEST_PREFIX)?;
    (value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())).then_some(value)
}

fn content_digest(files: &[(String, Vec<u8>)]) -> String {
    let mut digest = Sha256::new();
    for (path, content) in files {
        digest.update(Sha256::digest(path.as_bytes()));
        digest.update(Sha256::digest(content));
    }
    format!("{DIGEST_PREFIX}{:x}", digest.finalize())
}

fn posix_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use zip::write::SimpleFileOptions;

    const LINUX_MAX_ARG_STRLEN: u64 = 131_072;

    fn write_bundle(path: &Path, declared: &[u8], archived: &[u8]) -> String {
        write_named_bundle(path, "probe", declared, archived)
    }

    fn write_named_bundle(path: &Path, name: &str, declared: &[u8], archived: &[u8]) -> String {
        let files = vec![("SKILL.md".to_string(), declared.to_vec())];
        let skills = BTreeMap::from([(
            name.to_string(),
            BundleSkill {
                digest: content_digest(&files),
                files: vec!["SKILL.md".to_string()],
            },
        )]);
        let payload = serde_json::to_vec(&serde_json::json!({"skills": &skills})).unwrap();
        let digest = format!("{DIGEST_PREFIX}{:x}", Sha256::digest(payload));
        let etag = format!("\"{digest}\"");
        let mut archive = zip::ZipWriter::new(File::create(path).unwrap());
        archive
            .start_file(ARCHIVE_MANIFEST, SimpleFileOptions::default())
            .unwrap();
        archive
            .write_all(&serde_json::to_vec(&BundleManifest { digest, skills }).unwrap())
            .unwrap();
        archive
            .start_file(format!("{name}/SKILL.md"), SimpleFileOptions::default())
            .unwrap();
        archive.write_all(archived).unwrap();
        archive.finish().unwrap();
        etag
    }

    fn scratch(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("ufo-skills-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn a_baked_bundle_skips_network_sync() {
        let root = scratch("baked");
        let home = Home {
            root: root.join("home"),
        };
        let session = Session::new(
            "invalid".to_string(),
            Some("invalid".to_string()),
            "channel".to_string(),
            None,
            "session".to_string(),
            None,
            false,
            false,
        );
        let original = std::env::var_os(SYSTEM_SKILLS_BAKED_ENV);
        unsafe { std::env::set_var(SYSTEM_SKILLS_BAKED_ENV, "1") };

        let result = sync(&home, &session);

        match original {
            Some(value) => unsafe { std::env::set_var(SYSTEM_SKILLS_BAKED_ENV, value) },
            None => unsafe { std::env::remove_var(SYSTEM_SKILLS_BAKED_ENV) },
        }
        assert_eq!(result, Ok(()));
        assert!(!home.root.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn installs_directly_and_prunes_old_bundles() {
        let root = scratch("install");
        let skills = root.join(SKILLS_DIR);
        fs::create_dir_all(skills.join("bundles/old")).unwrap();
        fs::write(skills.join("current"), "old").unwrap();
        let archive = root.join("bundle.zip");
        let etag = write_bundle(&archive, b"workflow", b"workflow");

        install_bundle(&skills, &archive, &etag).unwrap();

        assert_eq!(
            fs::read(skills.join("probe/SKILL.md")).unwrap(),
            b"workflow"
        );
        assert!(skills.join(INSTALLED_MANIFEST).is_file());
        assert!(!skills.join("bundles").exists());
        assert!(!skills.join("current").exists());
        assert_eq!(current_etag(&skills), Some(etag));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn refuses_changed_bundle_bytes_before_pruning() {
        let root = scratch("changed");
        let skills = root.join(SKILLS_DIR);
        fs::create_dir_all(skills.join("bundles/keep")).unwrap();
        let archive = root.join("bundle.zip");
        let etag = write_bundle(&archive, b"expected", b"changed");

        assert!(install_bundle(&skills, &archive, &etag).is_err());

        assert!(skills.join("bundles/keep").is_dir());
        assert!(!skills.join(INSTALLED_MANIFEST).exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn loads_system_and_user_skills_from_one_root() {
        let root = scratch("load");
        let home = Home {
            root: root.join("home"),
        };
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive = root.join("bundle.zip");
        let etag = write_bundle(&archive, b"system", b"system");
        install_bundle(&skills, &archive, &etag).unwrap();
        let user_files = vec![("SKILL.md".to_string(), b"user".to_vec())];
        let params = serde_json::json!({
            "system": {"probe": content_digest(&[("SKILL.md".to_string(), b"system".to_vec())])},
            "user": {"greet": {
                "digest": content_digest(&user_files),
                "files": {"SKILL.md": URL_SAFE.encode(b"user")},
            }},
        });

        let reply = load(&home, &params.to_string()).unwrap();
        let roots: serde_json::Value = serde_json::from_slice(&reply).unwrap();

        assert_eq!(fs::read(skills.join("probe/SKILL.md")).unwrap(), b"system");
        assert_eq!(fs::read(skills.join("greet/SKILL.md")).unwrap(), b"user");
        assert_eq!(roots["roots"]["probe"], posix_path(&skills.join("probe")));
        assert_eq!(roots["roots"]["greet"], posix_path(&skills.join("greet")));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn loads_a_nested_system_skill() {
        let root = scratch("nested-system");
        let home = Home {
            root: root.join("home"),
        };
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive = root.join("bundle.zip");
        let name = "website-building/webapp";
        let etag = write_named_bundle(&archive, name, b"system", b"system");
        install_bundle(&skills, &archive, &etag).unwrap();
        let digest = content_digest(&[("SKILL.md".to_string(), b"system".to_vec())]);
        let params = serde_json::json!({"system": {name: digest}, "user": {}});

        let reply = load(&home, &params.to_string()).unwrap();
        let roots: serde_json::Value = serde_json::from_slice(&reply).unwrap();

        assert_eq!(
            fs::read(skills.join("website-building/webapp/SKILL.md")).unwrap(),
            b"system"
        );
        assert_eq!(roots["roots"][name], posix_path(&skills.join(name)));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn loads_a_large_staged_payload_and_removes_it() {
        let root = scratch("staged-load");
        let home = Home {
            root: root.join("home"),
        };
        let content = vec![b'x'; 200_000];
        let files = vec![("reference.bin".to_string(), content.clone())];
        let payload = root.join("payload.json");
        fs::write(
            &payload,
            serde_json::json!({
                "system": {},
                "user": {"large": {
                    "digest": content_digest(&files),
                    "files": {"reference.bin": URL_SAFE.encode(&content)},
                }},
            })
            .to_string(),
        )
        .unwrap();
        assert!(fs::metadata(&payload).unwrap().len() > LINUX_MAX_ARG_STRLEN);

        let reply = load_staged(&home, &payload).unwrap();
        let roots: serde_json::Value = serde_json::from_slice(&reply).unwrap();

        assert!(!payload.exists());
        assert_eq!(
            fs::read(home.root.join("skills/large/reference.bin")).unwrap(),
            content
        );
        assert_eq!(
            roots["roots"]["large"],
            posix_path(&home.root.join("skills/large"))
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn refuses_a_user_skill_that_matches_a_system_skill() {
        let root = scratch("conflict");
        let home = Home {
            root: root.join("home"),
        };
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive = root.join("bundle.zip");
        let etag = write_bundle(&archive, b"system", b"system");
        install_bundle(&skills, &archive, &etag).unwrap();
        let user_files = vec![("SKILL.md".to_string(), b"user".to_vec())];
        let params = serde_json::json!({
            "system": {"probe": content_digest(&[("SKILL.md".to_string(), b"system".to_vec())])},
            "user": {"probe": {
                "digest": content_digest(&user_files),
                "files": {"SKILL.md": URL_SAFE.encode(b"user")},
            }},
        });

        assert_eq!(
            load(&home, &params.to_string()),
            Err("user skill conflicts with system skill: probe".to_string())
        );
        assert_eq!(fs::read(skills.join("probe/SKILL.md")).unwrap(), b"system");
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn a_user_skill_cannot_replace_the_system_manifest() {
        let root = scratch("manifest-conflict");
        let home = Home {
            root: root.join("home"),
        };
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive = root.join("bundle.zip");
        let etag = write_bundle(&archive, b"system", b"system");
        install_bundle(&skills, &archive, &etag).unwrap();
        let before = fs::read(skills.join(INSTALLED_MANIFEST)).unwrap();
        let user_files = vec![("SKILL.md".to_string(), b"user".to_vec())];
        let params = serde_json::json!({
            "system": {},
            "user": {INSTALLED_MANIFEST: {
                "digest": content_digest(&user_files),
                "files": {"SKILL.md": URL_SAFE.encode(b"user")},
            }},
        });

        assert_eq!(
            load(&home, &params.to_string()),
            Err(format!("invalid skill name {INSTALLED_MANIFEST:?}"))
        );
        assert_eq!(fs::read(skills.join(INSTALLED_MANIFEST)).unwrap(), before);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn a_user_skill_may_replace_an_inactive_baked_skill() {
        let root = scratch("inactive-conflict");
        let home = Home {
            root: root.join("home"),
        };
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive = root.join("bundle.zip");
        let etag = write_bundle(&archive, b"system", b"system");
        install_bundle(&skills, &archive, &etag).unwrap();
        let user_files = vec![("SKILL.md".to_string(), b"user".to_vec())];
        let params = serde_json::json!({
            "system": {},
            "user": {"probe": {
                "digest": content_digest(&user_files),
                "files": {"SKILL.md": URL_SAFE.encode(b"user")},
            }},
        });

        let reply = load(&home, &params.to_string()).unwrap();
        let roots: serde_json::Value = serde_json::from_slice(&reply).unwrap();

        assert_eq!(fs::read(skills.join("probe/SKILL.md")).unwrap(), b"user");
        assert_eq!(roots["roots"]["probe"], posix_path(&skills.join("probe")));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn a_missing_requested_digest_syncs_the_bundle_before_loading() {
        let root = scratch("load-sync");
        let home = Home {
            root: root.join("home"),
        };
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let old_archive = root.join("old.zip");
        let old_etag = write_bundle(&old_archive, b"old", b"old");
        install_bundle(&skills, &old_archive, &old_etag).unwrap();
        let new_archive = root.join("new.zip");
        let new_etag = write_bundle(&new_archive, b"current", b"current");
        let bundle = fs::read(&new_archive).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let serving = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut byte = [0u8; 1];
            while !request.ends_with(b"\r\n\r\n") {
                stream.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
            }
            let head = format!(
                "HTTP/1.1 200 OK\r\netag: {new_etag}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                bundle.len()
            );
            stream.write_all(head.as_bytes()).unwrap();
            stream.write_all(&bundle).unwrap();
        });
        let session = Session::new(
            url.clone(),
            Some(url),
            "channel".to_string(),
            None,
            "session".to_string(),
            None,
            false,
            false,
        );
        let expected = content_digest(&[("SKILL.md".to_string(), b"current".to_vec())]);
        let params = serde_json::json!({"system": {"probe": expected}, "user": {}}).to_string();

        let reply = load_synced(&home, &session, &params).unwrap();
        serving.join().unwrap();
        let roots: serde_json::Value = serde_json::from_slice(&reply).unwrap();

        assert_eq!(fs::read(skills.join("probe/SKILL.md")).unwrap(), b"current");
        assert_eq!(roots["roots"]["probe"], posix_path(&skills.join("probe")));
        let _ = fs::remove_dir_all(root);
    }
}
