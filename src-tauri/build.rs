fn main() {
    // Automatically link bin/server-sidecar into target/{debug,release} during cargo builds
    if let Ok(manifest_dir) = std::env::var("CARGO_MANIFEST_DIR") {
        let manifest_path = std::path::PathBuf::from(manifest_dir);
        let bin_sidecar = manifest_path.join("bin").join("server-sidecar");
        if bin_sidecar.exists() {
            for profile in &["debug", "release"] {
                let target_dir = manifest_path.join("target").join(profile);
                if target_dir.is_dir() {
                    let symlink_path = target_dir.join("server-sidecar");
                    if !symlink_path.exists() {
                        #[cfg(unix)]
                        let _ = std::os::unix::fs::symlink(&bin_sidecar, &symlink_path);
                    }
                }
            }
        }
    }
    tauri_build::build()
}
