# ─── Stage 1: heavy stable dependencies (variant-aware) ──────────────────────
# Two image variants are published from this Dockerfile:
#   - slim (default, `:latest`) — ~450 MB. cocoindex-code + LiteLLM only.
#     For users who'll point the embedding at a cloud provider (OpenAI,
#     Voyage, Gemini, …).
#   - full (`:full`)             — ~5 GB. Also bundles sentence-transformers
#     + torch + a pre-baked default model. For users who want offline-ready
#     local embeddings without an API key.
#
# This stage installs only the big, slow-changing deps that are shared across
# releases:
#   - full: `sentence-transformers` (pulls torch + transformers + tokenizers
#     transitively, ~1 GB of wheels).
#   - slim: nothing — cocoindex-code's LiteLLM deps get installed in stage 2.
#
# The cache key is the RUN command string, which changes with CCC_VARIANT, so
# BuildKit keeps separate cache entries per variant and reuses each across
# releases until we bump the deps.
#
# `cocoindex` and `cocoindex-code` are deliberately NOT installed here —
# they bump often, so pinning them at this layer would invalidate the heavy
# cache on every release. Stage 2 installs them on top; transitive deps are
# already satisfied, so uv only fetches the two packages themselves.
#
# Use slim (glibc-based) — cocoindex ships pre-built Rust wheels that need glibc.
# Alpine / musl-libc would require building from source.
#
# `--system` tells uv to install into the base Python at
# /usr/local/lib/python3.12/... since there's no virtualenv in the image.
FROM python:3.12-slim AS deps

RUN pip install --quiet uv

ARG CCC_VARIANT=slim
RUN if [ "$CCC_VARIANT" = "full" ]; then \
        uv pip install --system --prerelease=allow sentence-transformers; \
    fi

# ─── Stage 2: install cocoindex + cocoindex-code (per release) ───────────────
# Cheap relative to stage 1: transitive deps like torch are already in place
# for the full variant; for slim there are no heavy deps to pull. uv only
# needs to fetch the cocoindex + cocoindex-code wheels themselves.
FROM deps AS builder
WORKDIR /build
ARG CCC_VARIANT=slim

# Default behaviour: install cocoindex-code from PyPI, picking the extras
# that match CCC_VARIANT.
# Release workflow / local tests override with (respectively):
#   --build-arg CCC_INSTALL_SPEC=/ccc-src
#   --build-arg CCC_INSTALL_SPEC=/ccc-src[full]
ARG CCC_INSTALL_SPEC=""
COPY . /ccc-src
RUN if [ -z "$CCC_INSTALL_SPEC" ]; then \
        if [ "$CCC_VARIANT" = "full" ]; then \
            CCC_INSTALL_SPEC="cocoindex-code[full]"; \
        else \
            CCC_INSTALL_SPEC="cocoindex-code"; \
        fi; \
    fi; \
    uv pip install --system --prerelease=allow \
        "cocoindex>=1.0.0a33" \
        "${CCC_INSTALL_SPEC}"

# ─── Stage 3: pre-bake the default embedding model (full only) ───────────────
# For the full variant, bakes Snowflake/snowflake-arctic-embed-xs into
# /var/cocoindex/cache/... so Docker's first-mount copy-up populates the
# cocoindex-data volume with the model — no network fetch on first start.
# For slim, just creates empty cache dirs so the runtime stage's COPY works
# regardless of variant.
FROM builder AS model_cache
ARG CCC_VARIANT=slim

ENV HF_HOME=/var/cocoindex/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/var/cocoindex/cache/sentence-transformers

RUN mkdir -p /var/cocoindex/cache/huggingface /var/cocoindex/cache/sentence-transformers \
    && if [ "$CCC_VARIANT" = "full" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('Snowflake/snowflake-arctic-embed-xs'); print('Model cached.')"; \
    fi

# ─── Stage 4: runtime ─────────────────────────────────────────────────────────
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
