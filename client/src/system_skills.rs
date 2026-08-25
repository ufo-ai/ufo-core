use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::Read;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::config::Home;
use crate::wire::{Session, SystemSkillsFetch};

const DIGEST_PREFIX: &str = "sha256:";
const SKILLS_DIR: &str = "skills";
const OBJECTS_DIR: &str = "objects";
const BUNDLES_DIR: &str = "bundles";
const CURRENT_FILE: &str = "current";
const MOUNT_DIR: &str = ".skills";
const MANIFEST_FILE: &str = "manifest.json";

#[derive(Deserialize)]
struct BundleManifest {
    digest: String,
    skills: BTreeMap<String, BundleSkill>,
}

#[derive(Deserialize, Serialize)]
struct BundleSkill {
    digest: String,
}

pub fn sync(home: &Home, session: &Session) -> Result<(), String> {
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
    match fetched {
        SystemSkillsFetch::Current => Ok(()),
        SystemSkillsFetch::Downloaded(etag) => {
            let outcome = install_bundle(&root, &archive, &etag);
            let _ = fs::remove_file(archive);
            outcome
        }
    }
}

fn current_etag(root: &Path) -> Option<String> {
    let etag = fs::read_to_string(root.join(CURRENT_FILE)).ok()?;
    let bundle = bundle_id(&etag)?;
    root.join(BUNDLES_DIR)
        .join(bundle)
        .is_dir()
        .then(|| etag.trim().to_string())
}

fn install_bundle(root: &Path, archive_path: &Path, etag: &str) -> Result<(), String> {
    let bundle = bundle_id(etag).ok_or("system skills response has an invalid etag")?;
    let staging = root.join(format!(".bundle-{}", std::process::id()));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir(&staging).map_err(|error| error.to_string())?;
    let outcome = extract_bundle(archive_path, &staging, etag).and_then(|_| {
        let bundles = root.join(BUNDLES_DIR);
        fs::create_dir_all(&bundles).map_err(|error| error.to_string())?;
        let target = bundles.join(bundle);
        if target.is_dir() {
            fs::remove_dir_all(&staging).map_err(|error| error.to_string())?;
        } else {
            fs::rename(&staging, &target).map_err(|error| error.to_string())?;
        }
        fs::write(root.join(CURRENT_FILE), format!("{}\n", etag.trim()))
            .map_err(|error| error.to_string())
    });
    if outcome.is_err() {
        let _ = fs::remove_dir_all(staging);
    }
    outcome
}

fn extract_bundle(archive_path: &Path, staging: &Path, etag: &str) -> Result<(), String> {
    let file = File::open(archive_path).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|error| error.to_string())?;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).map_err(|error| error.to_string())?;
        if entry.is_dir() {
            continue;
        }
        let relative = entry
            .enclosed_name()
            .ok_or("system skills archive contains an unsafe path")?
            .to_path_buf();
        valid_archive_path(&relative)?;
        let target = staging.join(&relative);
        fs::create_dir_all(target.parent().ok_or("system skills path has no parent")?)
            .map_err(|error| error.to_string())?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
            .map_err(|error| error.to_string())?;
        std::io::copy(&mut entry, &mut output).map_err(|error| error.to_string())?;
    }
    let mut manifest = String::new();
    File::open(staging.join(MANIFEST_FILE))
        .map_err(|error| error.to_string())?
        .read_to_string(&mut manifest)
        .map_err(|error| error.to_string())?;
    let parsed: BundleManifest =
        serde_json::from_str(&manifest).map_err(|error| error.to_string())?;
    let expected = etag.trim().trim_matches('"');
    let payload = serde_json::to_vec(&serde_json::json!({"skills": &parsed.skills}))
        .map_err(|error| error.to_string())?;
    let bundle_digest = format!("{DIGEST_PREFIX}{:x}", Sha256::digest(payload));
    if parsed.digest != expected || bundle_digest != expected {
        return Err("system skills manifest does not match its etag".to_string());
    }
    for (name, skill) in parsed.skills {
        let object_id = digest_id(&skill.digest)
            .ok_or_else(|| format!("invalid system skill digest for {name}"))?;
        let object = staging.join(OBJECTS_DIR).join(object_id);
        let files = object_files(&object)?;
        if content_digest(&files) != skill.digest {
            return Err(format!(
                "system skill object does not match its digest: {name}"
            ));
        }
    }
    Ok(())
}

fn valid_archive_path(path: &Path) -> Result<(), String> {
    if path == Path::new(MANIFEST_FILE) {
        return Ok(());
    }
    let parts = path.components().collect::<Vec<_>>();
    if parts.len() < 3
        || parts[0].as_os_str() != OBJECTS_DIR
        || digest_id(&format!(
            "{DIGEST_PREFIX}{}",
            parts[1].as_os_str().to_string_lossy()
        ))
        .is_none()
        || parts
            .iter()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(format!(
            "invalid system skills archive path {}",
            path.display()
        ));
    }
    Ok(())
}

pub fn main(args: &[String]) -> i32 {
    if args.len() != 1 {
        eprintln!("usage: ufo fs system-skills <json>");
        return 2;
    }
    let workspace = match std::env::current_dir() {
        Ok(workspace) => workspace,
        Err(error) => {
            eprintln!("ufo fs system-skills: {error}");
            return 1;
        }
    };
    match load(&Home::resolve(), &workspace, &args[0]) {
        Ok(reply) => {
            println!("{}", String::from_utf8_lossy(&reply));
            0
        }
        Err(error) => {
            eprintln!("ufo fs system-skills: {error}");
            1
        }
    }
}

pub fn load(home: &Home, workspace: &Path, params: &str) -> Result<Vec<u8>, String> {
    let requested = serde_json::from_str::<BTreeMap<String, String>>(params)
        .map_err(|error| error.to_string())?;
    let mounted = mount(home, workspace, &requested)?;
    serde_json::to_vec(&serde_json::json!({"mounted": mounted})).map_err(|error| error.to_string())
}

fn mount(
    home: &Home,
    workspace: &Path,
    requested: &BTreeMap<String, String>,
) -> Result<Vec<String>, String> {
    let mut mounted = Vec::new();
    for (name, digest) in requested {
        safe_relative(name)?;
        let Some(object_id) = digest_id(digest) else {
            return Err(format!("invalid system skill digest for {name}"));
        };
        let Some(source) = find_object(home, object_id) else {
            continue;
        };
        let files = object_files(&source)?;
        if content_digest(&files) != *digest {
            continue;
        }
        for (relative, content) in files {
            write_mounted(workspace, name, &relative, &content)?;
        }
        mounted.push(name.clone());
    }
    Ok(mounted)
}

fn safe_relative(path: &str) -> Result<(), String> {
    if path.is_empty()
        || Path::new(path)
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(format!("invalid system skill path {path:?}"));
    }
    Ok(())
}

fn digest_id(digest: &str) -> Option<&str> {
    let value = digest.strip_prefix(DIGEST_PREFIX)?;
    (value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())).then_some(value)
}

fn bundle_id(etag: &str) -> Option<&str> {
    digest_id(etag.trim().trim_matches('"'))
}

fn find_object(home: &Home, object_id: &str) -> Option<PathBuf> {
    find_object_in(&home.root.join(SKILLS_DIR), object_id)
}

fn find_object_in(root: &Path, object_id: &str) -> Option<PathBuf> {
    let current = fs::read_to_string(root.join(CURRENT_FILE)).ok();
    if let Some(bundle) = current.as_deref().and_then(bundle_id) {
        let path = root
            .join(BUNDLES_DIR)
            .join(bundle)
            .join(OBJECTS_DIR)
            .join(object_id);
        if path.is_dir() {
            return Some(path);
        }
    }
    let mut bundles = fs::read_dir(root.join(BUNDLES_DIR))
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    bundles.sort();
    bundles
        .into_iter()
        .map(|bundle| bundle.join(OBJECTS_DIR).join(object_id))
        .find(|path| path.is_dir())
}

fn object_files(root: &Path) -> Result<Vec<(String, Vec<u8>)>, String> {
    if !fs::symlink_metadata(root)
        .map_err(|error| error.to_string())?
        .file_type()
        .is_dir()
    {
        return Err(format!("{} is not a system skill object", root.display()));
    }
    let mut pending = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        let mut entries = fs::read_dir(&directory)
            .map_err(|error| error.to_string())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries.into_iter().rev() {
            let path = entry.path();
            let kind = fs::symlink_metadata(&path)
                .map_err(|error| error.to_string())?
                .file_type();
            if kind.is_dir() {
                pending.push(path);
            } else if kind.is_file() {
                let relative = path
                    .strip_prefix(root)
                    .map_err(|error| error.to_string())?
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy())
                    .collect::<Vec<_>>()
                    .join("/");
                files.push((
                    relative,
                    fs::read(&path).map_err(|error| error.to_string())?,
                ));
            } else {
                return Err(format!("{} is not a regular file", path.display()));
            }
        }
    }
    files.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(files)
}

fn content_digest(files: &[(String, Vec<u8>)]) -> String {
    let mut digest = Sha256::new();
    for (path, content) in files {
        digest.update(Sha256::digest(path.as_bytes()));
        digest.update(Sha256::digest(content));
    }
    format!("{DIGEST_PREFIX}{:x}", digest.finalize())
}

#[cfg(unix)]
fn write_mounted(
    workspace: &Path,
    name: &str,
    relative: &str,
    content: &[u8],
) -> Result<(), String> {
    let path = format!("{MOUNT_DIR}/{name}/{relative}");
    let target = crate::guard::contained_file(&path, workspace, true)
        .map_err(|error| error.message().to_string())?;
    target
        .replace_bytes(content, 0o644)
        .map_err(|error| error.message().to_string())
}

#[cfg(not(unix))]
fn write_mounted(
    workspace: &Path,
    name: &str,
    relative: &str,
    content: &[u8],
) -> Result<(), String> {
    let target = workspace.join(MOUNT_DIR).join(name).join(relative);
    let parent = target.parent().ok_or("system skill target has no parent")?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    fs::write(target, content).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use zip::write::SimpleFileOptions;

    fn write_bundle(path: &Path, declared: &[u8], archived: &[u8]) -> String {
        let files = vec![("SKILL.md".to_string(), declared.to_vec())];
        let skill_digest = content_digest(&files);
        let skills = BTreeMap::from([(
            "probe".to_string(),
            BundleSkill {
                digest: skill_digest.clone(),
            },
        )]);
        let payload = serde_json::to_vec(&serde_json::json!({"skills": &skills})).unwrap();
        let digest = format!("{DIGEST_PREFIX}{:x}", Sha256::digest(payload));
        let etag = format!("\"{digest}\"");
        let mut archive = zip::ZipWriter::new(File::create(path).unwrap());
        archive
            .start_file(MANIFEST_FILE, SimpleFileOptions::default())
            .unwrap();
        archive
            .write_all(
                &serde_json::to_vec(&serde_json::json!({
                    "digest": digest,
                    "skills": skills,
                }))
                .unwrap(),
            )
            .unwrap();
        archive
            .start_file(
                format!("objects/{}/SKILL.md", digest_id(&skill_digest).unwrap()),
                SimpleFileOptions::default(),
            )
            .unwrap();
        archive.write_all(archived).unwrap();
        archive.finish().unwrap();
        etag
    }

    fn scratch(name: &str) -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("ufo-system-skills-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        root
    }

    fn cached_object(home: &Home, digest: &str) -> PathBuf {
        let bundle = "f".repeat(64);
        let skills = home.root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        fs::write(
            skills.join(CURRENT_FILE),
            format!("\"{DIGEST_PREFIX}{bundle}\"\n"),
        )
        .unwrap();
        skills
            .join(BUNDLES_DIR)
            .join(bundle)
            .join(OBJECTS_DIR)
            .join(digest_id(digest).unwrap())
    }

    #[test]
    fn mounts_only_the_content_addressed_skill_that_was_requested() {
        let root = scratch("mount");
        let home = Home {
            root: root.join("home"),
        };
        let workspace = root.join("workspace");
        fs::create_dir_all(&workspace).unwrap();
        let files = vec![
            ("SKILL.md".to_string(), b"workflow".to_vec()),
            ("scripts/run.py".to_string(), b"print('ok')".to_vec()),
        ];
        let digest = content_digest(&files);
        let object = cached_object(&home, &digest);
        for (path, content) in &files {
            let target = object.join(path);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, content).unwrap();
        }
        let mounted = mount(
            &home,
            &workspace,
            &BTreeMap::from([("probe".to_string(), digest)]),
        )
        .unwrap();
        assert_eq!(mounted, vec!["probe"]);
        assert_eq!(
            fs::read(workspace.join(".skills/probe/scripts/run.py")).unwrap(),
            b"print('ok')"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn changed_cached_bytes_are_not_mounted() {
        let root = scratch("changed");
        let home = Home {
            root: root.join("home"),
        };
        let workspace = root.join("workspace");
        fs::create_dir_all(&workspace).unwrap();
        let expected = vec![("SKILL.md".to_string(), b"expected".to_vec())];
        let digest = content_digest(&expected);
        let object = cached_object(&home, &digest);
        fs::create_dir_all(&object).unwrap();
        fs::write(object.join("SKILL.md"), b"changed").unwrap();
        let mounted = mount(
            &home,
            &workspace,
            &BTreeMap::from([("probe".to_string(), digest)]),
        )
        .unwrap();
        assert!(mounted.is_empty());
        assert!(!workspace.join(".skills/probe/SKILL.md").exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn installs_a_downloaded_bundle_under_ufo_home() {
        let root = scratch("install");
        let skills = root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive_path = root.join("bundle.zip");
        let etag = write_bundle(&archive_path, b"workflow", b"workflow");
        assert_eq!(
            etag,
            "\"sha256:10936b8d47be11283777bb3e27dee5f7d8d0c7c116f424a55c7ec4f1ec390c98\""
        );

        install_bundle(&skills, &archive_path, &etag).unwrap();

        let bundle = bundle_id(&etag).unwrap();
        let object_digest = content_digest(&[("SKILL.md".to_string(), b"workflow".to_vec())]);
        let object = digest_id(&object_digest).unwrap();
        assert_eq!(
            fs::read(
                skills
                    .join(BUNDLES_DIR)
                    .join(bundle)
                    .join(format!("objects/{object}/SKILL.md"))
            )
            .unwrap(),
            b"workflow"
        );
        assert_eq!(
            fs::read_to_string(skills.join(CURRENT_FILE))
                .unwrap()
                .trim(),
            etag
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn refuses_a_bundle_whose_object_bytes_changed() {
        let root = scratch("changed-bundle");
        let skills = root.join(SKILLS_DIR);
        fs::create_dir_all(&skills).unwrap();
        let archive_path = root.join("bundle.zip");
        let etag = write_bundle(&archive_path, b"expected", b"changed");

        assert!(install_bundle(&skills, &archive_path, &etag).is_err());
        assert!(!skills.join(CURRENT_FILE).exists());
        let _ = fs::remove_dir_all(root);
    }
}
