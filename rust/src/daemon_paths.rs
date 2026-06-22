//! Daemon filesystem paths. Ports `_daemon_paths.py`.

use std::path::PathBuf;

use crate::settings::user_settings_dir;

/// Directory holding `daemon.sock`, `daemon.pid`, `daemon.log`. Override with
/// `COCOINDEX_CODE_RUNTIME_DIR`; defaults to the user-settings dir.
pub fn daemon_runtime_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("COCOINDEX_CODE_RUNTIME_DIR") {
        return PathBuf::from(dir);
    }
    user_settings_dir()
}

pub fn daemon_socket_path() -> PathBuf {
    daemon_runtime_dir().join("daemon.sock")
}

pub fn daemon_pid_path() -> PathBuf {
    daemon_runtime_dir().join("daemon.pid")
}

pub fn daemon_log_path() -> PathBuf {
    daemon_runtime_dir().join("daemon.log")
}
