# ccc Management

## Installation

Install CocoIndex Code via pipx:

```bash
pipx install cocoindex-code
```

To upgrade to the latest version:

```bash
pipx upgrade cocoindex-code
```

After installation, the `ccc` command is available globally.

## Project Initialization

Run from the root directory of the project to index:

```bash
ccc init
```

This creates:
- `~/.cocoindex_code/global_settings.yml` (user-level settings, e.g., model configuration) if it does not already exist.
- `.cocoindex_code/settings.yml` (project-level settings, e.g., include/exclude file patterns).

If `.git` exists in the directory, `.cocoindex_code/` is automatically added to `.gitignore`.

Use `-f` to skip the confirmation prompt if `ccc init` detects a potential parent project root.

After initialization, edit the settings files if needed (see [settings.md](settings.md) for format details), then run `ccc index` to build the initial index.

## Troubleshooting

### Diagnostics

Run `ccc doctor` to check system health end-to-end:

```bash
ccc doctor
```

This checks global settings, daemon status, embedding model (runs a test embedding), and — if run from within a project — file matching (walks files using the same logic as the indexer) and index status. Results stream incrementally. Always points to `daemon.log` at the end for further investigation.

### Checking Project Status

To view the current project's index status:

```bash
ccc status
```

This shows whether indexing is ongoing and index statistics.

### Daemon Management

The daemon starts automatically on first use. To check its status:

```bash
ccc daemon status
```

This shows whether the daemon is running, its version, uptime, and loaded projects.

To restart the daemon (useful if it gets into a bad state):

```bash
ccc daemon restart
```

To stop the daemon:

```bash
ccc daemon stop
```

## Cleanup

To reset a project's index (removes databases, keeps settings):

```bash
ccc reset
```

To fully remove all CocoIndex Code data for a project (including settings):

```bash
ccc reset --all
```

Both commands prompt for confirmation. Use `-f` to skip.
