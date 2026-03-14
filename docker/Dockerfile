# ─── Stage 1: install dependencies ───────────────────────────────────────────
# Use slim (glibc-based) — cocoindex ships pre-built Rust wheels that need glibc.
# Alpine / musl-libc would require building from source.
FROM python:3.12-slim AS builder

# Install uv via pip — avoids a ghcr.io pull during build
RUN pip install --quiet uv

WORKDIR /build

RUN uv pip install --system --prerelease=allow \
    "cocoindex>=1.0.0a16" \
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
COPY --from=model_cache /root/.cache /root/.cache

# The codebase is mounted at runtime — nothing project-specific lives here.
WORKDIR /workspace

# ── Runtime defaults (all overridable via -e / --env) ─────────────────────────
# Root path: point at the mounted workspace.
ENV COCOINDEX_CODE_ROOT_PATH=/workspace

# Additional extensions: add project-specific extras at runtime, e.g.:
#   -e COCOINDEX_CODE_EXTRA_EXTENSIONS="conf,sbt"
# See README for ext:lang syntax to map extensions to existing parsers.

# Exclude patterns: override at runtime for your build system, e.g.:
#   -e COCOINDEX_CODE_EXCLUDE_PATTERNS='["**/target/**","**/node_modules/**"]'

# Embedding model: defaults to local SentenceTransformers (no API key needed).
# Override for a code-optimised model, e.g.:
#   -e COCOINDEX_CODE_EMBEDDING_MODEL=voyage/voyage-code-3
#   -e VOYAGE_API_KEY=your-key

# ── MCP stdio entrypoint ──────────────────────────────────────────────────────
# 'serve' runs the MCP server over stdio (JSON-RPC on stdin/stdout).
# 'index' is a one-shot indexer — useful for pre-warming before first use.
ENTRYPOINT ["cocoindex-code", "serve"]
