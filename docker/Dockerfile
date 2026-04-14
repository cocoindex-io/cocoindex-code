# ─── Stage 1: install dependencies ───────────────────────────────────────────
# Use slim (glibc-based) — cocoindex ships pre-built Rust wheels that need glibc.
# Alpine / musl-libc would require building from source.
FROM python:3.12-slim AS builder

# Install uv via pip — avoids a ghcr.io pull during build
RUN pip install --quiet uv

WORKDIR /build

RUN uv pip install --system --prerelease=allow \
    "cocoindex>=1.0.0a33" \
    "cocoindex-code[default]"

# ─── Stage 2: pre-bake the default embedding model ────────────────────────────
# Bakes Snowflake/snowflake-arctic-embed-xs into the image so cold container
# starts don't trigger a download. Skip this stage (--target builder) if
# you always supply a global_settings.yml pointing at a non-local model.
FROM builder AS model_cache

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('Snowflake/snowflake-arctic-embed-xs'); print('Model cached.')"

# ─── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Copy installed packages and cached model from previous stages
COPY --from=model_cache /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=model_cache /usr/local/bin/cocoindex-code /usr/local/bin/cocoindex-code
COPY --from=model_cache /usr/local/bin/ccc /usr/local/bin/ccc
COPY --from=model_cache /root/.cache /root/.cache

# The codebase is mounted at runtime — nothing project-specific lives here.
WORKDIR /workspace

# ── Runtime defaults (all overridable via -e / --env) ─────────────────────────

# Map index databases into the container's native filesystem so SQLite avoids
# the slow/unreliable cross-OS volume layer (especially on macOS / Windows).
# Format: /source=/target  — databases for projects under /workspace go to /db.
ENV COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/db

# Additional extensions: add project-specific extras at runtime, e.g.:
#   -e COCOINDEX_CODE_EXTRA_EXTENSIONS="conf,sbt"
# See README for ext:lang syntax to map extensions to existing parsers.

# Exclude patterns: override at runtime for your build system, e.g.:
#   -e COCOINDEX_CODE_EXCLUDE_PATTERNS='["**/target/**","**/node_modules/**"]'

# Embedding model: defaults to local sentence-transformers
# (Snowflake/snowflake-arctic-embed-xs, pre-baked above).
# To use a different model, pre-mount a global_settings.yml into
# ~/.cocoindex_code/ before the container starts, e.g.
#   -v /path/to/global_settings.yml:/root/.cocoindex_code/global_settings.yml

# ── Persistent daemon entrypoint ──────────────────────────────────────────────
# Initializes user settings on first start, then runs the daemon in the
# foreground so the container stays alive.
# Use `docker exec` to invoke the CLI or start an MCP session — see README.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
