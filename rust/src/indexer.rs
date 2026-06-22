//! CocoIndex flow for indexing codebases. Ports `indexer.py`.
//!
//! Pipeline: walk -> filter (include/exclude + .gitignore) -> detect language
//! -> tree-sitter chunk -> embed -> upsert into the sqlite-vec table.

use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock};

use anyhow::Result as AnyResult;
use cocoindex::ops::text::{RecursiveChunkConfig, RecursiveSplitter, detect_code_language};
use cocoindex::prelude::*;
use cocoindex::RunStats;
use cocoindex::sqlite::{self, ColumnDef, SqliteTableOptions, TableSchema, Vec0TableDef};

use crate::embedder::CodeEmbedder;
use crate::embedder_params::Params;
use crate::schema::{CodeChunk, TABLE_NAME};
use crate::settings::{ProjectSettings, UserSettings};

// Chunking configuration (matches indexer.py).
const CHUNK_SIZE: usize = 1000;
const MIN_CHUNK_SIZE: usize = 250;
const CHUNK_OVERLAP: usize = 150;

// ---------------------------------------------------------------------------
// Context keys
// ---------------------------------------------------------------------------

/// Target sqlite database. State id tracks the db path.
pub static DB: LazyLock<ContextKey<sqlite::Database>> = LazyLock::new(|| {
    ContextKey::new_with_state("index_db", |db: &sqlite::Database| db.state_id().to_string())
});

/// Shared embedder. State key tracks provider+model so changing the model
/// invalidates memoized files (parity for Python `detect_change=True`).
pub static EMBEDDER: LazyLock<ContextKey<CodeEmbedder>> = LazyLock::new(|| {
    ContextKey::new_with_state("embedder", |e: &CodeEmbedder| e.state_key())
});

/// Project settings (language overrides used during processing).
pub static PROJECT_SETTINGS: LazyLock<ContextKey<Arc<ProjectSettings>>> =
    LazyLock::new(|| ContextKey::new("project_settings"));

// ---------------------------------------------------------------------------
// Per-file processing (memoized)
// ---------------------------------------------------------------------------

#[cocoindex::function]
async fn process_file(ctx: &Ctx, file: FileEntry, model_tag: String) -> Result<Vec<CodeChunk>> {
    // `model_tag` (the embedder's identity) is part of the memo key so changing
    // the embedding model reprocesses every file with the new model — parity for
    // Python's `Annotated[embedding, EMBEDDER]` + `detect_change=True`. Without
    // it, stale embeddings of the old dimension would be re-declared against a
    // recreated vec0 table and fail with a dimension mismatch.
    let _ = &model_tag;
    let file_path = file.key();
    let content = match file.content_str() {
        Ok(c) => c,
        // Non-UTF-8 file: skip (Python catches UnicodeDecodeError).
        Err(_) => return Ok(Vec::new()),
    };
    if content.trim().is_empty() {
        return Ok(Vec::new());
    }

    let suffix = match Path::new(&file_path).extension() {
        // Match Python's `PurePath.suffix`: an empty extension (e.g. a
        // trailing-dot name like `foo.`) yields no suffix, not `"."`.
        Some(e) if !e.is_empty() => format!(".{}", e.to_string_lossy()),
        _ => String::new(),
    };
    let filename = Path::new(&file_path)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| file_path.clone());

    let project_settings: &Arc<ProjectSettings> = ctx.get_key(&PROJECT_SETTINGS)?;
    let language = project_settings
        .language_overrides
        .iter()
        .find(|lo| format!(".{}", lo.ext) == suffix)
        .map(|lo| lo.lang.clone())
        .or_else(|| detect_code_language(&filename))
        .unwrap_or_else(|| "text".to_string());

    let splitter = RecursiveSplitter::new().map_err(|e| Error::engine(format!("splitter: {e}")))?;
    let chunks = splitter.split_with(
        &content,
        RecursiveChunkConfig {
            chunk_size: CHUNK_SIZE,
            min_chunk_size: Some(MIN_CHUNK_SIZE),
            chunk_overlap: Some(CHUNK_OVERLAP),
            language: Some(language.clone()),
        },
    );
    if chunks.is_empty() {
        return Ok(Vec::new());
    }

    let codes: Vec<String> = chunks.iter().map(|c| c.text(&content).to_string()).collect();
    let embedder = ctx.get_key(&EMBEDDER)?;
    let embeddings = embedder
        .embed_batch(codes.clone(), &Params::new())
        .await
        .map_err(|e| Error::engine(format!("embed: {e}")))?;

    let mut id_gen = IdGenerator::new();
    let mut rows = Vec::with_capacity(chunks.len());
    for ((chunk, code), embedding) in chunks.iter().zip(codes).zip(embeddings) {
        let id = id_gen.next_id(ctx, &code).await?;
        let id = i64::try_from(id).map_err(|_| Error::engine("chunk id does not fit in i64"))?;
        rows.push(CodeChunk {
            id,
            file_path: file_path.clone(),
            language: language.clone(),
            content: chunk.text(&content).to_string(),
            start_line: chunk.start.line as i64,
            end_line: chunk.end.line as i64,
            embedding,
        });
    }
    Ok(rows)
}

// ---------------------------------------------------------------------------
// Main flow
// ---------------------------------------------------------------------------

fn table_schema(dim: usize) -> AnyResult<TableSchema> {
    Ok(TableSchema::new(
        [
            ("id", ColumnDef::new("INTEGER")),
            ("file_path", ColumnDef::new("TEXT")),
            ("language", ColumnDef::new("TEXT")),
            ("content", ColumnDef::new("TEXT")),
            ("start_line", ColumnDef::new("INTEGER")),
            ("end_line", ColumnDef::new("INTEGER")),
            ("embedding", ColumnDef::new(format!("float[{dim}]"))),
        ],
        ["id"],
    )?)
}

async fn app_main(
    ctx: Ctx,
    root: PathBuf,
    include: Vec<String>,
    exclude: Vec<String>,
    dim: usize,
    model_tag: String,
) -> Result<()> {
    let opts = SqliteTableOptions {
        virtual_table_def: Some(Vec0TableDef {
            partition_key_columns: vec!["language".to_string()],
            auxiliary_columns: vec![
                "file_path".to_string(),
                "content".to_string(),
                "start_line".to_string(),
                "end_line".to_string(),
            ],
        }),
        ..Default::default()
    };
    let schema = table_schema(dim).map_err(|e| Error::engine(e.to_string()))?;
    let table = sqlite::mount_table_target_with_options(&ctx, &DB, TABLE_NAME, schema, opts).await?;

    let matcher = crate::walk::GitignoreAwareMatcher::new(&root, &include, &exclude)
        .map_err(|e| Error::engine(e.to_string()))?;
    let files: Vec<(String, FileEntry)> = walk_dir(root.clone())
        .recursive(true)
        .path_matcher(matcher)
        .items()?;
    println!("indexing {} files from {}", files.len(), root.display());

    let rows_by_file =
        mount_each!(files, |file| process_file(ctx, file, model_tag.clone())).await?;

    let mut count = 0usize;
    for rows in &rows_by_file {
        count += rows.len();
        for row in rows {
            table.declare_row(&ctx, row)?;
        }
    }
    println!("indexed {count} chunks total");
    Ok(())
}

/// Run one indexing pass with a pre-built (warm) embedder. Returns the run
/// stats. Used by the daemon, which holds a single embedder across projects.
pub async fn run_index(
    project_root: &Path,
    embedder: &CodeEmbedder,
    project: &ProjectSettings,
) -> AnyResult<RunStats> {
    let dim = embedder.dimension().await?;

    let db = crate::db::open_target_db(&crate::settings::target_sqlite_db_path(project_root)).await?;
    let coco_db_path = crate::settings::cocoindex_db_path(project_root);

    let include = project.include_patterns.clone();
    let exclude = project.exclude_patterns.clone();
    let root = project_root.to_path_buf();
    let model_tag = embedder.state_key();

    let app = Environment::builder()
        .db_path(coco_db_path)
        .provide_key(&DB, db)
        .provide_key(&EMBEDDER, embedder.clone())
        .provide_key(&PROJECT_SETTINGS, Arc::new(project.clone()))
        .build()
        .await?
        .app("CocoIndexCode")
        .await?;
    let stats = app
        .run(move |ctx| app_main(ctx, root, include, exclude, dim, model_tag))
        .await?;
    Ok(stats)
}

/// Convenience for the in-process path (and tests): build the embedder from
/// settings, then run one indexing pass.
#[allow(dead_code)]
pub async fn index(
    project_root: &Path,
    user: &UserSettings,
    project: &ProjectSettings,
) -> AnyResult<String> {
    let indexing_params = crate::embedder_params::resolve_embedder_params(&user.embedding)?.indexing;
    let embedder = crate::embedder::create_embedder(&user.embedding, &indexing_params).await?;
    let stats = run_index(project_root, &embedder, project).await?;
    Ok(format!("{stats}"))
}
