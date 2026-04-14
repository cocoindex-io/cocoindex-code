# ─── Stage 1: install dependencies ───────────────────────────────────────────
# Use slim (glibc-based) — cocoindex ships pre-built Rust wheels that need glibc.
# Alpine / musl-libc would require building from source.
FROM python:3.12-slim AS builder

RUN pip install --quiet uv

WORKDIR /build

# Default: install the released cocoindex-code from PyPI (release flow).
# Tests/local dev override with:
#   --build-arg CCC_INSTALL_SPEC=/ccc-src[default]
# which installs from the copied-in source tree instead. The COPY always runs;
# with .dockerignore trimming build artifacts it adds ~nothing.
ARG CCC_INSTALL_SPEC="cocoindex-code[default]"
COPY . /ccc-src

RUN uv pip install --system --prerelease=allow \
    "cocoindex>=1.0.0a33" \
    "${CCC_INSTALL_SPEC}"

# ─── Stage 2: pre-bake the default embedding model ────────────────────────────
# Bakes Snowflake/snowflake-arctic-embed-xs into the merged data directory at
# /var/cocoindex/cache/..., so on first run Docker's volume copy-up populates
# the cocoindex-data volume with the model — no network fetch needed.
FROM builder AS model_cache

ENV HF_HOME=/var/cocoindex/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/var/cocoindex/cache/sentence-transformers

RUN mkdir -p /var/cocoindex/cache/huggingface /var/cocoindex/cache/sentence-transformers \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('Snowflake/snowflake-arctic-embed-xs'); print('Model cached.')"

# ─── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# gosu for privilege-drop (PUID/PGID pattern); create non-root coco user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 coco \
    && useradd -u 1000 -g 1000 -m coco

# Copy installed packages + pre-baked model from previous stages.
COPY --from=model_cache /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=model_cache /usr/local/bin/cocoindex-code /usr/local/bin/cocoindex-code
COPY --from=model_cache /usr/local/bin/ccc /usr/local/bin/ccc
COPY --from=model_cache /var/cocoindex/cache /var/cocoindex/cache

# Pre-create writable paths so the entrypoint's chown (under PUID) works even on
# a fresh container, and so the default root-uid path has them in place.
RUN mkdir -p /var/cocoindex/db /var/run/cocoindex_code \
    && chown -R coco:coco /var/cocoindex /var/run/cocoindex_code

WORKDIR /workspace

# ── Runtime defaults (all overridable via -e / --env) ─────────────────────────
#
# COCOINDEX_CODE_DIR — holds global_settings.yml on the bind mount so users can
#   edit it directly on the host.
# COCOINDEX_CODE_RUNTIME_DIR — keeps daemon.sock/pid/log on the container's
#   native filesystem (AF_UNIX sockets on bind mounts are unreliable on
#   Docker Desktop, and /var/run is the standard spot for ephemeral runtime
#   state — wiped on container recreate, no stale-socket risk).
# COCOINDEX_CODE_DB_PATH_MAPPING — keeps the indexer's LMDB + SQLite databases
#   on the native filesystem for speed and correctness.
# HF_HOME / SENTENCE_TRANSFORMERS_HOME — direct the model cache at the path
#   the cocoindex-data volume mounts over.
ENV COCOINDEX_CODE_DIR=/workspace/.cocoindex_code \
    COCOINDEX_CODE_RUNTIME_DIR=/var/run/cocoindex_code \
    COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/var/cocoindex/db \
    COCOINDEX_CODE_DAEMON_SUPERVISED=1 \
    HF_HOME=/var/cocoindex/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/var/cocoindex/cache/sentence-transformers

# Set COCOINDEX_CODE_HOST_PATH_MAPPING at run time — it depends on the host path
# the user bind-mounts to /workspace and can't be baked into the image.

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
