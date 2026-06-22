//! IPC message types + framing for daemon communication. Ports `protocol.py`.
//!
//! Wire format: a 4-byte big-endian length prefix followed by a msgpack
//! payload (one message per frame). Rust-to-Rust only — not wire-compatible
//! with the Python daemon's `multiprocessing.connection` + msgspec framing.

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Protocol/daemon version. A handshake mismatch triggers a daemon restart.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

// ---------------------------------------------------------------------------
// Shared payloads
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IndexingProgress {
    pub num_execution_starts: i64,
    pub num_unchanged: i64,
    pub num_adds: i64,
    pub num_deletes: i64,
    pub num_reprocesses: i64,
    pub num_errors: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SearchResult {
    pub file_path: String,
    pub language: String,
    pub content: String,
    pub start_line: i64,
    pub end_line: i64,
    pub score: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DaemonProjectInfo {
    pub project_root: String,
    pub indexing: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DbPathMappingEntry {
    pub source: String,
    pub target: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DoctorCheckResult {
    pub name: String,
    pub ok: bool,
    pub details: Vec<String>,
    pub errors: Vec<String>,
    #[serde(default)]
    pub traceback: Option<String>,
}

// ---------------------------------------------------------------------------
// Requests / responses (externally tagged enums)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Request {
    Handshake { version: String },
    Index { project_root: String },
    Search {
        project_root: String,
        query: String,
        languages: Option<Vec<String>>,
        paths: Option<Vec<String>>,
        limit: i64,
        offset: i64,
    },
    ProjectStatus { project_root: String },
    DaemonStatus,
    RemoveProject { project_root: String },
    Stop,
    Doctor { project_root: Option<String> },
    DaemonEnv,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Response {
    Handshake {
        ok: bool,
        daemon_version: String,
        global_settings_mtime_us: Option<i64>,
        warnings: Vec<String>,
    },
    Index { success: bool, message: Option<String> },
    IndexProgress { progress: IndexingProgress },
    IndexWaiting,
    Search {
        success: bool,
        results: Vec<SearchResult>,
        total_returned: i64,
        offset: i64,
        message: Option<String>,
    },
    ProjectStatus {
        indexing: bool,
        total_chunks: i64,
        total_files: i64,
        languages: std::collections::BTreeMap<String, i64>,
        progress: Option<IndexingProgress>,
        index_exists: bool,
    },
    DaemonStatus {
        version: String,
        uptime_seconds: f64,
        projects: Vec<DaemonProjectInfo>,
    },
    RemoveProject { ok: bool },
    Stop { ok: bool },
    Doctor { result: DoctorCheckResult, final_: bool },
    DaemonEnv {
        env_names: Vec<String>,
        settings_env_names: Vec<String>,
        #[serde(default)]
        db_path_mappings: Vec<DbPathMappingEntry>,
        #[serde(default)]
        host_path_mappings: Vec<DbPathMappingEntry>,
    },
    Error { message: String, traceback: Option<String> },
}

// ---------------------------------------------------------------------------
// Framing
// ---------------------------------------------------------------------------

/// Upper bound on a single frame's payload. Generous for any real request
/// (search results, index status) while bounding allocation from a bad header.
const MAX_FRAME_BYTES: usize = 256 * 1024 * 1024;

pub async fn write_msg<W, T>(w: &mut W, msg: &T) -> std::io::Result<()>
where
    W: AsyncWriteExt + Unpin,
    T: Serialize,
{
    let buf = rmp_serde::to_vec_named(msg)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let len = u32::try_from(buf.len())
        .map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "frame too large"))?;
    w.write_all(&len.to_be_bytes()).await?;
    w.write_all(&buf).await?;
    w.flush().await
}

pub async fn read_msg<R, T>(r: &mut R) -> std::io::Result<T>
where
    R: AsyncReadExt + Unpin,
    T: DeserializeOwned,
{
    let mut len = [0u8; 4];
    r.read_exact(&mut len).await?;
    let n = u32::from_be_bytes(len) as usize;
    // Reject an implausible length before allocating: a bad/truncated frame
    // would otherwise force a multi-GiB allocation from a 4-byte header.
    if n > MAX_FRAME_BYTES {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("frame too large: {n} bytes (max {MAX_FRAME_BYTES})"),
        ));
    }
    let mut buf = vec![0u8; n];
    r.read_exact(&mut buf).await?;
    rmp_serde::from_slice(&buf)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}
