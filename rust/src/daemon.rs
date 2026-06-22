//! Daemon process: Unix-socket listener, project registry, request dispatch.
//! Ports `daemon.py` (+ the registry/dispatch from `project.py`).

use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Result, bail};
use sqlx::Row;
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::{Mutex, Notify};

use crate::daemon_paths::{daemon_pid_path, daemon_runtime_dir, daemon_socket_path};
use crate::embedder::{CodeEmbedder, create_embedder};
use crate::embedder_params::{Params, resolve_embedder_params};
use crate::protocol::{
    DaemonProjectInfo, DoctorCheckResult, Request, Response, SearchResult, VERSION, read_msg,
    write_msg,
};
use crate::schema::TABLE_NAME;
use crate::settings::{
    UserSettings, global_settings_mtime_us, load_project_settings, load_user_settings,
    target_sqlite_db_path, user_settings_path,
};

// ---------------------------------------------------------------------------
// Project
// ---------------------------------------------------------------------------

struct Project {
    root: PathBuf,
    embedder: CodeEmbedder,
    query_params: Params,
    index_lock: Mutex<()>,
    /// Whether a load-time initial index has been kicked off, and whether it
    /// has finished. Ports `Project._initial_index_done` + the
    /// `ensure_indexing_started` / `should_wait_for_indexing` machinery.
    initial_started: std::sync::atomic::AtomicBool,
    initial_done: std::sync::atomic::AtomicBool,
    done_notify: Notify,
}

impl Project {
    fn is_indexing(&self) -> bool {
        self.index_lock.try_lock().is_err()
    }

    /// Kick off the load-time index once (idempotent), returning immediately.
    fn ensure_indexing_started(self: &Arc<Self>) {
        use std::sync::atomic::Ordering;
        if self.initial_started.swap(true, Ordering::AcqRel) {
            return; // already started
        }
        let me = self.clone();
        tokio::spawn(async move {
            if let Err(e) = me.run_index().await {
                eprintln!("initial index failed for {}: {e}", me.root.display());
            }
            me.initial_done.store(true, Ordering::Release);
            me.done_notify.notify_waiters();
        });
    }

    fn should_wait_for_indexing(&self) -> bool {
        !self.initial_done.load(std::sync::atomic::Ordering::Acquire)
    }

    async fn wait_for_indexing_done(&self) {
        loop {
            let notified = self.done_notify.notified();
            // `Notified` only registers the waiter on first poll, so it must be
            // enabled *before* the flag check. Otherwise a `notify_waiters()`
            // firing between the check and the `.await` is missed and the waiter
            // parks forever (the only notify already happened).
            tokio::pin!(notified);
            notified.as_mut().enable();
            if self.initial_done.load(std::sync::atomic::Ordering::Acquire) {
                return;
            }
            notified.await;
        }
    }

    async fn run_index(&self) -> Result<()> {
        let _guard = self.index_lock.lock().await;
        let ps = load_project_settings(&self.root)?;
        crate::indexer::run_index(&self.root, &self.embedder, &ps).await?;
        Ok(())
    }

    async fn search(
        &self,
        query: &str,
        languages: &[String],
        paths: &[String],
        limit: i64,
        offset: i64,
    ) -> Result<Vec<SearchResult>> {
        let db_path = target_sqlite_db_path(&self.root);
        if !db_path.exists() {
            bail!(
                "Index database not found at {}. Run `ccc index` first.",
                db_path.display()
            );
        }
        let query_vec = self.embedder.embed(query, &self.query_params).await?;
        let pool = crate::db::open_readonly_pool(&db_path).await?;
        let results =
            crate::query::query_codebase(&pool, &query_vec, limit, offset, languages, paths).await?;
        Ok(results
            .into_iter()
            .map(|r| SearchResult {
                file_path: r.file_path,
                language: r.language,
                content: r.content,
                start_line: r.start_line,
                end_line: r.end_line,
                score: r.score,
            })
            .collect())
    }

    async fn status(&self) -> Response {
        let indexing = self.is_indexing();
        let db_path = target_sqlite_db_path(&self.root);
        match index_counts(&db_path).await {
            Ok((total_chunks, total_files, languages)) => Response::ProjectStatus {
                indexing,
                total_chunks,
                total_files,
                languages,
                progress: None,
                index_exists: true,
            },
            Err(_) => Response::ProjectStatus {
                indexing,
                total_chunks: 0,
                total_files: 0,
                languages: BTreeMap::new(),
                progress: None,
                index_exists: false,
            },
        }
    }
}

/// Query chunk/file/language counts from the index db.
async fn index_counts(db_path: &std::path::Path) -> Result<(i64, i64, BTreeMap<String, i64>)> {
    if !db_path.exists() {
        bail!("no index");
    }
    let pool = crate::db::open_readonly_pool(db_path).await?;
    let total_chunks: i64 = sqlx::query(&format!("SELECT COUNT(*) AS c FROM {TABLE_NAME}"))
        .fetch_one(&pool)
        .await?
        .try_get("c")?;
    let total_files: i64 =
        sqlx::query(&format!("SELECT COUNT(DISTINCT file_path) AS c FROM {TABLE_NAME}"))
            .fetch_one(&pool)
            .await?
            .try_get("c")?;
    let rows = sqlx::query(&format!(
        "SELECT language, COUNT(*) AS cnt FROM {TABLE_NAME} GROUP BY language ORDER BY cnt DESC"
    ))
    .fetch_all(&pool)
    .await?;
    let mut languages = BTreeMap::new();
    for row in rows {
        let lang: String = row.try_get("language")?;
        let cnt: i64 = row.try_get("cnt")?;
        languages.insert(lang, cnt);
    }
    Ok((total_chunks, total_files, languages))
}

// ---------------------------------------------------------------------------
// Project registry
// ---------------------------------------------------------------------------

struct ProjectRegistry {
    embedder: Option<CodeEmbedder>,
    /// When the embedder is `None` because loading failed (rather than absent
    /// settings), this carries the underlying error so clients see the real
    /// cause instead of a misleading "run `ccc init`" message.
    embedder_error: Option<String>,
    indexing_params: Params,
    query_params: Params,
    projects: Mutex<HashMap<String, Arc<Project>>>,
}

impl ProjectRegistry {
    fn no_embedder_error(&self) -> anyhow::Error {
        match &self.embedder_error {
            Some(e) => anyhow::anyhow!(e.clone()),
            None => anyhow::anyhow!(
                "Daemon has no global settings loaded. Run `ccc init` to set up cocoindex-code."
            ),
        }
    }

    async fn get_project(&self, root: &str) -> Result<Arc<Project>> {
        let Some(embedder) = &self.embedder else {
            return Err(self.no_embedder_error());
        };
        let mut projects = self.projects.lock().await;
        if let Some(p) = projects.get(root) {
            return Ok(p.clone());
        }
        let project = Arc::new(Project {
            root: PathBuf::from(root),
            embedder: embedder.clone(),
            query_params: self.query_params.clone(),
            index_lock: Mutex::new(()),
            initial_started: std::sync::atomic::AtomicBool::new(false),
            initial_done: std::sync::atomic::AtomicBool::new(false),
            done_notify: Notify::new(),
        });
        projects.insert(root.to_string(), project.clone());
        Ok(project)
    }

    async fn remove_project(&self, root: &str) -> bool {
        self.projects.lock().await.remove(root).is_some()
    }

    async fn list_projects(&self) -> Vec<DaemonProjectInfo> {
        self.projects
            .lock()
            .await
            .iter()
            .map(|(root, p)| DaemonProjectInfo {
                project_root: root.clone(),
                indexing: p.is_indexing(),
            })
            .collect()
    }
}

// ---------------------------------------------------------------------------
// Shared daemon state
// ---------------------------------------------------------------------------

struct DaemonState {
    registry: ProjectRegistry,
    start_time: std::time::Instant,
    settings_mtime_us: Option<i64>,
    settings_env_names: Vec<String>,
    handshake_warnings: Vec<String>,
    shutdown: Notify,
}

// ---------------------------------------------------------------------------
// Connection handling
// ---------------------------------------------------------------------------

async fn handle_connection(mut stream: UnixStream, state: Arc<DaemonState>) {
    // 1. Handshake.
    let req: Request = match read_msg(&mut stream).await {
        Ok(r) => r,
        Err(_) => return,
    };
    let Request::Handshake { version } = &req else {
        let _ = write_msg(
            &mut stream,
            &Response::Error {
                message: "First message must be a handshake".to_string(),
                traceback: None,
            },
        )
        .await;
        return;
    };
    let ok = version == VERSION;
    if write_msg(
        &mut stream,
        &Response::Handshake {
            ok,
            daemon_version: VERSION.to_string(),
            global_settings_mtime_us: state.settings_mtime_us,
            warnings: state.handshake_warnings.clone(),
        },
    )
    .await
    .is_err()
        || !ok
    {
        return;
    }

    // 2. One request.
    let req: Request = match read_msg(&mut stream).await {
        Ok(r) => r,
        Err(_) => return,
    };
    dispatch(req, &mut stream, &state).await;
}

async fn dispatch(req: Request, stream: &mut UnixStream, state: &Arc<DaemonState>) {
    let reg = &state.registry;
    let result: std::result::Result<(), ()> = match req {
        Request::Index { project_root } => stream_index(stream, reg, &project_root).await,
        Request::Search {
            project_root,
            query,
            languages,
            paths,
            limit,
            offset,
        } => {
            match reg.get_project(&project_root).await {
                Ok(project) => {
                    // Kick off load-time indexing; wait for it before searching
                    // (ports `ensure_indexing_started` + `_search_with_wait`).
                    project.ensure_indexing_started();
                    if project.should_wait_for_indexing() {
                        if send(stream, &Response::IndexWaiting).await.is_err() {
                            return;
                        }
                        project.wait_for_indexing_done().await;
                    }
                    let resp = match project
                        .search(
                            &query,
                            &languages.unwrap_or_default(),
                            &paths.unwrap_or_default(),
                            limit,
                            offset,
                        )
                        .await
                    {
                        Ok(results) => Response::Search {
                            total_returned: results.len() as i64,
                            results,
                            success: true,
                            offset,
                            message: None,
                        },
                        Err(e) => error_resp(e),
                    };
                    send(stream, &resp).await
                }
                Err(e) => send(stream, &error_resp(e)).await,
            }
        }
        Request::ProjectStatus { project_root } => {
            let resp = match reg.get_project(&project_root).await {
                Ok(project) => {
                    project.ensure_indexing_started();
                    project.status().await
                }
                Err(e) => error_resp(e),
            };
            send(stream, &resp).await
        }
        Request::DaemonStatus => {
            let resp = Response::DaemonStatus {
                version: VERSION.to_string(),
                uptime_seconds: state.start_time.elapsed().as_secs_f64(),
                projects: reg.list_projects().await,
            };
            send(stream, &resp).await
        }
        Request::RemoveProject { project_root } => {
            reg.remove_project(&project_root).await;
            send(stream, &Response::RemoveProject { ok: true }).await
        }
        Request::Stop => {
            let r = send(stream, &Response::Stop { ok: true }).await;
            state.shutdown.notify_one();
            r
        }
        Request::Doctor { project_root } => handle_doctor(stream, reg, project_root).await,
        Request::DaemonEnv => {
            let mut env_names: Vec<String> = std::env::vars().map(|(k, _)| k).collect();
            env_names.sort();
            send(
                stream,
                &Response::DaemonEnv {
                    env_names,
                    settings_env_names: state.settings_env_names.clone(),
                    // Container path-mapping support is deferred; report empty.
                    db_path_mappings: vec![],
                    host_path_mappings: vec![],
                },
            )
            .await
        }
        Request::Handshake { .. } => send(
            stream,
            &Response::Error {
                message: "Unexpected second handshake".to_string(),
                traceback: None,
            },
        )
        .await,
    };
    let _ = result;
}

fn error_resp(e: anyhow::Error) -> Response {
    Response::Error { message: e.to_string(), traceback: None }
}

async fn send(stream: &mut UnixStream, resp: &Response) -> std::result::Result<(), ()> {
    write_msg(stream, resp).await.map_err(|_| ())
}

async fn stream_index(
    stream: &mut UnixStream,
    reg: &ProjectRegistry,
    project_root: &str,
) -> std::result::Result<(), ()> {
    let project = match reg.get_project(project_root).await {
        Ok(p) => p,
        Err(e) => return send(stream, &error_resp(e)).await,
    };
    if project.is_indexing() {
        send(stream, &Response::IndexWaiting).await?;
    }
    let resp = match project.run_index().await {
        Ok(()) => Response::Index { success: true, message: None },
        Err(e) => Response::Index { success: false, message: Some(e.to_string()) },
    };
    // Any completed index satisfies the load-time "initial index done" gate, so
    // a subsequent search doesn't redundantly wait/re-index (parity for
    // `_run_index_inner` setting `_initial_index_done`).
    use std::sync::atomic::Ordering;
    project.initial_started.store(true, Ordering::Release);
    project.initial_done.store(true, Ordering::Release);
    project.done_notify.notify_waiters();
    send(stream, &resp).await
}

async fn handle_doctor(
    stream: &mut UnixStream,
    reg: &ProjectRegistry,
    project_root: Option<String>,
) -> std::result::Result<(), ()> {
    let mut results: Vec<DoctorCheckResult> = Vec::new();
    match project_root {
        None => {
            results.push(check_model(reg, "indexing", &reg.indexing_params).await);
            results.push(check_model(reg, "query", &reg.query_params).await);
        }
        Some(root) => {
            results.push(check_file_walk(&root));
            results.push(check_index_status(&root).await);
        }
    }
    for r in results {
        send(stream, &Response::Doctor { result: r, final_: false }).await?;
    }
    send(
        stream,
        &Response::Doctor {
            result: DoctorCheckResult {
                name: "done".to_string(),
                ok: true,
                details: vec![],
                errors: vec![],
                traceback: None,
            },
            final_: true,
        },
    )
    .await
}

async fn check_model(reg: &ProjectRegistry, label: &str, params: &Params) -> DoctorCheckResult {
    let name = format!("Model Check ({label})");
    let Some(embedder) = &reg.embedder else {
        return DoctorCheckResult {
            name,
            ok: false,
            details: vec![],
            errors: vec![reg.no_embedder_error().to_string()],
            traceback: None,
        };
    };
    let params_detail = if params.is_empty() {
        "params: {} (no extra kwargs)".to_string()
    } else {
        format!("params: {}", serde_json::Value::Object(params.clone()))
    };
    match embedder.embed("hello world", params).await {
        Ok(v) => DoctorCheckResult {
            name,
            ok: true,
            details: vec![params_detail, format!("Embedding dimension: {}", v.len())],
            errors: vec![],
            traceback: None,
        },
        Err(e) => DoctorCheckResult {
            name,
            ok: false,
            details: vec![params_detail],
            errors: vec![e.to_string()],
            traceback: None,
        },
    }
}

fn check_file_walk(root: &str) -> DoctorCheckResult {
    let root_path = PathBuf::from(root);
    let ps = match load_project_settings(&root_path) {
        Ok(p) => p,
        Err(e) => {
            return DoctorCheckResult {
                name: "File Walk".to_string(),
                ok: false,
                details: vec![],
                errors: vec![e.to_string()],
                traceback: None,
            };
        }
    };
    let matcher =
        match crate::walk::GitignoreAwareMatcher::new(&root_path, &ps.include_patterns, &ps.exclude_patterns) {
            Ok(m) => m,
            Err(e) => {
                return DoctorCheckResult {
                    name: "File Walk".to_string(),
                    ok: false,
                    details: vec![],
                    errors: vec![e.to_string()],
                    traceback: None,
                };
            }
        };
    let mut total = 0usize;
    let mut by_ext: BTreeMap<String, usize> = BTreeMap::new();
    if let Ok(items) = cocoindex::walk_dir(root_path).recursive(true).path_matcher(matcher).items() {
        for (key, _) in items {
            total += 1;
            let ext = std::path::Path::new(&key)
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy()))
                .unwrap_or_else(|| "(no ext)".to_string());
            *by_ext.entry(ext).or_default() += 1;
        }
    }
    let mut details = vec![format!("Total matched files: {total}")];
    let mut counts: Vec<(&String, &usize)> = by_ext.iter().collect();
    counts.sort_by(|a, b| b.1.cmp(a.1));
    for (ext, count) in counts {
        details.push(format!("  {ext}: {count}"));
    }
    DoctorCheckResult { name: "File Walk".to_string(), ok: true, details, errors: vec![], traceback: None }
}

async fn check_index_status(root: &str) -> DoctorCheckResult {
    let db_path = target_sqlite_db_path(&PathBuf::from(root));
    let mut details = vec![format!("Index: {}", db_path.display())];
    if !db_path.exists() {
        details.push("Index not created yet.".to_string());
        return DoctorCheckResult {
            name: "Index Status".to_string(),
            ok: true,
            details,
            errors: vec![],
            traceback: None,
        };
    }
    match index_counts(&db_path).await {
        Ok((chunks, files, langs)) => {
            details.push(format!("Chunks: {chunks}"));
            details.push(format!("Files: {files}"));
            for (lang, cnt) in &langs {
                details.push(format!("  {lang}: {cnt} chunks"));
            }
            DoctorCheckResult { name: "Index Status".to_string(), ok: true, details, errors: vec![], traceback: None }
        }
        Err(e) => DoctorCheckResult {
            name: "Index Status".to_string(),
            ok: false,
            details,
            errors: vec![e.to_string()],
            traceback: None,
        },
    }
}

// ---------------------------------------------------------------------------
// Daemon entry point
// ---------------------------------------------------------------------------

/// Build the registry from settings (or no-settings mode).
async fn build_registry() -> (ProjectRegistry, Vec<String>, Vec<String>) {
    let mut warnings = Vec::new();
    let mut settings_env_names = Vec::new();
    let mut embedder_error: Option<String> = None;
    if user_settings_path().is_file() {
        match load_user_settings() {
            Ok(user) => {
                settings_env_names = user.envs.keys().cloned().collect();
                for (k, v) in &user.envs {
                    // SAFETY: single-threaded startup before tasks spawn.
                    unsafe { std::env::set_var(k, v) };
                }
                match resolve_embedder_params(&user.embedding) {
                    Ok(params) => {
                        if params.used_backward_compat {
                            warnings.push(backward_compat_warning(&user));
                        }
                        match create_embedder(&user.embedding, &params.indexing).await {
                            Ok(embedder) => {
                                return (
                                    ProjectRegistry {
                                        embedder: Some(embedder),
                                        embedder_error: None,
                                        indexing_params: params.indexing,
                                        query_params: params.query,
                                        projects: Mutex::new(HashMap::new()),
                                    },
                                    settings_env_names,
                                    warnings,
                                );
                            }
                            Err(e) => {
                                eprintln!("Failed to create embedder: {e}");
                                embedder_error = Some(e.to_string());
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("Invalid embedder params: {e}");
                        embedder_error = Some(e.to_string());
                    }
                }
            }
            Err(e) => {
                eprintln!("Failed to load user settings: {e}");
                embedder_error = Some(e.to_string());
            }
        }
    }
    (
        ProjectRegistry {
            embedder: None,
            embedder_error,
            indexing_params: Params::new(),
            query_params: Params::new(),
            projects: Mutex::new(HashMap::new()),
        },
        settings_env_names,
        warnings,
    )
}

fn backward_compat_warning(user: &UserSettings) -> String {
    format!(
        "Your embedding model ({}) was previously hardcoded to use prompt_name=\"query\" for \
         queries. Add `query_params: {{prompt_name: query}}` under `embedding:` in {} to silence \
         this warning.",
        user.embedding.model,
        user_settings_path().display()
    )
}

/// Daemon main (blocking until shutdown). Entry point for `ccc run-daemon`.
pub async fn run_daemon() -> Result<()> {
    std::fs::create_dir_all(daemon_runtime_dir())?;

    // Bind before the expensive registry build and before claiming the PID file,
    // so a duplicate start bails fast and never clobbers a healthy daemon's
    // socket or PID file.
    let sock_path = daemon_socket_path();
    let listener = match UnixListener::bind(&sock_path) {
        Ok(l) => l,
        Err(e) if e.kind() == std::io::ErrorKind::AddrInUse => {
            // A socket file is present. If a daemon is actually listening, leave
            // it alone; if the connect is refused the socket is stale — unlink
            // and rebind.
            if UnixStream::connect(&sock_path).await.is_ok() {
                bail!(
                    "another daemon is already listening on {}",
                    sock_path.display()
                );
            }
            let _ = std::fs::remove_file(&sock_path);
            UnixListener::bind(&sock_path)?
        }
        Err(e) => return Err(e.into()),
    };

    let pid_path = daemon_pid_path();
    std::fs::write(&pid_path, std::process::id().to_string())?;

    let settings_mtime_us = global_settings_mtime_us();
    let (registry, settings_env_names, handshake_warnings) = build_registry().await;
    eprintln!("Daemon listening on {} (v{VERSION})", sock_path.display());

    let state = Arc::new(DaemonState {
        registry,
        start_time: std::time::Instant::now(),
        settings_mtime_us,
        settings_env_names,
        handshake_warnings,
        shutdown: Notify::new(),
    });

    // Signal handling.
    let sig_state = state.clone();
    tokio::spawn(async move {
        let mut term =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).unwrap();
        let mut int =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt()).unwrap();
        tokio::select! {
            _ = term.recv() => {}
            _ = int.recv() => {}
        }
        sig_state.shutdown.notify_one();
    });

    // Accept loop. Connection tasks are tracked so in-flight requests (a Stop
    // reply, an index write) can drain on shutdown rather than being aborted.
    let mut conns = tokio::task::JoinSet::new();
    loop {
        tokio::select! {
            _ = state.shutdown.notified() => break,
            accepted = listener.accept() => {
                if let Ok((stream, _)) = accepted {
                    let st = state.clone();
                    conns.spawn(async move { handle_connection(stream, st).await });
                }
            }
        }
        while conns.try_join_next().is_some() {}
    }

    // Drain in-flight connections with a grace period before tearing down.
    let _ = tokio::time::timeout(std::time::Duration::from_secs(10), async {
        while conns.join_next().await.is_some() {}
    })
    .await;

    // Cleanup.
    let _ = std::fs::remove_file(&sock_path);
    if let Ok(stored) = std::fs::read_to_string(&pid_path) {
        if stored.trim() == std::process::id().to_string() {
            let _ = std::fs::remove_file(&pid_path);
        }
    }
    eprintln!("Daemon stopped");
    Ok(())
}
