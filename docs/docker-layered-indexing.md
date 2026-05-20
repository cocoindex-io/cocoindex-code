# Docker Sidecar Layered Indexing

This guide covers the Docker-specific configuration for Git layered indexing. For the core model, see [Git Layered Indexing](./layered-indexing.md).

The intended Docker architecture is:

- one central daemon container with no source-code mount
- Docker named volumes for daemon state, runtime files, config, caches, and layer databases
- short-lived sidecar containers for repo work
- each sidecar mounts exactly one authorized Git checkout at `/workspace` and at
  the same absolute path it has on the host, so libgit2 can resolve
  linked-worktree metadata without exposing a broader source tree
- sidecars talk to the central daemon over a private Docker network

Do not mount `$HOME` or a broad source tree just to make indexing work.

## Repo-Scoped Wrapper

Build the branch-local image:

```bash
cd /path/to/cocoindex-code
make build
```

Install the wrapper as `ccc`:

```bash
make install-ccc-wrapper
```

Or run it directly from the checkout with `/path/to/cocoindex-code/bin/ccc`.

Authorize one repo and register its base ref:

```bash
cd /path/to/repo
ccc init --base main
```

Then index and search:

```bash
ccc index
ccc search "query planner"
ccc overlay status
```

The wrapper refuses to run outside an authorized repo. Running `ccc init` from another repo authorizes that repo separately. Source access is granted only to the short-lived sidecar for that repo.

Linked worktrees must also be authorized explicitly:

```bash
cd /path/to/repo.worktrees/feature-1
ccc init --base main
ccc index
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
name:
  cocoindex-code-sidecar-<repo>-<branch>-<command>-<hash>
labels:
  io.cocoindex.code.role=sidecar
  io.cocoindex.code.repo=<repo>
  io.cocoindex.code.branch=<branch>
  io.cocoindex.code.command=<command>
  io.cocoindex.code.worktree=<authorized repo path>
mounts:
  /authorized/repo             -> /workspace
  /authorized/repo             -> /authorized/repo
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

The second repo bind mount is the same authorized checkout, not a parent
directory. It exists so linked-worktree `.git` metadata that contains absolute
host paths still resolves inside the sidecar.

Sidecar names use lowercase Docker-safe slugs, so a repo/branch such as
`fever2` and `feature/PLATFORM-5958-example` appears in `docker ps` as a name
like `cocoindex-code-sidecar-fever2-feature-platform-5958-example-index-<hash>`.
The labels keep the unsanitized repo, branch, command, and worktree values for
inspection and filtering.

Indexing runs in the sidecar because it is the process with Git/source access. The resulting layer metadata and layer databases are written to shared daemon state. Search sends the resolved layer IDs to the central daemon, and the daemon serves the query from shared layer databases without mounting the repository.

## State

Host-side wrapper metadata:

```text
$HOME/.cocoindex_code/docker-sidecar/authorized-repos.tsv
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

Reset Docker state:

```bash
cd /path/to/cocoindex-code
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
| `COCOINDEX_CODE_WRAPPER_DATA_DIR` | Host-side allowlist directory. Default: `$HOME/.cocoindex_code/docker-sidecar`. |
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
cd /path/to/cocoindex-code
make ps
make logs
```

Check through a repo-authorized sidecar:

```bash
cd /path/to/repo
ccc daemon status
ccc overlay status
```

Inspect named volume contents:

```bash
docker run --rm -it \
  -v cocoindex-code-local-state:/var/cocoindex \
  cocoindex-code:local-layered sh
```
