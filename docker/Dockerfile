# Single-stage image with cache-friendly layer ordering so user `docker pull`s
# on upgrade only fetch the small per-release layer.
#
# Stable layers (reuse across releases — digest reproducible from the RUN
# command string + base image, so users keep them in local cache):
#   1. apt install gosu + create coco user
#   2. install uv
#   3. (full only) `uv pip install sentence-transformers` — ~1 GB of torch +
#      transformers. This is the heavy, slow-changing layer we're optimizing
#      around.
#   4. (full only) pre-bake the default embedding model under
#      /var/cocoindex/cache/... so the named volume's copy-up populates it
#      on first start without a network fetch.
#   5. writable-path setup (mkdir /var/cocoindex/db + /var/run/cocoindex_code,
#      chown to coco) + env vars + entrypoint copy.
#
# Per-release layers (invalidate when the source tree changes):
#   6. COPY . /ccc-src — build context (~MB).
#   7. `uv pip install "cocoindex>=..." "${CCC_INSTALL_SPEC}"` — installs
#      cocoindex + cocoindex-code + any of their deps not already in place
#      from layer 3. Per-release layer size is bounded by what cocoindex +
#      cocoindex-code + their non-ST deps actually occupy (~tens of MB).
#
# Two image variants are published per release:
#   - slim (default, `:latest`) — ~450 MB. Layer 3 is a no-op; cocoindex-code's
#     LiteLLM deps install in layer 7.
#   - full (`:full`)             — ~5 GB. Layer 3 + Layer 4 bundle torch +
#     sentence-transformers + a baked model for offline-ready local embeddings.
#
# Use slim (glibc-based) — cocoindex ships pre-built Rust wheels that need glibc.
# Alpine / musl-libc would require building from source.
#
# `--system` tells uv to install into the base Python at
# /usr/local/lib/python3.12/... since there's no virtualenv in the image.

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 coco \
    && useradd -u 1000 -g 1000 -m coco

RUN pip install --quiet uv

# Heavy, stable deps for full variant. Layer digest is reproducible across
# releases (RUN command string is constant), so users skip re-downloading
# this layer on upgrade.
ARG CCC_VARIANT=slim
RUN if [ "$CCC_VARIANT" = "full" ]; then \
        uv pip install --system sentence-transformers; \
    fi

ENV HF_HOME=/var/cocoindex/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/var/cocoindex/cache/sentence-transformers

# Pre-bake the default embedding model (full only). For slim, just create
# empty cache dirs so the cocoindex-data named volume mounts cleanly.
RUN mkdir -p /var/cocoindex/cache/huggingface /var/cocoindex/cache/sentence-transformers \
    && if [ "$CCC_VARIANT" = "full" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('Snowflake/snowflake-arctic-embed-xs'); print('Model cached.')"; \
    fi

# Writable paths the daemon needs, pre-chowned to coco. Under PUID/PGID the
# entrypoint re-chowns to the host user; under root (Docker Desktop
# default) coco-ownership is harmless since processes run as root and can
# write anywhere.
RUN mkdir -p /var/cocoindex/db /var/run/cocoindex_code \
    && chown -R coco:coco /var/cocoindex /var/run/cocoindex_code

WORKDIR /workspace

# Runtime defaults — see the spec for what each does. All overridable at
# `docker run -e ...` time.
ENV COCOINDEX_CODE_DIR=/workspace/.cocoindex_code \
    COCOINDEX_CODE_RUNTIME_DIR=/var/run/cocoindex_code \
    COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/var/cocoindex/db \
    COCOINDEX_CODE_DAEMON_SUPERVISED=1

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# ─── Per-release layer (last so only this one invalidates per release) ─────
#
# Default (PyPI flow): install cocoindex-code from PyPI, picking the extras
# that match CCC_VARIANT.
# Release workflow / local tests override with (respectively):
#   --build-arg CCC_INSTALL_SPEC=/ccc-src
#   --build-arg CCC_INSTALL_SPEC=/ccc-src[full]
# to install from the source tree. `rw=true` on the bind mount gives
# hatch-vcs a writable overlay for `_version.py` during the PEP 517 build;
# the overlay is discarded after the RUN, so the source tree doesn't
# persist as a layer in the final image.
ARG CCC_INSTALL_SPEC=""
RUN --mount=type=bind,source=.,target=/ccc-src,rw=true \
    if [ -z "$CCC_INSTALL_SPEC" ]; then \
        if [ "$CCC_VARIANT" = "full" ]; then \
            CCC_INSTALL_SPEC="cocoindex-code[full]"; \
        else \
            CCC_INSTALL_SPEC="cocoindex-code"; \
        fi; \
    fi; \
    uv pip install --system "${CCC_INSTALL_SPEC}"
