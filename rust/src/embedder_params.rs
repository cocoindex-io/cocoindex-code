//! Validation/resolution of embedder `indexing_params` / `query_params`
//! (`embedder_params.py`) and the curated defaults table consulted by
//! `ccc init` (`embedder_defaults.py`).

use std::collections::BTreeSet;

use anyhow::{Result, bail};
use regex::Regex;
use serde_json::{Map, Value};

use crate::settings::EmbeddingSettings;

pub type Params = Map<String, Value>;

/// Models previously hardcoded as needing a query prompt. The legacy bridge
/// fills `query = {"prompt_name": "query"}` for these when the user set neither
/// param side.
pub const LEGACY_QUERY_PROMPT_MODELS: &[&str] =
    &["nomic-ai/nomic-embed-code", "nomic-ai/CodeRankEmbed"];

/// Accepted kwargs per provider (intentionally minimal, per-side meaningful).
fn accepted_kwargs_for(provider: &str) -> Result<&'static [&'static str]> {
    match provider {
        "sentence-transformers" => Ok(&["prompt_name"]),
        "litellm" => Ok(&["input_type"]),
        other => bail!("Unknown provider: {other:?}"),
    }
}

pub fn validate_params(
    provider: &str,
    indexing: &Params,
    query: &Params,
) -> Result<()> {
    let accepted: BTreeSet<&str> = accepted_kwargs_for(provider)?.iter().copied().collect();
    for (side, params) in [("indexing_params", indexing), ("query_params", query)] {
        if params.is_empty() {
            continue;
        }
        let mut unknown: Vec<&String> =
            params.keys().filter(|k| !accepted.contains(k.as_str())).collect();
        unknown.sort();
        if !unknown.is_empty() {
            bail!(
                "{side}: unknown key(s) {unknown:?} for provider {provider:?}. Accepted keys: {:?}.",
                accepted
            );
        }
    }
    Ok(())
}

/// Effective params spread into `embed()` at runtime.
pub struct EmbedderParams {
    pub indexing: Params,
    pub query: Params,
    pub used_backward_compat: bool,
}

pub fn resolve_embedder_params(settings: &EmbeddingSettings) -> Result<EmbedderParams> {
    // `flatten()`: absent (None) and present-null (Some(None)) both yield an
    // empty map for the *value*, matching Python's `dict(x or {})`.
    let mut indexing = settings.indexing_params.clone().flatten().unwrap_or_default();
    let mut query = settings.query_params.clone().flatten().unwrap_or_default();
    let mut used_backward_compat = false;

    if settings.indexing_params.is_none()
        && settings.query_params.is_none()
        && settings.provider == "sentence-transformers"
        && LEGACY_QUERY_PROMPT_MODELS.contains(&settings.model.as_str())
    {
        query = Map::new();
        query.insert("prompt_name".to_string(), Value::String("query".to_string()));
        indexing = Map::new();
        used_backward_compat = true;
    }

    validate_params(&settings.provider, &indexing, &query)?;
    Ok(EmbedderParams { indexing, query, used_backward_compat })
}

// ---------------------------------------------------------------------------
// Curated defaults (ccc init only)
// ---------------------------------------------------------------------------

fn pair(items: &[(&str, &str)]) -> Params {
    let mut m = Map::new();
    for (k, v) in items {
        m.insert((*k).to_string(), Value::String((*v).to_string()));
    }
    m
}

/// Look up recommended (indexing, query) params for a model. First match wins.
/// Returns `None` when no curated entry matches.
pub fn lookup_defaults(provider: &str, model: &str) -> Option<(Params, Params)> {
    // (provider, exact-name | regex, indexing, query)
    let st = "sentence-transformers";
    let ll = "litellm";

    // Exact-name entries.
    let exact: &[(&str, &str, &[(&str, &str)], &[(&str, &str)])] = &[
        (st, "nomic-ai/CodeRankEmbed", &[], &[("prompt_name", "query")]),
        (st, "nomic-ai/nomic-embed-code", &[], &[("prompt_name", "query")]),
        (st, "nomic-ai/nomic-embed-text-v1", &[("prompt_name", "passage")], &[("prompt_name", "query")]),
        (st, "nomic-ai/nomic-embed-text-v1.5", &[("prompt_name", "passage")], &[("prompt_name", "query")]),
        (st, "mixedbread-ai/mxbai-embed-large-v1", &[], &[("prompt_name", "query")]),
    ];
    for (p, name, idx, qry) in exact {
        if *p == provider && *name == model {
            return Some((pair(idx), pair(qry)));
        }
    }

    // Regex entries.
    let regexes: &[(&str, &str, &[(&str, &str)], &[(&str, &str)])] = &[
        (st, r"^Snowflake/snowflake-arctic-embed-.+$", &[], &[("prompt_name", "query")]),
        (ll, r"^cohere/embed-(english|multilingual)(-light)?-v3\.0$", &[("input_type", "search_document")], &[("input_type", "search_query")]),
        (ll, r"^voyage/.+$", &[("input_type", "document")], &[("input_type", "query")]),
        (ll, r"^nvidia_nim/nvidia/.+$", &[("input_type", "passage")], &[("input_type", "query")]),
        (ll, r"^gemini/(gemini-embedding|text-embedding|embedding)[-\w.]*$", &[("input_type", "RETRIEVAL_DOCUMENT")], &[("input_type", "RETRIEVAL_QUERY")]),
    ];
    for (p, re, idx, qry) in regexes {
        if *p == provider && Regex::new(re).ok()?.is_match(model) {
            return Some((pair(idx), pair(qry)));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::EmbeddingSettings;

    fn parse(yaml: &str) -> EmbeddingSettings {
        serde_yaml::from_str(yaml).unwrap()
    }

    #[test]
    fn legacy_bridge_fires_when_params_absent() {
        let s = parse("provider: sentence-transformers\nmodel: nomic-ai/CodeRankEmbed\n");
        let r = resolve_embedder_params(&s).unwrap();
        assert!(r.used_backward_compat);
        assert_eq!(r.query.get("prompt_name").unwrap(), "query");
    }

    #[test]
    fn present_null_query_params_opts_out_of_legacy_bridge() {
        // `query_params:` present-but-null must opt out of the bridge (Python parity).
        let s = parse("provider: sentence-transformers\nmodel: nomic-ai/CodeRankEmbed\nquery_params:\n");
        let r = resolve_embedder_params(&s).unwrap();
        assert!(!r.used_backward_compat, "present-null query_params must opt out");
        assert!(r.query.is_empty());
    }

    #[test]
    fn explicit_params_are_validated_and_passed_through() {
        let s = parse(
            "provider: litellm\nmodel: voyage/voyage-3\nindexing_params:\n  input_type: document\n",
        );
        let r = resolve_embedder_params(&s).unwrap();
        assert_eq!(r.indexing.get("input_type").unwrap(), "document");
    }
}
