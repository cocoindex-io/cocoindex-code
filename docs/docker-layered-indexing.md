# Docker Sidecar Layered Indexing

This guide covers the Docker-specific configuration for Git layered indexing. For the core model, see [Git Layered Indexing](./layered-indexing.md).

The intended Docker architecture is:

- one central daemon container with no source-code mount
- Docker named volumes for daemon state, runtime files, config, caches, and layer databases
- short-lived sidecar containers for repo work
- each sidecar mounts exactly one authorized Git checkout at `/workspace`
- sidecars talk to the central daemon over a private Docker network

Do not mount `$HOME` or a broad source tree just to make indexing work.

## Repo-Scoped Sample

Build the branch-local image:

```bash
cd sample
make build
```

Authorize one repo and register its base ref:

```bash
cd /path/to/repo
/path/to/cocoindex-code/sample/bin/ccc init --base main
```

Then index and search:

```bash
/path/to/cocoindex-code/sample/bin/ccc index
/path/to/cocoindex-code/sample/bin/ccc search "query planner"
/path/to/cocoindex-code/sample/bin/ccc overlay status
```

The wrapper refuses to run outside an authorized repo. Running `ccc init` from another repo authorizes that repo separately. Source access is granted only to the short-lived sidecar for that repo.

Linked worktrees must also be authorized explicitly:

```bash
cd /path/to/repo.worktrees/feature-1
/path/to/cocoindex-code/sample/bin/ccc init --base main
/path/to/cocoindex-code/sample/bin/ccc index
```

When linked worktrees share the same Git common directory, they can share daemon layer state while each sidecar still mounts only the initialized checkout.

## What Runs Where

Central daemon container:

```text
mounts:
  $HOME/.cocoindex_code          -> /home/coco/.cocoindex_code
  cocoindex-code-local-state   -> /var/cocoindex
  cocoindex-code-local-runtime -> /var/run/cocoindex_code
network:
  cocoindex-code-local
listens:
  COCOINDEX_CODE_DAEMON_TCP=0.0.0.0:8765
source access:
  none
```

Sidecar container:

```text
mounts:
  /authorized/repo             -> /workspace
  $HOME/.cocoindex_code        -> /home/coco/.cocoindex_code
  cocoindex-code-local-state   -> /var/cocoindex
  cocoindex-code-local-runtime -> /var/run/cocoindex_code
network:
  cocoindex-code-local
connects:
  COCOINDEX_CODE_DAEMON_TCP=cocoindex-code-local-daemon:8765
source access:
  only the authorized repo
```

Indexing runs in the sidecar because it is the process with Git/source access. The resulting layer metadata and layer databases are written to shared daemon state. Search sends the resolved layer IDs to the central daemon, and the daemon serves the query from shared layer databases without mounting the repository.

## State

Host-side sample metadata:

```text
sample/data/authorized-repos.tsv
```

Docker named volumes:

| Volume | Mounted As | Purpose |
|---|---|---|
| `cocoindex-code-local-state` | `/var/cocoindex` | Global settings, daemon DB, layer metadata, layer DBs, caches |
| `cocoindex-code-local-runtime` | `/var/run/cocoindex_code` | PID/log runtime files |

Host user settings:

| Host Path | Mounted As | Purpose |
|---|---|---|
| `${COCOINDEX_CODE_HOST_SETTINGS_DIR:-$HOME/.cocoindex_code}` | `/home/coco/.cocoindex_code` | Global `ccc` settings shared with the Docker daemon and sidecars |

Reset sample Docker state:

```bash
cd sample
make reset
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `COCOINDEX_CODE_IMAGE` | Image used for central daemon and sidecars. Default: `cocoindex-code:local-layered`. |
| `COCOINDEX_CODE_DAEMON_CONTAINER` | Central daemon container name. Default: `cocoindex-code-local-daemon`. |
| `COCOINDEX_CODE_DOCKER_NETWORK` | Private Docker network. Default: `cocoindex-code-local`. |
| `COCOINDEX_CODE_STATE_VOLUME` | Shared daemon state named volume. Default: `cocoindex-code-local-state`. |
| `COCOINDEX_CODE_RUNTIME_VOLUME` | Shared runtime named volume. Default: `cocoindex-code-local-runtime`. |
| `COCOINDEX_CODE_HOST_SETTINGS_DIR` | Host user settings directory mounted into daemon and sidecars. Default: `$HOME/.cocoindex_code`. |
| `COCOINDEX_CODE_SAMPLE_DATA_DIR` | Host-side allowlist directory. Default: `sample/data`. |
| `PUID`, `PGID` | Linux-only ownership mapping. |

Internal sidecar/daemon variables:

| Variable | Purpose |
|---|---|
| `COCOINDEX_CODE_SIDECAR=1` | Tells CLI to run repo-mounted indexing locally in the sidecar. |
| `COCOINDEX_CODE_DAEMON_TCP` | TCP daemon address. Central listens on `0.0.0.0:8765`; sidecars connect to the daemon container name. |
| `COCOINDEX_CODE_DIR=/home/coco/.cocoindex_code` | Container path for the host-mounted global settings directory. |
| `COCOINDEX_CODE_STATE_DIR=/var/cocoindex/state` | Durable daemon layer state. |
| `COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/var/cocoindex/db` | Keeps layer/project databases on Docker native storage. |

## Debugging

Check the central daemon:

```bash
cd sample
make ps
make logs
```

Check through a repo-authorized sidecar:

```bash
cd /path/to/repo
/path/to/cocoindex-code/sample/bin/ccc daemon status
/path/to/cocoindex-code/sample/bin/ccc overlay status
```

Inspect named volume contents:

```bash
docker run --rm -it \
  -v cocoindex-code-local-state:/var/cocoindex \
  cocoindex-code:local-layered sh
```
