# ─── Stage 1: install dependencies ───────────────────────────────────────────
# Use slim (glibc-based) — cocoindex ships pre-built Rust wheels that need glibc.
# Alpine / musl-libc would require building from source.
FROM python:3.12-slim AS builder

# Install uv via pip — avoids a ghcr.io pull during build
RUN pip install --quiet uv

WORKDIR /build

RUN uv pip install --system --prerelease=allow \
    "cocoindex>=1.0.0a33" \
    "cocoindex-code" \
    "sentence-transformers>=3.3.1"

# ─── Stage 2: pre-bake the default embedding model ────────────────────────────
# Bakes sentence-transformers/all-MiniLM-L6-v2 into the image so cold container
# starts don't trigger a ~90 MB download. Skip this stage (--target builder) if
# you always supply COCOINDEX_CODE_EMBEDDING_MODEL pointing at an external model.
FROM builder AS model_cache

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); print('Model cached.')"

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

# Embedding model: defaults to local SentenceTransformers (no API key needed).
# Override for a code-optimised model, e.g.:
#   -e COCOINDEX_CODE_EMBEDDING_MODEL=voyage/voyage-code-3
#   -e VOYAGE_API_KEY=your-key

# ── Persistent daemon entrypoint ──────────────────────────────────────────────
# Initializes user settings on first start, then runs the daemon in the
# foreground so the container stays alive.
# Use `docker exec` to invoke the CLI or start an MCP session — see README.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
