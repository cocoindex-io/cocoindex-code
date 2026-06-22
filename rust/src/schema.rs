//! Data models for CocoIndex Code. Ports `schema.py` / the `CodeChunk` in
//! `shared.py`.

use serde::{Deserialize, Serialize};

/// A code chunk stored in the sqlite-vec `code_chunks_vec` table.
///
/// Field order/names mirror the Python `CodeChunk` dataclass. `embedding` is a
/// plain `Vec<f32>`; the sqlite target serializes it to the JSON-array literal
/// sqlite-vec expects for a `float[N]` column.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CodeChunk {
    pub id: i64,
    pub file_path: String,
    pub language: String,
    pub content: String,
    pub start_line: i64,
    pub end_line: i64,
    pub embedding: Vec<f32>,
}

/// One result from a vector similarity query. Ports the Python `QueryResult`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueryResult {
    pub file_path: String,
    pub language: String,
    pub content: String,
    pub start_line: i64,
    pub end_line: i64,
    pub score: f64,
}

/// The vector index table name (matches Python `indexer.py`).
pub const TABLE_NAME: &str = "code_chunks_vec";
