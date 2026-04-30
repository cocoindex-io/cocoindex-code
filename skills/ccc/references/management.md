# ccc Management

This reference covers installation, initialization, daemon operations, validation, and cleanup.

## Installation

Install CocoIndex Code via pipx. Two install styles:

```bash
pipx install 'cocoindex-code[full]'      # batteries included (local embeddings via sentence-transformers)
pipx install cocoindex-code              # slim (LiteLLM-only; requires a cloud embedding provider + API key)
```

The `[full]` extra pulls in `sentence-transformers` so the first-run default (local embeddings, no API key) works out of the box. The slim install is for environments where you don't want the torch/transformers deps and plan to use a LiteLLM-supported cloud provider instead.

To upgrade to the latest version:

```bash
pipx upgrade cocoindex-code
```

After installation, the `ccc` command is available globally.

If you want host-specific MCP registration help after install:

```bash
ccc install
ccc install --apply
```

For the best Claude Code / Codex setup, use both surfaces:

```bash
cgrep "your query"          # shell-native local search
ccc install --apply         # MCP registration for Claude Code / Codex
```

Use `cgrep` when you want quick local search results in the terminal. Use MCP when the agent needs richer repository-native tools such as `codebase_search`, `codebase_symbol`, `codebase_impact`, or `codebase_workflow`.

## Project Initialization

Run from the root directory of the project to index:

```bash
ccc init
```

For an umbrella workspace that contains multiple sibling repos, treat the umbrella directory as the project root and keep one root-level `.cocoindex_code/settings.yml`. If a host wrapper or container shell needs the root pinned explicitly, set:

```bash
COCOINDEX_CODE_ROOT_PATH=/path/to/workspace
```

Then run `ccc` normally from that workspace context. A custom `setup.sh` or `watch.sh` layer is not required for this pattern.

**First run (global settings don't exist yet)** — `ccc init` prompts interactively for the embedding provider (sentence-transformers / litellm) and model, then runs a one-off test embed via the daemon to confirm the model works. Accept the defaults for the sentence-transformers path, or pick litellm and enter a model identifier.

**Subsequent runs** (global settings already exist) — prompts are skipped; only project settings and `.gitignore` are set up.

To skip the interactive prompts on the first run (e.g. in a script or container), pass `--litellm-model MODEL`:

```bash
ccc init --litellm-model openai/text-embedding-3-small
```

This is also the only way to pick a LiteLLM model when stdin isn't a TTY and you've done a slim install.

`ccc init` creates:
- `~/.cocoindex_code/global_settings.yml` (user-level, embedding config + env vars).
- `.cocoindex_code/settings.yml` (project-level, include/exclude patterns).

If `.git` exists in the directory, `.cocoindex_code/` is automatically added to `.gitignore`.

Use `-f` to skip the confirmation prompt if `ccc init` detects a potential parent project root.

After initialization, edit the settings files if needed (see [settings.md](settings.md) for format details), then run `ccc index` to build the initial index. If the model test printed `[FAIL]` during `init`, edit `global_settings.yml` (and optionally add API keys under the commented `envs:` block) and verify with `ccc doctor` before indexing.

## Common Daily Commands

```bash
ccc index
ccc search "authentication middleware"
ccc codebase workflow review --ref-spec HEAD~3..HEAD
ccc codebase workflow onboard
ccc codebase graph visualize --format html --output graph.html
```

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

To inspect the codebase intelligence layer specifically:

```bash
ccc codebase status
```

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

If you need to remove only the loaded project from the daemon but keep the daemon alive, use the MCP `codebase_remove` tool from an agent host.

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
