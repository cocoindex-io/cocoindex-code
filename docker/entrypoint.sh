#!/bin/sh
# Initialize user settings on first run, then hand off to the daemon.
set -e

# `ccc init` creates ~/.cocoindex_code/global_settings.yml with the default
# embedding model if the file doesn't already exist. Pre-mount a custom
# global_settings.yml to override (see Dockerfile comments).
ccc init -f 2>/dev/null || true

exec ccc run-daemon
