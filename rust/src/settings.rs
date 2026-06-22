//! YAML settings schema, loading, saving, and path helpers. Ports
//! `settings.py`.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Default file patterns
// ---------------------------------------------------------------------------

pub const DEFAULT_INCLUDED_PATTERNS: &[&str] = &[
    "**/*.py", "**/*.pyi", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.mjs", "**/*.cjs",
    "**/*.rs", "**/*.go", "**/*.java", "**/*.c", "**/*.h", "**/*.cpp", "**/*.hpp", "**/*.cc",
    "**/*.cxx", "**/*.hxx", "**/*.hh", "**/*.cs", "**/*.sql", "**/*.sh", "**/*.bash", "**/*.zsh",
    "**/*.md", "**/*.mdx", "**/*.txt", "**/*.rst", "**/*.php", "**/*.lua", "**/*.rb", "**/*.swift",
    "**/*.kt", "**/*.kts", "**/*.scala", "**/*.r", "**/*.html", "**/*.htm", "**/*.svelte",
    "**/*.vue", "**/*.css", "**/*.scss", "**/*.json", "**/*.xml", "**/*.yaml", "**/*.yml",
    "**/*.toml", "**/*.sol", "**/*.pas", "**/*.dpr", "**/*.dtd", "**/*.f", "**/*.f90", "**/*.f95",
    "**/*.f03",
];

pub const DEFAULT_EXCLUDED_PATTERNS: &[&str] = &[
    "**/.*",
    "**/__pycache__",
    "**/node_modules",
    "**/target",
    "**/build/assets",
    "**/dist",
    "**/vendor/*.*/*",
    "**/vendor/*",
    "**/.cocoindex_code",
];

// Python defaults to `Snowflake/snowflake-arctic-embed-xs`, but fastembed's
// model registry does not include it. We default to a small, high-quality
// retrieval model that fastembed ships (resolved by suffix to
// `Xenova/bge-small-en-v1.5`).
pub const DEFAULT_ST_MODEL: &str = "BAAI/bge-small-en-v1.5";

// ---------------------------------------------------------------------------
// Dataclasses
// ---------------------------------------------------------------------------

fn default_provider() -> String {
    "litellm".to_string()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EmbeddingSettings {
    pub model: String,
    #[serde(default = "default_provider")]
    pub provider: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub device: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_interval_ms: Option<i64>,
    /// Outer `None` = key absent; `Some(None)` = key present but null;
    /// `Some(Some(map))` = key present with a value. This three-state encoding
    /// mirrors Python's `None` (absent) vs `{}` (present-but-empty/null), which
    /// the legacy-bridge opt-out depends on. See [`resolve_embedder_params`].
    #[serde(default, deserialize_with = "double_option", skip_serializing_if = "Option::is_none")]
    pub indexing_params: Option<Option<serde_json::Map<String, serde_json::Value>>>,
    #[serde(default, deserialize_with = "double_option", skip_serializing_if = "Option::is_none")]
    pub query_params: Option<Option<serde_json::Map<String, serde_json::Value>>>,
}

/// Deserialize so a *present* key (even `null`) becomes `Some(...)`, while an
/// *absent* key stays `None` (via `#[serde(default)]`). Standard serde collapses
/// a present-null into `None`; this preserves the distinction.
fn double_option<'de, D, T>(de: D) -> std::result::Result<Option<Option<T>>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::Deserialize<'de>,
{
    serde::Deserialize::deserialize(de).map(Some)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UserSettings {
    pub embedding: EmbeddingSettings,
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub envs: std::collections::BTreeMap<String, String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LanguageOverride {
    /// Extension without the dot, e.g. "inc".
    pub ext: String,
    /// Language name, e.g. "php".
    pub lang: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ChunkerMapping {
    pub ext: String,
    /// "module.path:callable" — retained for config compatibility. Custom
    /// Python chunkers are not loadable from Rust; see `chunking.rs`.
    pub module: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProjectSettings {
    #[serde(default = "default_included")]
    pub include_patterns: Vec<String>,
    #[serde(default = "default_excluded")]
    pub exclude_patterns: Vec<String>,
    #[serde(default)]
    pub language_overrides: Vec<LanguageOverride>,
    #[serde(default)]
    pub chunkers: Vec<ChunkerMapping>,
}

fn default_included() -> Vec<String> {
    DEFAULT_INCLUDED_PATTERNS.iter().map(|s| s.to_string()).collect()
}
fn default_excluded() -> Vec<String> {
    DEFAULT_EXCLUDED_PATTERNS.iter().map(|s| s.to_string()).collect()
}

impl Default for ProjectSettings {
    fn default() -> Self {
        Self {
            include_patterns: default_included(),
            exclude_patterns: default_excluded(),
            language_overrides: Vec::new(),
            chunkers: Vec::new(),
        }
    }
}

pub fn default_user_settings() -> UserSettings {
    UserSettings {
        embedding: EmbeddingSettings {
            provider: "sentence-transformers".to_string(),
            model: DEFAULT_ST_MODEL.to_string(),
            device: None,
            min_interval_ms: None,
            indexing_params: None,
            query_params: None,
        },
        envs: Default::default(),
    }
}

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

const SETTINGS_DIR_NAME: &str = ".cocoindex_code";
const SETTINGS_FILE_NAME: &str = "settings.yml";
const USER_SETTINGS_FILE_NAME: &str = "global_settings.yml";
const TARGET_SQLITE_DB_NAME: &str = "target_sqlite.db";
const COCOINDEX_DB_NAME: &str = "cocoindex.db";

/// Directory for database files. Honors `COCOINDEX_CODE_DB_PATH_MAPPING` in the
/// Python version; that mapping is deferred to Phase 2 (daemon/container).
pub fn resolve_db_dir(project_root: &Path) -> PathBuf {
    project_root.join(SETTINGS_DIR_NAME)
}

pub fn target_sqlite_db_path(project_root: &Path) -> PathBuf {
    resolve_db_dir(project_root).join(TARGET_SQLITE_DB_NAME)
}

pub fn cocoindex_db_path(project_root: &Path) -> PathBuf {
    resolve_db_dir(project_root).join(COCOINDEX_DB_NAME)
}

pub fn user_settings_dir() -> PathBuf {
    if let Ok(override_dir) = std::env::var("COCOINDEX_CODE_DIR") {
        return PathBuf::from(override_dir);
    }
    dirs::home_dir()
        .map(|h| h.join(SETTINGS_DIR_NAME))
        .unwrap_or_else(|| PathBuf::from(SETTINGS_DIR_NAME))
}

pub fn user_settings_path() -> PathBuf {
    user_settings_dir().join(USER_SETTINGS_FILE_NAME)
}

pub fn project_settings_path(project_root: &Path) -> PathBuf {
    project_root.join(SETTINGS_DIR_NAME).join(SETTINGS_FILE_NAME)
}

/// Walk up from `start` looking for an initialized project (`settings.yml`) or
/// a git repo (`.git/`), stopping at (and excluding) the home directory. Ports
/// `find_parent_with_marker`. Used by `ccc init` to warn before initializing
/// inside an existing project/repo.
pub fn find_parent_with_marker(start: &Path) -> Option<PathBuf> {
    let home = dirs::home_dir().and_then(|h| std::fs::canonicalize(&h).ok());
    let mut current =
        std::fs::canonicalize(start).unwrap_or_else(|_| start.to_path_buf());
    loop {
        if Some(&current) == home.as_ref() {
            return None;
        }
        // Match Python's order: stop at the filesystem root *before* testing the
        // marker, so a marker at `/` is never returned.
        let Some(parent) = current.parent().filter(|p| *p != current) else {
            return None;
        };
        if current.join(SETTINGS_DIR_NAME).join(SETTINGS_FILE_NAME).is_file()
            || current.join(".git").is_dir()
        {
            return Some(current);
        }
        current = parent.to_path_buf();
    }
}

/// mtime of `global_settings.yml` in integer microseconds, or `None` if absent.
/// The daemon records this at startup; the client compares to detect staleness.
pub fn global_settings_mtime_us() -> Option<i64> {
    let meta = std::fs::metadata(user_settings_path()).ok()?;
    let mtime = meta.modified().ok()?;
    let dur = mtime.duration_since(std::time::UNIX_EPOCH).ok()?;
    Some(dur.as_micros() as i64)
}

/// Walk up from `start` looking for `.cocoindex_code/settings.yml`.
pub fn find_project_root(start: &Path) -> Option<PathBuf> {
    // Absolutize like Python's `start.resolve()` so the upward walk reaches the
    // filesystem root even when `start` is relative or doesn't exist.
    let mut current = std::fs::canonicalize(start).unwrap_or_else(|_| {
        if start.is_absolute() {
            start.to_path_buf()
        } else {
            std::env::current_dir()
                .map(|c| c.join(start))
                .unwrap_or_else(|_| start.to_path_buf())
        }
    });
    loop {
        if current.join(SETTINGS_DIR_NAME).join(SETTINGS_FILE_NAME).is_file() {
            return Some(current);
        }
        match current.parent() {
            Some(parent) if parent != current => current = parent.to_path_buf(),
            _ => return None,
        }
    }
}

// ---------------------------------------------------------------------------
// I/O
// ---------------------------------------------------------------------------

pub fn load_user_settings() -> Result<UserSettings> {
    let path = user_settings_path();
    if !path.is_file() {
        bail!("User settings not found: {}", path.display());
    }
    let text = std::fs::read_to_string(&path)
        .with_context(|| format!("reading {}", path.display()))?;
    if text.trim().is_empty() {
        bail!("Error loading {}: File is empty", path.display());
    }
    let settings: UserSettings = serde_yaml::from_str(&text)
        .with_context(|| format!("parsing {}", path.display()))?;
    Ok(settings)
}

pub fn save_user_settings(settings: &UserSettings) -> Result<PathBuf> {
    let path = user_settings_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let yaml = serde_yaml::to_string(settings)?;
    std::fs::write(&path, yaml)?;
    Ok(path)
}

pub fn load_project_settings(project_root: &Path) -> Result<ProjectSettings> {
    let path = project_settings_path(project_root);
    if !path.is_file() {
        bail!("Project settings not found: {}", path.display());
    }
    let text = std::fs::read_to_string(&path)?;
    if text.trim().is_empty() {
        return Ok(ProjectSettings::default());
    }
    let settings: ProjectSettings = serde_yaml::from_str(&text)
        .with_context(|| format!("parsing {}", path.display()))?;
    Ok(settings)
}

pub fn save_project_settings(project_root: &Path, settings: &ProjectSettings) -> Result<PathBuf> {
    let path = project_settings_path(project_root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let yaml = serde_yaml::to_string(settings)?;
    std::fs::write(&path, yaml)?;
    Ok(path)
}
