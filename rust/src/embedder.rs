//! Embedder backend. Ports `shared.create_embedder`.
//!
//! Only **local sentence-transformers** (fastembed) is supported right now.
//! The Python tool also offers a `litellm` provider for cloud/multi-provider
//! embeddings; there is no in-process Rust equivalent (the community
//! `litellm-rust` crate is alpha and only covers OpenAI-compatible endpoints),
//! so that option is intentionally not exposed yet. `create_embedder` parses
//! existing `provider: litellm` configs without panicking and returns a clear
//! error pointing users at the local provider — keeping settings files
//! backward compatible.

use anyhow::{Result, anyhow, bail};

use crate::embedder_params::Params;
use crate::settings::EmbeddingSettings;

/// Legacy model-name prefix (`sbert/…`) stripped before loading, matching the
/// Python embedder. Kept for backward compatibility with older configs.
const SBERT_PREFIX: &str = "sbert/";

/// The embedding backend. Currently always a local fastembed model.
#[derive(Clone)]
pub struct CodeEmbedder {
    inner: cocoindex::ops::sentence_transformers::SentenceTransformerEmbedder,
}

impl CodeEmbedder {
    /// Stable identity for change detection (parity for Python's
    /// `ContextKey(..., detect_change=True)` keyed on the embedder).
    pub fn state_key(&self) -> String {
        format!("sentence-transformers:{}", self.inner.model_name())
    }

    pub async fn embed_batch(&self, texts: Vec<String>, _params: &Params) -> Result<Vec<Vec<f32>>> {
        // NOTE: `prompt_name` (query vs passage) is not yet threaded through the
        // SDK embedder; tracked as a parity follow-up.
        self.inner
            .embed_batch(texts)
            .await
            .map_err(|e| anyhow!("local embed failed: {e}"))
    }

    pub async fn embed(&self, text: &str, params: &Params) -> Result<Vec<f32>> {
        let mut out = self.embed_batch(vec![text.to_string()], params).await?;
        out.pop().ok_or_else(|| anyhow!("embedder returned no vectors"))
    }

    /// The embedding dimension (exact, from the loaded model).
    pub async fn dimension(&self) -> Result<usize> {
        Ok(self.inner.dimension())
    }
}

/// Build an embedder from settings. Only `provider: sentence-transformers`
/// (local fastembed) is supported; any other provider is rejected with a clear
/// message rather than silently failing.
pub async fn create_embedder(
    settings: &EmbeddingSettings,
    _indexing_params: &Params,
) -> Result<CodeEmbedder> {
    if settings.provider != "sentence-transformers" {
        bail!(
            "Only local 'sentence-transformers' (fastembed) embeddings are supported in the Rust \
             port right now — provider '{}' is not available. Set `provider: sentence-transformers` \
             in {} and choose a fastembed-supported model.",
            settings.provider,
            crate::settings::user_settings_path().display()
        );
    }

    let mut model = settings.model.clone();
    if let Some(stripped) = model.strip_prefix(SBERT_PREFIX) {
        model = stripped.to_string();
    }
    let inner = cocoindex::ops::sentence_transformers::SentenceTransformerEmbedder::load(&model)
        .await
        .map_err(|e| anyhow!("loading sentence-transformers model {model:?}: {e}"))?;
    Ok(CodeEmbedder { inner })
}
