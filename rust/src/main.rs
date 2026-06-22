//! `ccc` — Rust port of cocoindex-code. Daemon-backed CLI.

mod client;
mod daemon;
mod daemon_paths;
mod db;
mod embedder;
mod embedder_params;
mod indexer;
mod mcp;
mod project;
mod protocol;
mod query;
mod schema;
mod settings;
mod walk;

use std::path::{Path, PathBuf};

use anyhow::{Result, bail};
use clap::{Parser, Subcommand};

use crate::client::{ProjectStatus, SearchOutcome};

#[derive(Parser)]
#[command(name = "ccc", about = "CocoIndex Code — index and search codebases (Rust port).")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Initialize a project for cocoindex-code.
    Init {
        path: Option<PathBuf>,
        /// Local sentence-transformers model to use (fastembed-supported).
        #[arg(long)]
        model: Option<String>,
        #[arg(short = 'f', long)]
        force: bool,
    },
    /// Create/update the index for the codebase.
    Index,
    /// Semantic search across the codebase.
    Search {
        query: Vec<String>,
        #[arg(long = "lang")]
        lang: Vec<String>,
        #[arg(long = "path")]
        path: Option<String>,
        #[arg(long, default_value_t = 0)]
        offset: i64,
        #[arg(long, default_value_t = 10)]
        limit: i64,
        #[arg(long)]
        refresh: bool,
    },
    /// Show project status.
    Status,
    /// Reset project databases and optionally settings.
    Reset {
        #[arg(long = "all")]
        all: bool,
        #[arg(short = 'f', long)]
        force: bool,
    },
    /// Check system health and report issues.
    Doctor {
        #[arg(short = 'v', long)]
        verbose: bool,
    },
    /// Run as an MCP server (stdio).
    Mcp,
    /// Manage the daemon process.
    Daemon {
        #[command(subcommand)]
        cmd: DaemonCmd,
    },
    /// Internal: run the daemon process.
    #[command(name = "run-daemon", hide = true)]
    RunDaemon,
}

#[derive(Subcommand)]
enum DaemonCmd {
    /// Show daemon status.
    Status,
    /// Restart the daemon.
    Restart,
    /// Stop the daemon.
    Stop,
}

fn cwd() -> Result<PathBuf> {
    Ok(std::env::current_dir()?)
}

/// Find the project root, requiring global + project settings (ports
/// `require_project_root`).
fn require_project_root() -> Result<PathBuf> {
    if !settings::user_settings_path().is_file() {
        bail!(
            "Global settings not found: {}\nRun `ccc init` to create it with default settings.",
            settings::user_settings_path().display()
        );
    }
    settings::find_project_root(&cwd()?).ok_or_else(|| {
        anyhow::anyhow!(
            "Not in an initialized project directory.\nRun `ccc init` in your project root to get started."
        )
    })
}

fn resolve_default_path(project_root: &Path) -> Option<String> {
    let cwd = cwd().ok()?.canonicalize().ok()?;
    let rel = cwd.strip_prefix(project_root).ok()?;
    if rel.as_os_str().is_empty() {
        return None;
    }
    // Force POSIX separators (mirrors Python's `as_posix()`): stored `file_path`
    // values are POSIX, so a Windows `\` here would never match the SQL GLOB.
    let rel_posix = rel
        .components()
        .map(|c| c.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/");
    Some(format!("{rel_posix}/*"))
}

fn print_index_stats(s: &ProjectStatus) {
    if s.indexing {
        println!("Indexing in progress...");
    }
    if !s.index_exists {
        println!("\nIndex not created yet.");
        return;
    }
    println!("\nIndex stats:");
    println!("  Chunks: {}", s.total_chunks);
    println!("  Files:  {}", s.total_files);
    if !s.languages.is_empty() {
        println!("  Languages:");
        let mut langs: Vec<_> = s.languages.iter().collect();
        langs.sort_by(|a, b| b.1.cmp(a.1));
        for (lang, count) in langs {
            println!("    {lang}: {count} chunks");
        }
    }
}

fn print_search_results(outcome: &SearchOutcome) {
    if !outcome.success {
        eprintln!("Search failed: {}", outcome.message.clone().unwrap_or_default());
        return;
    }
    if outcome.results.is_empty() {
        println!("No results found.");
        return;
    }
    for (i, r) in outcome.results.iter().enumerate() {
        println!("\n--- Result {} (score: {:.3}) ---", i + 1, r.score);
        println!("File: {}:{}-{} [{}]", r.file_path, r.start_line, r.end_line, r.language);
        println!("{}", r.content);
    }
}

const GITIGNORE_COMMENT: &str = "# CocoIndex Code (ccc)";
const GITIGNORE_ENTRY: &str = "/.cocoindex_code/";

fn add_to_gitignore(project_root: &Path) {
    if !project_root.join(".git").is_dir() {
        return;
    }
    let gitignore = project_root.join(".gitignore");
    if gitignore.is_file() {
        let mut content = std::fs::read_to_string(&gitignore).unwrap_or_default();
        if content.lines().any(|l| l == GITIGNORE_ENTRY) {
            return;
        }
        if !content.is_empty() && !content.ends_with('\n') {
            content.push('\n');
        }
        content.push_str(&format!("{GITIGNORE_COMMENT}\n{GITIGNORE_ENTRY}\n"));
        let _ = std::fs::write(&gitignore, content);
    } else {
        let _ = std::fs::write(&gitignore, format!("{GITIGNORE_COMMENT}\n{GITIGNORE_ENTRY}\n"));
    }
}

fn remove_from_gitignore(project_root: &Path) {
    let gitignore = project_root.join(".gitignore");
    let Ok(content) = std::fs::read_to_string(&gitignore) else {
        return;
    };
    let mut out: Vec<&str> = Vec::new();
    for line in content.lines() {
        if line == GITIGNORE_ENTRY {
            // Drop a preceding matching comment line too.
            if out.last().map(|l| *l == GITIGNORE_COMMENT).unwrap_or(false) {
                out.pop();
            }
            continue;
        }
        out.push(line);
    }
    let mut new_content = out.join("\n");
    if content.ends_with('\n') && !new_content.is_empty() {
        new_content.push('\n');
    }
    let _ = std::fs::write(&gitignore, new_content);
}

#[tokio::main]
async fn main() {
    if let Err(e) = run().await {
        eprintln!("Error: {e}");
        std::process::exit(1);
    }
}

async fn run() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .with_writer(std::io::stderr)
        .init();

    let cli = Cli::parse();
    match cli.command {
        Command::RunDaemon => daemon::run_daemon().await?,

        Command::Init { path, model, force } => {
            let root = match path {
                Some(p) => p,
                None => cwd()?,
            };
            // Already initialized: still ensure global settings exist, then stop.
            if settings::project_settings_path(&root).is_file() {
                project::init(&root, model)?;
                println!("Project already initialized.");
                return Ok(());
            }
            // Warn if a parent dir is already a project/repo (unless --force).
            if !force {
                if let Some(parent) = settings::find_parent_with_marker(&root) {
                    let root_canon = std::fs::canonicalize(&root).unwrap_or_else(|_| root.clone());
                    if parent != root_canon {
                        bail!(
                            "A parent directory has a project marker: {}\nYou might want to run \
                             `ccc init` there instead.\nUse `ccc init -f` to initialize here anyway.",
                            parent.display()
                        );
                    }
                }
            }
            let written = project::init(&root, model)?;
            add_to_gitignore(&root);
            println!("Created project settings: {}", written.display());
            println!("You can edit the settings files to customize indexing behavior.");
            println!("Run `ccc index` to build the index.");
        }

        Command::Index => {
            let root = require_project_root()?;
            let root_str = root.to_string_lossy().to_string();
            println!("Project: {}", root.display());
            client::index(&root_str, || eprintln!("Another indexing is ongoing, waiting...")).await?;
            let status = client::project_status(&root_str).await?;
            print_index_stats(&status);
        }

        Command::Search { query, lang, path, offset, limit, refresh } => {
            let root = require_project_root()?;
            let root_str = root.to_string_lossy().to_string();
            let query_str = query.join(" ");
            if query_str.trim().is_empty() {
                bail!("usage: ccc search \"your query\" [--lang L] [--path GLOB]");
            }
            if refresh {
                client::index(&root_str, || eprintln!("Waiting for indexing...")).await?;
            }
            let paths = match path {
                Some(p) => Some(vec![p]),
                None => resolve_default_path(&root).map(|p| vec![p]),
            };
            let langs = if lang.is_empty() { None } else { Some(lang) };
            let outcome = client::search(
                &root_str,
                &query_str,
                langs,
                paths,
                limit,
                offset,
                || eprintln!("Waiting for indexing to complete..."),
            )
            .await?;
            print_search_results(&outcome);
        }

        Command::Status => {
            let root = require_project_root()?;
            let root_str = root.to_string_lossy().to_string();
            println!("Project: {}", root.display());
            println!("Settings: {}", settings::project_settings_path(&root).display());
            let db_path = settings::target_sqlite_db_path(&root);
            if db_path.exists() {
                println!("Index DB: {}", db_path.display());
            }
            let status = client::project_status(&root_str).await?;
            print_index_stats(&status);
        }

        Command::Reset { all, force } => reset(all, force).await?,

        Command::Doctor { verbose } => doctor(verbose).await?,

        Command::Mcp => {
            let root = require_project_root()?;
            mcp::serve(root.to_string_lossy().to_string()).await?;
        }

        Command::Daemon { cmd } => match cmd {
            DaemonCmd::Status => {
                let s = client::daemon_status().await?;
                println!("Daemon version: {}", s.version);
                println!("Uptime: {:.1}s", s.uptime_seconds);
                if s.projects.is_empty() {
                    println!("No projects loaded.");
                } else {
                    println!("Projects:");
                    for p in &s.projects {
                        let state = if p.indexing { "indexing" } else { "idle" };
                        println!("  {} [{state}]", p.project_root);
                    }
                }
            }
            DaemonCmd::Restart => {
                println!("Stopping daemon...");
                client::stop_daemon().await;
                println!("Starting daemon...");
                client::start_and_wait().await?;
                println!("Daemon restarted.");
            }
            DaemonCmd::Stop => {
                if !daemon_paths::daemon_pid_path().exists() && !client::is_daemon_running() {
                    println!("Daemon is not running.");
                } else if client::stop_and_report().await {
                    println!("Daemon stopped.");
                } else {
                    eprintln!("Warning: daemon may not have stopped cleanly.");
                }
            }
        },
    }
    Ok(())
}

async fn reset(all: bool, force: bool) -> Result<()> {
    let root = require_project_root()?;
    let cocoindex_dir = root.join(".cocoindex_code");
    let db_files = [settings::cocoindex_db_path(&root), settings::target_sqlite_db_path(&root)];
    let settings_file = settings::project_settings_path(&root);

    let mut to_delete: Vec<PathBuf> = db_files.iter().filter(|f| f.exists()).cloned().collect();
    if all && settings_file.exists() {
        to_delete.push(settings_file.clone());
    }
    if to_delete.is_empty() && !all {
        println!("Nothing to reset.");
        return Ok(());
    }
    if !to_delete.is_empty() {
        println!("The following files will be deleted:");
        for f in &to_delete {
            println!("  {}", f.display());
        }
    }
    if !force {
        eprint!("Proceed? [y/N] ");
        use std::io::Write;
        std::io::stderr().flush().ok();
        let mut answer = String::new();
        std::io::stdin().read_line(&mut answer)?;
        if !matches!(answer.trim().to_lowercase().as_str(), "y" | "yes") {
            println!("Aborted.");
            return Ok(());
        }
    }

    // Release file handles in the daemon first.
    client::try_remove_project(&root).await;

    for f in &to_delete {
        if f.is_dir() {
            let _ = std::fs::remove_dir_all(f);
        } else {
            let _ = std::fs::remove_file(f);
        }
    }
    if all {
        // remove_dir_all, not remove_dir: SQLite leaves `-wal`/`-shm` sidecars
        // next to the deleted `.db` files, so the directory is rarely empty.
        let _ = std::fs::remove_dir_all(&cocoindex_dir);
        remove_from_gitignore(&root);
        println!("Project fully reset.");
    } else {
        println!("Databases deleted.");
        if settings::project_settings_path(&root).exists() {
            println!(
                "Settings file still exists. Run `ccc reset --all` to remove it too,\nor edit it manually."
            );
        }
    }
    Ok(())
}

async fn doctor(verbose: bool) -> Result<()> {
    use crate::protocol::DoctorCheckResult;

    let print_result = |r: &DoctorCheckResult| {
        if r.name == "done" {
            return;
        }
        let tag = if r.ok { "[OK]" } else { "[FAIL]" };
        println!("\n  {tag} {}", r.name);
        for line in &r.details {
            println!("    {line}");
        }
        for err in &r.errors {
            eprintln!("    ERROR: {err}");
        }
        if let Some(tb) = &r.traceback {
            if verbose {
                for line in tb.lines() {
                    eprintln!("    {line}");
                }
            } else {
                eprintln!("    Run `ccc doctor -v` for the full traceback.");
            }
        }
    };

    // 1. Global settings (local).
    println!("\n  Global Settings\n  {}", "-".repeat(38));
    let gs_path = settings::user_settings_path();
    println!("  Settings: {}", gs_path.display());
    match settings::load_user_settings() {
        Ok(us) => {
            let device = us.embedding.device.as_deref().map(|d| format!(", device={d}")).unwrap_or_default();
            println!("  Embedding: provider={}, model={}{device}", us.embedding.provider, us.embedding.model);
            if !us.envs.is_empty() {
                // BTreeMap keys are already sorted; join plainly (no debug braces).
                let keys: Vec<&str> = us.envs.keys().map(String::as_str).collect();
                println!("  Env vars (from settings): {}", keys.join(", "));
            }
        }
        Err(e) => eprintln!("  ERROR: {e}"),
    }

    // 2. Daemon.
    println!("\n  Daemon\n  {}", "-".repeat(38));
    let mut daemon_ok = false;
    match client::daemon_status().await {
        Ok(s) => {
            println!("  Version: {}", s.version);
            println!("  Uptime: {:.1}s", s.uptime_seconds);
            println!("  Loaded projects: {}", s.projects.len());
            daemon_ok = true;
        }
        Err(e) => {
            eprintln!("  ERROR: Cannot connect to daemon: {e}");
            println!("  Remaining daemon-side checks will be skipped.");
        }
    }

    // 3. Daemon environment.
    if daemon_ok {
        match client::daemon_env().await {
            Ok(env) => {
                let settings_keys: std::collections::HashSet<&String> =
                    env.settings_env_names.iter().collect();
                let others: Vec<&String> =
                    env.env_names.iter().filter(|k| !settings_keys.contains(k)).collect();
                if !others.is_empty() {
                    let names: Vec<&str> = others.iter().map(|s| s.as_str()).collect();
                    println!("  Other env vars in daemon: {}", names.join(", "));
                }
            }
            Err(e) => eprintln!("  ERROR: Failed to get daemon env: {e}"),
        }
    }

    // 4. Model check (daemon-side, global).
    if daemon_ok {
        match client::doctor(None).await {
            Ok(results) => results.iter().for_each(&print_result),
            Err(e) => eprintln!("  ERROR: Model check failed: {e}"),
        }
    }

    // 4. Project settings + project checks.
    if let Some(root) = settings::find_project_root(&cwd()?) {
        println!("\n  Project Settings\n  {}", "-".repeat(38));
        let ps_path = settings::project_settings_path(&root);
        println!("  Settings: {}", ps_path.display());
        match settings::load_project_settings(&root) {
            Ok(ps) => {
                println!("  Include patterns ({}):", ps.include_patterns.len());
                println!("    {}", ps.include_patterns.join(", "));
                println!("  Exclude patterns ({}):", ps.exclude_patterns.len());
                println!("    {}", ps.exclude_patterns.join(", "));
                if !ps.language_overrides.is_empty() {
                    println!("  Language overrides:");
                    for lo in &ps.language_overrides {
                        println!("    .{} -> {}", lo.ext, lo.lang);
                    }
                }
            }
            Err(e) => eprintln!("  ERROR: {e}"),
        }
        if daemon_ok {
            match client::doctor(Some(&root.to_string_lossy())).await {
                Ok(results) => results.iter().for_each(&print_result),
                Err(e) => eprintln!("  ERROR: Project checks failed: {e}"),
            }
        }
    }

    // 6. Logs.
    println!("\n  Log Files\n  {}", "-".repeat(38));
    println!("  Daemon logs: {}", daemon_paths::daemon_log_path().display());
    println!("  Check logs above for further troubleshooting.");
    Ok(())
}
