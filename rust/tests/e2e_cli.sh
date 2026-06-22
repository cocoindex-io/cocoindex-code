#!/usr/bin/env bash
# End-to-end CLI test suite for the cocoindex-code Rust port.
# Run from anywhere:  bash rust/tests/e2e_cli.sh   (build `ccc` first, or set CCC_BIN)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BIN="${CCC_BIN:-$REPO/rust/target/debug/ccc}"
FIX="$REPO/tests/e2e_docker_fixtures/sample_project"
ROOT="${TMPDIR:-/tmp}/ccc_suite"
rm -rf "$ROOT"; mkdir -p "$ROOT"
export COCOINDEX_CODE_DIR="$ROOT/home" COCOINDEX_CODE_RUNTIME_DIR="$ROOT/run"

PASS=0; FAIL=0; FAILED_NAMES=""
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); FAILED_NAMES="$FAILED_NAMES\n   - $1"; printf '  FAIL %s\n' "$1"; }
# check NAME "expected_substr" actual_output
check() { if printf '%s' "$3" | grep -qF "$2"; then ok "$1"; else bad "$1 (want '$2')"; fi; }
ncheck(){ if printf '%s' "$3" | grep -qF "$2"; then bad "$1 (should NOT contain '$2')"; else ok "$1"; fi; }

stop() { $BIN daemon stop >/dev/null 2>&1; }

echo "### 1. Project setup: init / status-before-index / errors"
P="$ROOT/proj"; mkdir -p "$P"; cp -r "$FIX"/* "$P/"; cd "$P"
out=$($BIN search "x" 2>&1);            check "search before init -> init hint" "ccc init" "$out"
out=$($BIN init 2>&1);                  check "init creates settings"        "Created project settings" "$out"
out=$($BIN init 2>&1);                  check "re-init guarded"              "already initialized" "$out"
test -f "$P/.cocoindex_code/settings.yml" && ok "settings.yml written" || bad "settings.yml written"
# Search with no index transparently builds it first (parity with Python's daemon).
out=$($BIN search "verify password" --limit 1 2>&1); check "search auto-indexes (no prior index)" "File:" "$out"

echo "### 2. Index + stats"
out=$($BIN index 2>&1)
check "index stats python"    "python:" "$out"
dlog=$(cat "$ROOT/run/daemon.log" 2>/dev/null); check "daemon logged index run" "indexed" "$dlog"
out=$($BIN status 2>&1)
check "status shows project"  "Project:" "$out"
check "status shows index db" "Index DB:" "$out"
check "status chunk count"    "Chunks:" "$out"

echo "### 3. Search variants"
out=$($BIN search "verify password" --limit 2 2>&1);          check "basic search"        "auth.py" "$out"
out=$($BIN search "verify password" --lang python --limit 2 2>&1); check "--lang python"   "[python]" "$out"
out=$($BIN search "verify password" --lang nonexistent 2>&1);  check "--lang no-match"    "No results" "$out"
out=$($BIN search "helper" --path 'lib/*' --limit 2 2>&1);     check "--path filter"      "lib/utils.ts" "$out"
out=$($BIN search "helper" --path 'lib/*' --limit 2 2>&1);     ncheck "--path excludes others" "auth.py" "$out"
out=$($BIN search "request handler dispatch" --limit 1 2>&1);  check "multi-word query"   "handlers.py" "$out"
out=$($BIN search "" 2>&1);                                    check "empty query guard"  "usage:" "$out"
r0=$($BIN search "function" --limit 1 --offset 0 2>&1 | grep -m1 "File:")
r1=$($BIN search "function" --limit 1 --offset 1 2>&1 | grep -m1 "File:")
[ "$r0" != "$r1" ] && ok "offset paginates" || bad "offset paginates ($r0 == $r1)"

echo "### 4. Incremental indexing"
c1=$($BIN status 2>&1 | grep -oE 'Chunks: [0-9]+'); $BIN index >/dev/null 2>&1
c2=$($BIN status 2>&1 | grep -oE 'Chunks: [0-9]+')
{ [ -n "$c1" ] && [ "$c1" = "$c2" ]; } && ok "re-index idempotent ($c1)" || bad "re-index idempotent ($c1 vs $c2)"
echo "def brand_new_zztoken(): pass" >> "$P/src/auth.py"
$BIN index >/dev/null 2>&1
out=$($BIN search "zztoken" --limit 3 2>&1);  check "edit picked up on reindex" "auth.py" "$out"
rm "$P/lib/utils.ts"; $BIN index >/dev/null 2>&1
out=$($BIN status 2>&1)
ncheck "deleted file removed from index" "typescript" "$out"

echo "### 5. Daemon lifecycle"
out=$($BIN daemon status 2>&1);   check "daemon status version" "Daemon version:" "$out"
check "daemon lists project" "proj" "$out"
out=$($BIN daemon restart 2>&1);  check "daemon restart" "Daemon restarted" "$out"
out=$($BIN daemon status 2>&1);   check "daemon up after restart" "Daemon version:" "$out"
out=$($BIN daemon stop 2>&1);     check "daemon stop" "Daemon stopped" "$out"
out=$($BIN daemon stop 2>&1);     check "daemon stop when down" "not running" "$out"

echo "### 6. Auto-index on search (no prior index)"
P2="$ROOT/auto"; mkdir -p "$P2"; cp -r "$FIX"/* "$P2/"; cd "$P2"; $BIN init >/dev/null 2>&1
out=$($BIN search "authentication" --limit 1 2>&1);  check "auto-index waits" "Waiting for indexing" "$out"
check "auto-index returns results" "File:" "$out"
stop

echo "### 7. Doctor"
cd "$P"; out=$($BIN doctor 2>&1)
for s in "Global Settings" "Daemon" "Model Check (indexing)" "Model Check (query)" "Project Settings" "File Walk" "Index Status" "Log Files"; do
  check "doctor section: $s" "$s" "$out"
done
check "doctor model OK" "[OK] Model Check" "$out"
stop

echo "### 8. MCP server (stdio JSON-RPC)"
cd "$P"
mcp=$(printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search","arguments":{"query":"password","limit":1,"refresh_index":false}}}' \
 '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"bogus","arguments":{}}}' \
 | $BIN mcp 2>/dev/null)
check "mcp initialize"   '"serverInfo"' "$mcp"
check "mcp tools/list"   '"search"' "$mcp"
check "mcp tools/call ok" '"isError":false' "$mcp"
check "mcp unknown tool"  "Unknown tool" "$mcp"
stop

echo "### 9. Reset"
cd "$P"; out=$($BIN reset -f 2>&1);   check "reset db only" "Databases deleted" "$out"
test -f "$P/.cocoindex_code/settings.yml" && ok "reset keeps settings" || bad "reset keeps settings"
out=$($BIN reset --all -f 2>&1);      check "reset --all" "fully reset" "$out"
test -f "$P/.cocoindex_code/settings.yml" && bad "reset --all removes settings" || ok "reset --all removes settings"
stop

echo "### Summary"
echo "PASSED: $PASS   FAILED: $FAIL"
[ "$FAIL" -gt 0 ] && printf 'Failed:%b\n' "$FAILED_NAMES"
exit $FAIL
