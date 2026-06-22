//! SQLite connection setup with the sqlite-vec (vec0) extension registered.
//!
//! The CocoIndex SDK's `sqlite::Database::connect` does not load vec0, so we
//! build the pool ourselves (registering vec0 as an auto-extension) and hand it
//! to `Database::from_pool`. Python relies on the `sqlite-vec` PyPI package for
//! the same effect.

use std::path::Path;
use std::sync::Once;

use anyhow::{Context, Result};
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePool, SqlitePoolOptions};

static INIT_VEC0: Once = Once::new();

/// Register sqlite-vec as a SQLite auto-extension exactly once, so every
/// connection opened afterwards (by sqlx) has the `vec0` module available.
fn register_vec0() {
    INIT_VEC0.call_once(|| {
        // SAFETY: `sqlite3_auto_extension` stores the init fn pointer; sqlite-vec's
        // init has the standard loadable-extension signature. Must run before any
        // connection is opened on this process.
        unsafe {
            libsqlite3_sys::sqlite3_auto_extension(Some(std::mem::transmute::<
                *const (),
                unsafe extern "C" fn(
                    *mut libsqlite3_sys::sqlite3,
                    *mut *mut std::os::raw::c_char,
                    *const libsqlite3_sys::sqlite3_api_routines,
                ) -> std::os::raw::c_int,
            >(sqlite_vec::sqlite3_vec_init as *const ())));
        }
    });
}

/// Open (creating if missing) a sqlite pool with vec0 available. SQLite is a
/// single-writer engine, so the pool is capped at one connection (matches the
/// SDK).
pub async fn open_pool(path: &Path) -> Result<SqlitePool> {
    register_vec0();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true)
        // WAL lets the read-only query pool read concurrently with the writer;
        // busy_timeout absorbs brief lock contention instead of erroring.
        .journal_mode(SqliteJournalMode::Wal)
        .busy_timeout(std::time::Duration::from_secs(5));
    SqlitePoolOptions::new()
        .max_connections(1)
        .connect_with(options)
        .await
        .with_context(|| format!("opening sqlite db at {}", path.display()))
}

/// Open a read-only sqlite pool with vec0 available (for the query path).
pub async fn open_readonly_pool(path: &Path) -> Result<SqlitePool> {
    register_vec0();
    let options = SqliteConnectOptions::new()
        .filename(path)
        .read_only(true)
        .busy_timeout(std::time::Duration::from_secs(5));
    SqlitePoolOptions::new()
        .max_connections(4)
        .connect_with(options)
        .await
        .with_context(|| format!("opening sqlite db (readonly) at {}", path.display()))
}

/// Build the SDK `Database` handle around a vec0-enabled pool.
pub async fn open_target_db(path: &Path) -> Result<cocoindex::sqlite::Database> {
    let pool = open_pool(path).await?;
    Ok(cocoindex::sqlite::Database::from_pool(
        path.to_string_lossy().to_string(),
        pool,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::Row;

    /// The riskiest integration: confirm the sqlite-vec `vec0` virtual-table
    /// module is registered and a KNN query runs.
    #[tokio::test]
    async fn vec0_extension_loads_and_queries() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("t.db");
        let pool = open_pool(&path).await.unwrap();

        sqlx::query("CREATE VIRTUAL TABLE t USING vec0(id integer primary key, embedding float[3])")
            .execute(&pool)
            .await
            .expect("create vec0 table");
        sqlx::query("INSERT INTO t(id, embedding) VALUES (1, '[1,2,3]'), (2, '[9,9,9]')")
            .execute(&pool)
            .await
            .expect("insert vectors");

        let probe = [1.0f32, 2.0, 3.0];
        let mut blob = Vec::new();
        for f in probe {
            blob.extend_from_slice(&f.to_le_bytes());
        }
        let row =
            sqlx::query("SELECT id FROM t WHERE embedding MATCH ? AND k = 1 ORDER BY distance")
                .bind(blob)
                .fetch_one(&pool)
                .await
                .expect("knn query");
        let id: i64 = row.try_get("id").unwrap();
        assert_eq!(id, 1, "nearest neighbour to [1,2,3] should be row 1");
    }
}
