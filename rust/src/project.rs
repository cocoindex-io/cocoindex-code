//! Project initialization. Ports the non-interactive core of `project.py` /
//! `ccc init`.

use std::path::{Path, PathBuf};

use anyhow::Result;

use crate::embedder_params::lookup_defaults;
use crate::settings::{
    DEFAULT_ST_MODEL, EmbeddingSettings, ProjectSettings, UserSettings, default_user_settings,
    load_user_settings, project_settings_path, save_project_settings, save_user_settings,
    user_settings_path,
};

/// Initialize a project at `root`: create `.cocoindex_code/settings.yml` with
/// defaults, and `~/.cocoindex_code/global_settings.yml` if it doesn't exist.
///
/// Only local sentence-transformers (fastembed) is supported, so the global
/// settings are written with `provider: sentence-transformers` and the given
/// (or default) model. Curated `indexing_params`/`query_params` are applied
/// when the model is known.
pub fn init(root: &Path, model: Option<String>) -> Result<PathBuf> {
    let settings_dir = root.join(".cocoindex_code");
    std::fs::create_dir_all(&settings_dir)?;

    // Project settings (defaults) — write only if absent so we don't clobber.
    let proj_path = project_settings_path(root);
    if !proj_path.is_file() {
        save_project_settings(root, &ProjectSettings::default())?;
    }

    // Global (user) settings — write only on first-time setup.
    let user_path = user_settings_path();
    if !user_path.is_file() {
        let mut us: UserSettings = default_user_settings();
        us.embedding.provider = "sentence-transformers".to_string();
        us.embedding.model = model.unwrap_or_else(|| DEFAULT_ST_MODEL.to_string());
        apply_curated_defaults(&mut us.embedding);
        save_user_settings(&us)?;
    } else {
        // Validate it loads.
        load_user_settings()?;
    }

    Ok(proj_path)
}

fn apply_curated_defaults(emb: &mut EmbeddingSettings) {
    if emb.indexing_params.is_some() || emb.query_params.is_some() {
        return;
    }
    if let Some((idx, qry)) = lookup_defaults(&emb.provider, &emb.model) {
        if !idx.is_empty() {
            emb.indexing_params = Some(Some(idx));
        }
        if !qry.is_empty() {
            emb.query_params = Some(Some(qry));
        }
    }
}
