//! Vector similarity search over the sqlite-vec `vec0` index. Ports `query.py`.

use anyhow::{Result, bail};
use sqlx::{Row, SqlitePool};

use crate::schema::{QueryResult, TABLE_NAME};

/// Convert L2 distance to cosine similarity (exact for unit vectors).
fn l2_to_score(distance: f64) -> f64 {
    1.0 - distance * distance / 2.0
}

/// Encode an embedding as a little-endian float32 blob (what sqlite-vec's
/// `MATCH` / `vec_distance_L2` accept), matching Python's `.tobytes()`.
fn embedding_bytes(vec: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for f in vec {
        out.extend_from_slice(&f.to_le_bytes());
    }
    out
}

struct RawRow {
    file_path: String,
    language: String,
    content: String,
    start_line: i64,
    end_line: i64,
    distance: f64,
}

fn map_row(row: &sqlx::sqlite::SqliteRow) -> Result<RawRow> {
    Ok(RawRow {
        file_path: row.try_get("file_path")?,
        language: row.try_get("language")?,
        content: row.try_get("content")?,
        start_line: row.try_get("start_line")?,
        end_line: row.try_get("end_line")?,
        distance: row.try_get("distance")?,
    })
}

async fn knn_query(
    pool: &SqlitePool,
    embedding: &[u8],
    k: i64,
    language: Option<&str>,
) -> Result<Vec<RawRow>> {
    let sql = if language.is_some() {
        format!(
            "SELECT file_path, language, content, start_line, end_line, distance \
             FROM {TABLE_NAME} WHERE embedding MATCH ? AND k = ? AND language = ? ORDER BY distance"
        )
    } else {
        format!(
            "SELECT file_path, language, content, start_line, end_line, distance \
             FROM {TABLE_NAME} WHERE embedding MATCH ? AND k = ? ORDER BY distance"
        )
    };
    let mut q = sqlx::query(&sql).bind(embedding).bind(k);
    if let Some(lang) = language {
        q = q.bind(lang.to_string());
    }
    let rows = q.fetch_all(pool).await?;
    rows.iter().map(map_row).collect()
}

async fn full_scan_query(
    pool: &SqlitePool,
    embedding: &[u8],
    limit: i64,
    offset: i64,
    languages: &[String],
    paths: &[String],
) -> Result<Vec<RawRow>> {
    let mut conditions: Vec<String> = Vec::new();
    if !languages.is_empty() {
        let placeholders = vec!["?"; languages.len()].join(",");
        conditions.push(format!("language IN ({placeholders})"));
    }
    if !paths.is_empty() {
        let clauses = vec!["file_path GLOB ?"; paths.len()].join(" OR ");
        conditions.push(format!("({clauses})"));
    }
    let where_clause = if conditions.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", conditions.join(" AND "))
    };
    let sql = format!(
        "SELECT file_path, language, content, start_line, end_line, \
         vec_distance_L2(embedding, ?) as distance FROM {TABLE_NAME} {where_clause} \
         ORDER BY distance LIMIT ? OFFSET ?"
    );
    let mut q = sqlx::query(&sql).bind(embedding);
    for lang in languages {
        q = q.bind(lang.clone());
    }
    for path in paths {
        q = q.bind(path.clone());
    }
    q = q.bind(limit).bind(offset);
    let rows = q.fetch_all(pool).await?;
    rows.iter().map(map_row).collect()
}

/// Perform vector similarity search. `query_vec` is the already-embedded query.
pub async fn query_codebase(
    pool: &SqlitePool,
    query_vec: &[f32],
    limit: i64,
    offset: i64,
    languages: &[String],
    paths: &[String],
) -> Result<Vec<QueryResult>> {
    if query_vec.is_empty() {
        bail!("empty query embedding");
    }
    let embedding = embedding_bytes(query_vec);

    // Clamp pagination. A negative `limit`/`offset` would wrap when cast via
    // `as usize` (making `truncate` a no-op that returns every row), become an
    // unbounded `LIMIT -n` in SQL, or push a negative `k` into the KNN.
    let limit = limit.max(0);
    let offset = offset.max(0);

    let mut rows = if !paths.is_empty() {
        full_scan_query(pool, &embedding, limit, offset, languages, paths).await?
    } else if languages.len() <= 1 {
        let lang = languages.first().map(String::as_str);
        knn_query(pool, &embedding, limit + offset, lang).await?
    } else {
        let fetch_k = limit + offset;
        let mut merged: Vec<RawRow> = Vec::new();
        for lang in languages {
            merged.extend(knn_query(pool, &embedding, fetch_k, Some(lang)).await?);
        }
        merged.sort_by(|a, b| a.distance.total_cmp(&b.distance));
        merged.truncate(fetch_k as usize);
        merged
    };

    if paths.is_empty() {
        let skip = offset.max(0) as usize;
        rows = rows.into_iter().skip(skip).collect();
    }

    Ok(rows
        .into_iter()
        .map(|r| QueryResult {
            file_path: r.file_path,
            language: r.language,
            content: r.content,
            start_line: r.start_line,
            end_line: r.end_line,
            score: l2_to_score(r.distance),
        })
        .collect())
}
