# Docker Layered Indexing

This guide covers the Docker-specific configuration for Git layered indexing. For the core model, see [Git Layered Indexing](./layered-indexing.md).

## Recommended Compose Setup

Use the repository compose file:

```bash
docker compose -f docker/docker-compose.yml up -d
```

The compose defaults are designed for layered indexing:

```yaml
COCOINDEX_CODE_STATE_DIR: /var/cocoindex/state
COCOINDEX_CODE_RUNTIME_DIR: /var/run/cocoindex_code
COCOINDEX_CODE_DB_PATH_MAPPING: /workspace=/var/cocoindex/db
COCOINDEX_CODE_HOST_PATH_MAPPING: /workspace=$HOME
```

The important split is:

- source code and settings live on the bind mount under `/workspace`
- durable daemon layer metadata lives under `/var/cocoindex/state`
- per-project non-layer DB paths are remapped to `/var/cocoindex/db`
- sockets, PID files, and logs stay under `/var/run/cocoindex_code`

## Mount the Right Workspace

The default compose file mounts your home directory:

```bash
COCOINDEX_HOST_WORKSPACE=$HOME docker compose -f docker/docker-compose.yml up -d
```

For a narrower mount, point it at the parent containing both the root clone and linked worktrees:

```bash
COCOINDEX_HOST_WORKSPACE=$HOME/src/github/cocoindex-io \
  docker compose -f docker/docker-compose.yml up -d
```

Example host layout:

```text
$HOME/src/github/cocoindex-io/
  cocoindex-code/
  cocoindex-code.worktrees/
    feature-1/
```

Both paths must be visible inside the same container mount for the daemon to reuse repository and layer state across them.

## Host Wrapper

Use this wrapper so Docker commands resolve the host current directory correctly:

```bash
ccc() {
  local container="${COCOINDEX_CODE_CONTAINER_NAME:-cocoindex-code}"
  if [ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)" != "true" ]; then
    echo "cocoindex-code container is not running. Start it with: docker compose -f docker/docker-compose.yml up -d" >&2
    return 1
  fi

  local flags=(-i)
  if [ "${1:-}" != "mcp" ] && [ -t 0 ] && [ -t 1 ]; then
    flags=(-it)
  fi

  docker exec "${flags[@]}" \
    -e COCOINDEX_CODE_HOST_CWD="$PWD" \
    "$container" ccc "$@"
}
```

`COCOINDEX_CODE_HOST_CWD` is required for linked worktrees. It tells the container-side CLI which host directory you are actually in, then the path mapping translates it to `/workspace/...`.

## Layered Workflow in Docker

Root clone:

```bash
cd $HOME/src/github/cocoindex-io/cocoindex-code
ccc init --base main
ccc index
```

Linked worktree:

```bash
git worktree add ../cocoindex-code.worktrees/feature-1 -b feature-1 main
cd ../cocoindex-code.worktrees/feature-1
ccc index
ccc search "query planner"
ccc overlay status
```

The base layer is stored once under `/var/cocoindex/state` and reused by the linked worktree.

## Environment Variables

| Variable | Purpose |
|---|---|
| `COCOINDEX_CODE_IMAGE` | Image used by compose, e.g. `cocoindex/cocoindex-code:full`. |
| `COCOINDEX_CODE_CONTAINER_NAME` | Container name used by compose and the wrapper. |
| `COCOINDEX_HOST_WORKSPACE` | Host directory mounted at `/workspace`. Mount a parent that contains all worktrees you want to share. |
| `COCOINDEX_CODE_HOST_PATH_MAPPING` | Container-to-host path mapping for display and host CWD translation. |
| `COCOINDEX_CODE_HOST_CWD` | Host current directory passed per `docker exec` invocation. |
| `COCOINDEX_CODE_STATE_DIR` | Durable daemon layer state. Default: `/var/cocoindex/state`. |
| `COCOINDEX_CODE_RUNTIME_DIR` | Runtime socket/PID/log directory. Default: `/var/run/cocoindex_code`. |
| `COCOINDEX_CODE_DB_PATH_MAPPING` | Non-layer project DB remapping. Default: `/workspace=/var/cocoindex/db`. |
| `PUID`, `PGID` | Linux-only ownership mapping for bind-mounted files and Docker-managed state. |

## Debugging

Check daemon status:

```bash
docker exec cocoindex-code ccc daemon status
```

Inspect overlay status from the current host directory:

```bash
ccc overlay status
```

Inspect state in the container:

```bash
docker exec -it cocoindex-code sh
ls -R /var/cocoindex/state
```

Reset all Docker-managed index, layer, and cache state:

```bash
docker compose -f docker/docker-compose.yml down -v
```

This preserves your source tree because it is bind-mounted from the host.
