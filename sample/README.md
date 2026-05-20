# Repo-Scoped Docker Sample

This sample runs CocoIndex Code in Docker without mounting your home directory or a broad source tree.

The wrapper grants access on demand:

1. `ccc init` must be run inside a Git repository.
2. The wrapper records that Git root as authorized.
3. It starts one central daemon container with only shared state/runtime volumes.
4. Each `ccc` invocation runs a short-lived sidecar with only that repository mounted at `/workspace`.
5. Later commands only run when your current directory is inside an authorized repo.

Build the image from this branch:

```bash
cd sample
make build
```

`make build` is the local-source build and is equivalent to `make build-local`.
Use `make build-pypi` to build the image using the package install path instead.
Set `CCC_VARIANT=full` if you want the full image with local embedding support:

```bash
CCC_VARIANT=full make build-local
```

Initialize and authorize one repo:

```bash
cd /path/to/repo
/path/to/cocoindex-code/sample/bin/ccc init --base main
```

Index and search from the same repo:

```bash
/path/to/cocoindex-code/sample/bin/ccc index
/path/to/cocoindex-code/sample/bin/ccc search "query"
/path/to/cocoindex-code/sample/bin/ccc overlay status
```

Install the wrapper globally if desired:

```bash
cd /path/to/cocoindex-code/sample
make install-ccc-wrapper
```

Then use it as:

```bash
cd /path/to/repo
ccc init --base main
ccc index
```

Linked worktrees must be authorized separately by running `ccc init` from that worktree. They share layer state when they share the same Git common directory, but each sidecar only receives access to the worktree you initialized.

```bash
cd /path/to/repo.worktrees/feature-1
ccc init --base main
ccc index
```

State is stored under `sample/data/`:

- `authorized-repos.tsv`: host-side allowlist written by the wrapper

Shared Docker state uses named volumes:

- `cocoindex-code-local-state`: central daemon layer/index/config state mounted at `/var/cocoindex`
- `cocoindex-code-local-runtime`: daemon PID/log runtime files mounted at `/var/run/cocoindex_code`

User settings are shared from your host account:

- `${COCOINDEX_CODE_HOST_SETTINGS_DIR:-$HOME/.cocoindex_code}` is mounted into both the daemon and sidecars
- inside containers it is read as `COCOINDEX_CODE_DIR=/home/coco/.cocoindex_code`
- `ccc init` therefore writes global settings to your normal host path, for example `/Users/you/.cocoindex_code/global_settings.yml`

Useful overrides:

| Variable | Default |
|---|---|
| `COCOINDEX_CODE_IMAGE` | `cocoindex-code:local-layered` |
| `COCOINDEX_CODE_DAEMON_CONTAINER` | `cocoindex-code-local-daemon` |
| `COCOINDEX_CODE_DOCKER_NETWORK` | `cocoindex-code-local` |
| `COCOINDEX_CODE_HOST_SETTINGS_DIR` | `$HOME/.cocoindex_code` |
| `COCOINDEX_CODE_WORKSPACE_DIR` | `/workspace` |
| `COCOINDEX_CODE_CONTAINER_SETTINGS_DIR` | `/home/coco/.cocoindex_code` |
| `COCOINDEX_CODE_CONTAINER_STATE_ROOT` | `/var/cocoindex` |
| `COCOINDEX_CODE_RUNTIME_DIR` | `/var/run/cocoindex_code` |
| `COCOINDEX_CODE_DAEMON_PORT` | `8765` |
| `COCOINDEX_CODE_DAEMON_LISTEN` | `0.0.0.0:$COCOINDEX_CODE_DAEMON_PORT` |
| `COCOINDEX_CODE_DAEMON_CONNECT` | `$COCOINDEX_CODE_DAEMON_CONTAINER:$COCOINDEX_CODE_DAEMON_PORT` |

Sidecars talk to the central daemon over the private Docker network `cocoindex-code-local`. The daemon listens on `COCOINDEX_CODE_DAEMON_TCP=0.0.0.0:8765` inside that network; no host port is published.

Stop the central daemon container:

```bash
cd sample
make down
```
