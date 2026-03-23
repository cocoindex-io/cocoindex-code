#!/bin/sh
# Initialize user settings on first run, then hand off to the daemon.
set -e

# `ccc init` creates ~/.cocoindex_code/global_settings.yml if it doesn't exist.
# It reads COCOINDEX_CODE_EMBEDDING_MODEL and other env vars at this point.
ccc init -f 2>/dev/null || true

exec ccc run-daemon
