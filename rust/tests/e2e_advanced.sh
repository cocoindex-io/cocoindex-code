#!/usr/bin/env bash
# Advanced E2E scenarios for the cocoindex-code Rust port: custom config,
# language overrides, multi-project daemon, model-swap auto-restart, MCP args,
# odd files, and a real Rust codebase.
# Run:  bash rust/tests/e2e_advanced.sh   (build `ccc` first, or set CCC_BIN)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BIN="${CCC_BIN:-$REPO/rust/target/debug/ccc}"
FIX="$REPO/tests/e2e_docker_fixtures/sample_project"
ROOT="${TMPDIR:-/tmp}/ccc_suite2"
rm -rf "$ROOT"; mkdir -p "$ROOT"
export COCOINDEX_CODE_DIR="$ROOT/home" COCOINDEX_CODE_RUNTIME_DIR="$ROOT/run"
PASS=0; FAIL=0; FN=""
ok(){ PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); FN="$FN\n   - $1"; printf '  FAIL %s\n' "$1"; }
has(){ if printf '%s' "$3"|grep -qF "$2"; then ok "$1"; else bad "$1 (want '$2')"; fi; }
hasnt(){ if printf '%s' "$3"|grep -qF "$2"; then bad "$1 (unwanted '$2')"; else ok "$1"; fi; }
stop(){ $BIN daemon stop >/dev/null 2>&1; }

echo "### A. Custom include pattern + language_override"
P="$ROOT/cfg"; mkdir -p "$P"; cd "$P"; $BIN init >/dev/null 2>&1
cat > .cocoindex_code/settings.yml <<'EOF'
include_patterns:
- '**/*.py'
- '**/*.inc'
exclude_patterns:
- '**/.*'
- '**/skipme'
language_overrides:
- ext: inc
  lang: php
EOF
echo "<?php function zz_inc(){} ?>" > legacy.inc
echo "def zz_py(): pass" > app.py
mkdir -p skipme; echo "def zz_skip(): pass" > skipme/a.py
$BIN index >/dev/null 2>&1
out=$($BIN search "zz_inc" --limit 3 2>&1)
has "custom .inc indexed"        "legacy.inc" "$out"
has ".inc language override=php" "[php]"      "$out"
out=$($BIN search "zz_skip skip" --limit 5 2>&1)
hasnt "custom exclude pattern works" "skipme/a.py" "$out"
out=$($BIN doctor 2>&1)
has "doctor shows lang override" ".inc -> php" "$out"
stop

echo "### B. Multi-project daemon"
PA="$ROOT/projA"; PB="$ROOT/projB"
mkdir -p "$PA" "$PB"; cp -r "$FIX"/* "$PA/"; cp -r "$FIX"/* "$PB/"
( cd "$PA" && $BIN init >/dev/null 2>&1 && $BIN index >/dev/null 2>&1 )
( cd "$PB" && $BIN init >/dev/null 2>&1 && $BIN index >/dev/null 2>&1 )
out=$(cd "$PA" && $BIN daemon status 2>&1)
has "daemon serves projA" "projA" "$out"
has "daemon serves projB" "projB" "$out"
out=$(cd "$PA" && $BIN search "auth" --limit 1 2>&1); has "projA searchable" "File:" "$out"
out=$(cd "$PB" && $BIN search "auth" --limit 1 2>&1); has "projB searchable" "File:" "$out"
stop

echo "### C. Settings-change auto-restart (model swap -> new dim)"
P="$ROOT/swap"; mkdir -p "$P"; cp -r "$FIX"/* "$P/"; cd "$P"; $BIN init >/dev/null 2>&1
$BIN index >/dev/null 2>&1
out=$($BIN doctor 2>&1 | grep -m1 "Embedding dimension")
has "initial model dim 384" "384" "$out"
cat > "$ROOT/home/global_settings.yml" <<'EOF'
embedding:
  provider: sentence-transformers
  model: BAAI/bge-base-en-v1.5
EOF
sleep 1
out=$($BIN doctor 2>&1 | grep -m1 "Embedding dimension")
has "daemon auto-restarted, new dim 768" "768" "$out"
# Model change must reprocess files (new dim) — verifies the memo-key fix.
out=$($BIN search "password" --limit 1 2>&1); has "search works after model swap" "File:" "$out"
stop

echo "### D. MCP tool args (languages / paths / refresh)"
P="$ROOT/mcp"; mkdir -p "$P"; cp -r "$FIX"/* "$P/"; cd "$P"; $BIN init >/dev/null 2>&1; $BIN index >/dev/null 2>&1
mcp=$(printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search","arguments":{"query":"password","languages":["python"],"limit":2,"refresh_index":false}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"query":"helper","paths":["lib/*"],"limit":2,"refresh_index":false}}}' \
 | $BIN mcp 2>/dev/null)
has "mcp languages filter -> python" "auth.py" "$mcp"
has "mcp paths filter -> lib"        "utils.ts" "$mcp"
stop

echo "### E. Odd files: empty/blank skipped; binary indexed lossily (Python parity)"
P="$ROOT/odd"; mkdir -p "$P"; cd "$P"; $BIN init >/dev/null 2>&1
printf '' > empty.py                                   # empty -> skipped
printf '   \n\t\n' > blank.py                          # whitespace only -> skipped
printf '\xff\xfe\x00\x01bad bytes' > binary.py         # invalid UTF-8 -> indexed lossily (like Python)
echo "def real_zzfunc(): return 42" > real.py          # valid
$BIN index >/dev/null 2>&1
out=$($BIN status 2>&1)
has "valid file indexed"  "Chunks:" "$out"
out=$($BIN search "real_zzfunc" --limit 5 2>&1)
has "valid file searchable" "real.py" "$out"
files=$($BIN search "zz" --limit 20 2>&1 | grep -oE 'File: [^:]+' | sort -u)
hasnt "empty.py not indexed (no content)"  "empty.py"  "$files"
hasnt "blank.py not indexed (whitespace)"  "blank.py"  "$files"
# Note: binary.py IS indexed lossily, matching Python's read_text(errors='replace').

echo "### F. Real Rust codebase (the port's own src) — multi-language"
P="$ROOT/realrust"; mkdir -p "$P/src"
cp "$REPO"/rust/src/*.rs "$P/src/"
cp "$REPO"/rust/Cargo.toml "$P/"
cp "$REPO"/rust/README.md "$P/"
cd "$P"; $BIN init >/dev/null 2>&1; rr=$($BIN index 2>&1 | grep -E "rust:|toml:|markdown:")
has "indexed rust files"     "rust:"     "$rr"
has "indexed toml"           "toml:"     "$rr"
has "indexed markdown"       "markdown:" "$rr"
out=$($BIN search "sqlite vec0 vector similarity KNN query" --lang rust --limit 1 2>&1)
has "semantic search over rust code" "[rust]" "$out"
stop

echo "### Summary"
echo "PASSED: $PASS   FAILED: $FAIL"
[ "$FAIL" -gt 0 ] && printf 'Failed:%b\n' "$FN"
exit $FAIL
