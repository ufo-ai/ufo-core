use std::env;
use std::fs;
use std::path::PathBuf;

const TEST_GH_ARCHIVE: &[u8] = &[
    31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 83, 86, 212, 79, 202, 204, 211, 47, 206, 224, 42, 40, 202,
    204, 43, 73, 83, 80, 42, 77, 203, 215, 77, 207, 208, 45, 73, 45, 46, 177, 82, 45, 86, 82, 80,
    82, 113, 247, 119, 113, 117, 10, 117, 87, 226, 2, 0, 91, 139, 8, 179, 45, 0, 0, 0,
];

fn main() {
    println!("cargo:rerun-if-env-changed=UFO_GATEWAY_URL_DEFAULT");
    if let Some(gateway) = env::var_os("UFO_GATEWAY_URL_DEFAULT") {
        let gateway = gateway.to_string_lossy();
        if !gateway.starts_with("https://") && !gateway.starts_with("http://") {
            panic!("UFO_GATEWAY_URL_DEFAULT must be an http(s) URL, not {gateway:?}");
        }
    }
    println!("cargo:rerun-if-env-changed=UFO_GH_ARCHIVE");
    let destination = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR is set")).join("gh.gz");
    match env::var_os("UFO_GH_ARCHIVE") {
        Some(source) => {
            println!(
                "cargo:rerun-if-changed={}",
                PathBuf::from(&source).display()
            );
            fs::copy(&source, &destination).unwrap_or_else(|error| {
                panic!(
                    "could not embed {}: {error}",
                    PathBuf::from(source).display()
                )
            });
        }
        None if env::var("PROFILE").as_deref() == Ok("release") => {
            panic!("UFO_GH_ARCHIVE is required for release builds");
        }
        None => fs::write(&destination, TEST_GH_ARCHIVE)
            .unwrap_or_else(|error| panic!("could not write test gh: {error}")),
    }
}
