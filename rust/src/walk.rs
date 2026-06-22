//! File-path matching: include/exclude patterns + `.gitignore` awareness.
//! Ports the matcher composition in `indexer.py` (`GitignoreAwareMatcher` over
//! `PatternFilePathMatcher`).
//!
//! Include/exclude use the SDK's `PatternFilePathMatcher` — the same matcher
//! Python uses — so the pattern semantics match exactly. `.gitignore` filtering
//! is layered on top, honoring **nested per-directory** `.gitignore` files
//! (matching Python's per-directory spec stacking): for any path, each
//! `.gitignore` from the project root down to the path's directory is consulted,
//! deepest-first, so a deeper rule overrides a shallower one. Combined with
//! directory pruning (`is_dir_included`), this matches git's rule that a file
//! under an excluded directory cannot be re-included.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use anyhow::Result;
use cocoindex::{FilePathMatcher, PatternFilePathMatcher};
use ignore::Match;
use ignore::gitignore::{Gitignore, GitignoreBuilder};

pub struct GitignoreAwareMatcher {
    base: PatternFilePathMatcher,
    root: PathBuf,
    /// Per-directory (absolute) `.gitignore`, built lazily. `None` value = the
    /// directory has no `.gitignore`.
    cache: Mutex<HashMap<PathBuf, Option<Arc<Gitignore>>>>,
}

impl GitignoreAwareMatcher {
    pub fn new(root: &Path, include: &[String], exclude: &[String]) -> Result<Self> {
        let base = PatternFilePathMatcher::new(
            include.iter().map(String::as_str),
            exclude.iter().map(String::as_str),
        )
        .map_err(|e| anyhow::anyhow!("invalid include/exclude pattern: {e}"))?;
        let root = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
        Ok(Self { base, root, cache: Mutex::new(HashMap::new()) })
    }

    /// The `.gitignore` declared in `abs_dir` (if any), cached.
    fn gitignore_for_dir(&self, abs_dir: &Path) -> Option<Arc<Gitignore>> {
        let mut cache = self.cache.lock().unwrap();
        if let Some(entry) = cache.get(abs_dir) {
            return entry.clone();
        }
        let gi_path = abs_dir.join(".gitignore");
        let built = if gi_path.is_file() {
            let mut builder = GitignoreBuilder::new(abs_dir);
            builder.add(&gi_path);
            builder.build().ok().map(Arc::new)
        } else {
            None
        };
        cache.insert(abs_dir.to_path_buf(), built.clone());
        built
    }

    /// True if `rel_path` is ignored by any `.gitignore` from the project root
    /// down to its directory (deepest wins).
    fn ignored(&self, rel_path: &Path, is_dir: bool) -> bool {
        let abs = self.root.join(rel_path);
        let mut dir = if is_dir {
            abs.clone()
        } else {
            abs.parent().map(Path::to_path_buf).unwrap_or_else(|| self.root.clone())
        };
        loop {
            if let Some(gi) = self.gitignore_for_dir(&dir) {
                match gi.matched_path_or_any_parents(&abs, is_dir) {
                    Match::Ignore(_) => return true,
                    Match::Whitelist(_) => return false,
                    Match::None => {}
                }
            }
            if dir == self.root {
                break;
            }
            match dir.parent() {
                Some(parent) if parent.starts_with(&self.root) || parent == self.root => {
                    dir = parent.to_path_buf();
                }
                _ => break,
            }
        }
        false
    }
}

impl FilePathMatcher for GitignoreAwareMatcher {
    fn is_dir_included(&self, path: &Path) -> bool {
        !self.ignored(path, true) && self.base.is_dir_included(path)
    }
    fn is_file_included(&self, path: &Path) -> bool {
        !self.ignored(path, false) && self.base.is_file_included(path)
    }
}
