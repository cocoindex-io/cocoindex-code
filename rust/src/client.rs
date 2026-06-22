//! Client for the daemon. Ports `client.py`.
//!
//! Per-request connection model: each call connects, performs the version
//! handshake (auto-starting/restarting the daemon as needed), sends one
//! request, reads the response(s), and closes.

use std::os::unix::process::CommandExt;
use std::path::Path;
use std::time::Duration;

use anyhow::{Result, bail};

use crate::daemon_paths::{daemon_log_path, daemon_pid_path, daemon_runtime_dir, daemon_socket_path};
use crate::protocol::{
    DaemonProjectInfo, DoctorCheckResult, Request, Response, SearchResult, VERSION, read_msg,
    write_msg,
};
use crate::settings::global_settings_mtime_us;
use tokio::net::UnixStream;

enum Outcome {
    Connected(UnixStream),
    NeedsRestart,
    Unreachable,
}

async fn raw_connect() -> Result<Outcome> {
    let sock = daemon_socket_path();
    if !sock.exists() {
        return Ok(Outcome::Unreachable);
    }
    let mut stream = match UnixStream::connect(&sock).await {
        Ok(s) => s,
        Err(_) => return Ok(Outcome::Unreachable),
    };
    if write_msg(&mut stream, &Request::Handshake { version: VERSION.to_string() })
        .await
        .is_err()
    {
        return Ok(Outcome::Unreachable);
    }
    let resp: Response = match read_msg(&mut stream).await {
        Ok(r) => r,
        Err(_) => return Ok(Outcome::Unreachable),
    };
    match resp {
        Response::Handshake { ok, global_settings_mtime_us: daemon_mtime, warnings, .. } => {
            if !ok || global_settings_mtime_us() != daemon_mtime {
                return Ok(Outcome::NeedsRestart);
            }
            print_warnings(&warnings);
            Ok(Outcome::Connected(stream))
        }
        _ => Ok(Outcome::Unreachable),
    }
}

/// When set, an external supervisor (Docker entrypoint, systemd, …) owns daemon
/// respawn; the client never spawns one — it just waits for the socket.
fn is_daemon_supervised() -> bool {
    std::env::var("COCOINDEX_CODE_DAEMON_SUPERVISED").as_deref() == Ok("1")
}

/// Connect to the daemon, auto-starting or restarting it as needed.
async fn connect() -> Result<UnixStream> {
    match raw_connect().await? {
        Outcome::Connected(s) => return Ok(s),
        Outcome::NeedsRestart => stop_daemon().await,
        Outcome::Unreachable => {}
    }
    if is_daemon_supervised() {
        // Supervisor restarts it; just wait for the socket to reappear.
        wait_for_socket(None, Duration::from_secs(30)).await?;
    } else {
        let child = spawn_daemon()?;
        wait_for_socket(Some(child), Duration::from_secs(30)).await?;
    }
    for _ in 0..10 {
        if let Outcome::Connected(s) = raw_connect().await? {
            return Ok(s);
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    bail!("Failed to connect to daemon after starting it")
}

/// Daemon-side handshake warnings already surfaced this process (printed once).
static SURFACED_WARNINGS: std::sync::OnceLock<std::sync::Mutex<std::collections::HashSet<String>>> =
    std::sync::OnceLock::new();

fn print_warnings(warnings: &[String]) {
    let set = SURFACED_WARNINGS.get_or_init(Default::default);
    let mut seen = set.lock().unwrap();
    for w in warnings {
        if seen.insert(w.clone()) {
            eprintln!("Warning: {w}");
        }
    }
}

// ---------------------------------------------------------------------------
// Public request API
// ---------------------------------------------------------------------------

async fn send_one(req: Request) -> Result<Response> {
    let mut stream = connect().await?;
    write_msg(&mut stream, &req).await?;
    let resp: Response = read_msg(&mut stream).await?;
    if let Response::Error { message, .. } = &resp {
        bail!("Daemon error: {message}");
    }
    Ok(resp)
}

/// Run indexing; `on_waiting` is called if another index is in progress.
pub async fn index(project_root: &str, on_waiting: impl Fn()) -> Result<()> {
    let mut stream = connect().await?;
    write_msg(&mut stream, &Request::Index { project_root: project_root.to_string() }).await?;
    loop {
        let resp: Response = read_msg(&mut stream).await?;
        match resp {
            Response::IndexWaiting => on_waiting(),
            Response::IndexProgress { .. } => {}
            Response::Index { success: true, .. } => return Ok(()),
            Response::Index { success: false, message } => {
                bail!("Indexing failed: {}", message.unwrap_or_default())
            }
            Response::Error { message, .. } => bail!("Daemon error: {message}"),
            other => bail!("Unexpected response: {other:?}"),
        }
    }
}

pub struct SearchOutcome {
    pub success: bool,
    pub results: Vec<SearchResult>,
    pub message: Option<String>,
}

pub async fn search(
    project_root: &str,
    query: &str,
    languages: Option<Vec<String>>,
    paths: Option<Vec<String>>,
    limit: i64,
    offset: i64,
    on_waiting: impl Fn(),
) -> Result<SearchOutcome> {
    let mut stream = connect().await?;
    write_msg(
        &mut stream,
        &Request::Search {
            project_root: project_root.to_string(),
            query: query.to_string(),
            languages,
            paths,
            limit,
            offset,
        },
    )
    .await?;
    loop {
        let resp: Response = read_msg(&mut stream).await?;
        match resp {
            Response::IndexWaiting => on_waiting(),
            Response::Search { success, results, message, .. } => {
                return Ok(SearchOutcome { success, results, message });
            }
            Response::Error { message, .. } => bail!("Daemon error: {message}"),
            other => bail!("Unexpected response: {other:?}"),
        }
    }
}

pub struct ProjectStatus {
    pub indexing: bool,
    pub total_chunks: i64,
    pub total_files: i64,
    pub languages: std::collections::BTreeMap<String, i64>,
    pub index_exists: bool,
}

pub async fn project_status(project_root: &str) -> Result<ProjectStatus> {
    match send_one(Request::ProjectStatus { project_root: project_root.to_string() }).await? {
        Response::ProjectStatus { indexing, total_chunks, total_files, languages, index_exists, .. } => {
            Ok(ProjectStatus { indexing, total_chunks, total_files, languages, index_exists })
        }
        other => bail!("Unexpected response: {other:?}"),
    }
}

pub struct DaemonStatus {
    pub version: String,
    pub uptime_seconds: f64,
    pub projects: Vec<DaemonProjectInfo>,
}

pub async fn daemon_status() -> Result<DaemonStatus> {
    match send_one(Request::DaemonStatus).await? {
        Response::DaemonStatus { version, uptime_seconds, projects } => {
            Ok(DaemonStatus { version, uptime_seconds, projects })
        }
        other => bail!("Unexpected response: {other:?}"),
    }
}

pub async fn remove_project(project_root: &str) -> Result<()> {
    send_one(Request::RemoveProject { project_root: project_root.to_string() }).await?;
    Ok(())
}

pub struct DaemonEnv {
    pub env_names: Vec<String>,
    pub settings_env_names: Vec<String>,
}

pub async fn daemon_env() -> Result<DaemonEnv> {
    match send_one(Request::DaemonEnv).await? {
        Response::DaemonEnv { env_names, settings_env_names, .. } => {
            Ok(DaemonEnv { env_names, settings_env_names })
        }
        other => bail!("Unexpected response: {other:?}"),
    }
}

pub async fn doctor(project_root: Option<&str>) -> Result<Vec<DoctorCheckResult>> {
    let mut stream = connect().await?;
    write_msg(
        &mut stream,
        &Request::Doctor { project_root: project_root.map(str::to_string) },
    )
    .await?;
    let mut results = Vec::new();
    loop {
        let resp: Response = read_msg(&mut stream).await?;
        match resp {
            Response::Doctor { result, final_ } => {
                if final_ {
                    break;
                }
                results.push(result);
            }
            Response::Error { message, .. } => bail!("Daemon error: {message}"),
            other => bail!("Unexpected response: {other:?}"),
        }
    }
    Ok(results)
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

pub fn is_daemon_running() -> bool {
    daemon_socket_path().exists()
}

fn spawn_daemon() -> Result<std::process::Child> {
    std::fs::create_dir_all(daemon_runtime_dir())?;
    let log = std::fs::File::create(daemon_log_path())?;
    let exe = std::env::current_exe()?;
    let child = std::process::Command::new(exe)
        .arg("run-daemon")
        .stdin(std::process::Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log)
        .process_group(0) // detach from the controlling terminal
        .spawn()?;
    Ok(child)
}

/// Wait for the daemon socket to appear. When `child` is the process we spawned,
/// poll it each iteration: if it exits before the socket is ready, fail fast
/// with the daemon log instead of waiting out the full timeout (ports
/// `_wait_for_daemon`'s `proc.poll()` early-death detection). The socket check
/// runs *before* the exit check so a supervisor winning the bind isn't flagged.
async fn wait_for_socket(mut child: Option<std::process::Child>, timeout: Duration) -> Result<()> {
    let sock = daemon_socket_path();
    let deadline = tokio::time::Instant::now() + timeout;
    while tokio::time::Instant::now() < deadline {
        if sock.exists() {
            return Ok(());
        }
        if let Some(c) = child.as_mut() {
            if matches!(c.try_wait(), Ok(Some(_))) {
                let log = read_log().unwrap_or_default();
                bail!("Daemon process exited before it became ready.\n\nDaemon log:\n{log}");
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    let log = read_log().unwrap_or_default();
    bail!("Daemon did not start in time.\n\nDaemon log:\n{log}")
}

fn read_log() -> Option<String> {
    std::fs::read_to_string(daemon_log_path()).ok().filter(|s| !s.trim().is_empty())
}

fn read_pid() -> Option<i32> {
    let text = std::fs::read_to_string(daemon_pid_path()).ok()?;
    let pid: i32 = text.trim().parse().ok()?;
    if pid == std::process::id() as i32 { None } else { Some(pid) }
}

fn pid_alive(pid: i32) -> bool {
    // kill(pid, 0) probes existence.
    unsafe { libc::kill(pid, 0) == 0 }
}

async fn wait_for_exit(timeout: Duration) -> bool {
    let pid_path = daemon_pid_path();
    let deadline = tokio::time::Instant::now() + timeout;
    while tokio::time::Instant::now() < deadline {
        if !pid_path.exists() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    !pid_path.exists()
}

/// Stop the daemon: StopRequest → SIGTERM → SIGKILL.
pub async fn stop_daemon() {
    // New session: re-surface warnings after a restart (mirrors Python clearing
    // `_surfaced_warnings` in `stop_daemon`).
    if let Some(set) = SURFACED_WARNINGS.get() {
        set.lock().unwrap().clear();
    }
    let pid = read_pid();

    // 1. Graceful stop over the socket (bypassing auto-start).
    if let Ok(Outcome::Connected(mut stream)) = raw_connect().await {
        let _ = write_msg(&mut stream, &Request::Stop).await;
        let _: Result<Response, _> = read_msg(&mut stream).await;
    }
    if wait_for_exit(Duration::from_secs(3)).await {
        return;
    }

    // 2. SIGTERM.
    if let Some(pid) = pid {
        if pid_alive(pid) {
            unsafe { libc::kill(pid, libc::SIGTERM) };
        }
        if wait_for_exit(Duration::from_secs(2)).await {
            return;
        }
        // 3. SIGKILL.
        if pid_alive(pid) {
            unsafe { libc::kill(pid, libc::SIGKILL) };
        }
    }
    cleanup_stale_files(pid);
}

fn cleanup_stale_files(pid: Option<i32>) {
    let _ = std::fs::remove_file(daemon_socket_path());
    // Only remove the PID file if it still names the daemon we just stopped, so
    // we don't clobber a newer daemon's PID file (mirrors Python's guard).
    match pid {
        Some(pid) => {
            if let Ok(stored) = std::fs::read_to_string(daemon_pid_path()) {
                if stored.trim() == pid.to_string() {
                    let _ = std::fs::remove_file(daemon_pid_path());
                }
            }
        }
        None => {
            let _ = std::fs::remove_file(daemon_pid_path());
        }
    }
}

/// Start the daemon and wait for it to be reachable.
pub async fn start_and_wait() -> Result<()> {
    let child = spawn_daemon()?;
    wait_for_socket(Some(child), Duration::from_secs(30)).await
}

/// Stop and wait, reporting whether it stopped cleanly.
pub async fn stop_and_report() -> bool {
    stop_daemon().await;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    while tokio::time::Instant::now() < deadline {
        if !daemon_pid_path().exists() && !is_daemon_running() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    !daemon_pid_path().exists() && !is_daemon_running()
}

// Used by `reset` so an unreachable daemon doesn't abort the reset.
pub async fn try_remove_project(project_root: &Path) {
    let _ = remove_project(&project_root.to_string_lossy()).await;
}
