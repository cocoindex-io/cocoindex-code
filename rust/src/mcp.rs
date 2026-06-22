//! MCP server (stdio, newline-delimited JSON-RPC). Ports `server.py` —
//! exposes a single `search` tool that delegates to the daemon via `client`.

use anyhow::Result;
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

const PROTOCOL_VERSION: &str = "2024-11-05";

const INSTRUCTIONS: &str = "Code search and codebase understanding tools. Use when you need to \
find code, understand how something works, locate implementations, or explore an unfamiliar \
codebase. Provides semantic search that understands meaning -- unlike grep or text matching, it \
finds relevant code even when exact keywords are unknown.";

const SEARCH_DESCRIPTION: &str = "Semantic code search across the entire codebase -- finds code by \
meaning, not just text matching. Use this instead of grep/glob when you need to find \
implementations, understand how features work, or locate related code without knowing exact \
names or keywords. Accepts natural language queries (e.g., 'authentication logic', 'database \
connection handling') or code snippets. Returns matching code chunks with file paths, line \
numbers, and relevance scores. Start with a small limit (e.g., 5); if most results look \
relevant, use offset to paginate for more.";

fn search_tool_schema() -> Value {
    json!({
        "name": "search",
        "description": SEARCH_DESCRIPTION,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": { "type": "string", "description": "Natural language query or code snippet to search for. Examples: 'error handling middleware', 'how are users authenticated', 'database connection pool', or paste a code snippet to find similar code." },
                "limit": { "type": "integer", "minimum": 1, "maximum": 100, "default": 5, "description": "Maximum number of results to return (1-100)." },
                "offset": { "type": "integer", "minimum": 0, "default": 0, "description": "Number of results to skip for pagination." },
                "refresh_index": { "type": "boolean", "default": true, "description": "Whether to incrementally update the index before searching. Set to False for faster consecutive queries when the codebase hasn't changed." },
                "languages": { "type": "array", "items": { "type": "string" }, "description": "Filter by programming language(s). Example: ['python', 'typescript']" },
                "paths": { "type": "array", "items": { "type": "string" }, "description": "Filter by file path pattern(s) using GLOB wildcards (* and ?). Example: ['src/utils/*', '*.py']" }
            },
            "required": ["query"]
        }
    })
}

/// Run the MCP server over stdio for `project_root`. Kicks off a background
/// index, then serves JSON-RPC until stdin closes.
pub async fn serve(project_root: String) -> Result<()> {
    // Background index (best effort), so the first search is warm.
    let bg_root = project_root.clone();
    tokio::spawn(async move {
        let _ = crate::client::index(&bg_root, || {}).await;
    });

    let stdin = BufReader::new(tokio::io::stdin());
    let mut lines = stdin.lines();
    let mut stdout = tokio::io::stdout();

    while let Some(line) = lines.next_line().await? {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let id = msg.get("id").cloned();
        let method = msg.get("method").and_then(Value::as_str).unwrap_or("");

        // Notifications have no id and are never answered. Requests (with an id)
        // always get a reply: a result for known methods, or a JSON-RPC
        // "method not found" error otherwise — a silent drop hangs the client.
        let response: std::result::Result<Value, (i64, &str)> = match method {
            "initialize" => Ok(json!({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": { "tools": {} },
                "serverInfo": { "name": "cocoindex-code", "version": env!("CARGO_PKG_VERSION") },
                "instructions": INSTRUCTIONS,
            })),
            "tools/list" => Ok(json!({ "tools": [search_tool_schema()] })),
            "tools/call" => Ok(handle_tool_call(&project_root, msg.get("params")).await),
            "ping" => Ok(json!({})),
            _ => Err((-32601, "Method not found")),
        };

        if let Some(id) = id {
            let envelope = match response {
                Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
                Err((code, message)) => json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": { "code": code, "message": message },
                }),
            };
            let mut out = serde_json::to_string(&envelope)?;
            out.push('\n');
            stdout.write_all(out.as_bytes()).await?;
            stdout.flush().await?;
        }
    }
    Ok(())
}

async fn handle_tool_call(project_root: &str, params: Option<&Value>) -> Value {
    let params = params.cloned().unwrap_or_else(|| json!({}));
    let name = params.get("name").and_then(Value::as_str).unwrap_or("");
    if name != "search" {
        return tool_error(format!("Unknown tool: {name}"));
    }
    let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
    let query = args.get("query").and_then(Value::as_str).unwrap_or("").to_string();
    if query.trim().is_empty() {
        return tool_error("`query` is required".to_string());
    }
    let limit = args.get("limit").and_then(Value::as_i64).unwrap_or(5);
    let offset = args.get("offset").and_then(Value::as_i64).unwrap_or(0);
    let refresh = args.get("refresh_index").and_then(Value::as_bool).unwrap_or(true);
    let languages = str_list(args.get("languages"));
    let paths = str_list(args.get("paths"));

    if refresh {
        let _ = crate::client::index(project_root, || {}).await;
    }
    match crate::client::search(project_root, &query, languages, paths, limit, offset, || {}).await {
        Ok(outcome) => {
            let results: Vec<Value> = outcome
                .results
                .iter()
                .map(|r| {
                    json!({
                        "file_path": r.file_path,
                        "language": r.language,
                        "content": r.content,
                        "start_line": r.start_line,
                        "end_line": r.end_line,
                        "score": r.score,
                    })
                })
                .collect();
            let payload = json!({
                "success": outcome.success,
                "results": results,
                "total_returned": outcome.results.len(),
                "offset": offset,
            });
            json!({
                "content": [{ "type": "text", "text": serde_json::to_string_pretty(&payload).unwrap_or_default() }],
                "isError": false,
            })
        }
        Err(e) => tool_error(format!("Query failed: {e}")),
    }
}

fn str_list(v: Option<&Value>) -> Option<Vec<String>> {
    let arr = v?.as_array()?;
    let out: Vec<String> = arr.iter().filter_map(|x| x.as_str().map(str::to_string)).collect();
    if out.is_empty() { None } else { Some(out) }
}

fn tool_error(message: String) -> Value {
    json!({
        "content": [{ "type": "text", "text": message }],
        "isError": true,
    })
}
