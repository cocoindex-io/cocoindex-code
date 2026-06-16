This is built on top of [CocoIndex v1](https://cocoindex.io/docs-v1/llms.txt).


## Build and Test Commands

This project uses [uv](https://docs.astral.sh/uv/) for project management.

```bash
uv run mypy .           # Type check Python code
uv run pytest tests/    # Run Python tests
```

## Code Conventions

### Internal vs External Modules

We distinguish between **internal modules** (under packages with `_` prefix, e.g. `_internal.*` or `connectors.*._source`) and **external modules** (which users can directly import).

**External modules** (user-facing, e.g. `cocoindex/ops/sentence_transformers.py`):

* Be strict about not leaking implementation details
* Use `__all__` to explicitly list public exports
* Prefix ALL non-public symbols with `_`, including:
  * Standard library imports: `import threading as _threading`, `import typing as _typing`
  * Third-party imports: `import numpy as _np`, `from numpy.typing import NDArray as _NDArray`
  * Internal package imports: `from cocoindex.resources import schema as _schema`
* Exception: `TYPE_CHECKING` imports for type hints don't need prefixing

**Internal modules** (e.g. `cocoindex/_internal/component_ctx.py`):

* Less strict since users shouldn't import these directly
* Standard library and internal imports don't need underscore prefix
* Only prefix symbols that are truly private to the module itself (e.g. `_context_var` for a module-private ContextVar)

### General principles (also covered by `/review-changes`)

- **Top-level imports.** Defer to in-function only for a real circular dependency or a heavy import that isn't always needed.
- **Specific types over `Any`.** When a value enters as a weaker form (`str`, `Any`), convert to the strong type at the earliest point. Don't propagate the weak form.
- **`NamedTuple`/small dataclass for multi-value returns.** Access fields by name at call sites.
- **Single source of truth.** When the same value or logic appears in multiple places, consolidate it.
- **Delete dead code and dead config.** When a change makes something unreachable, the code, the tests, and the knobs all go.
- **Honest names.** The name describes what the code does today.

### Testing Guidelines

We prefer end-to-end tests on user-facing APIs, over unit tests on smaller internal functions. With this said, there're cases where unit tests are necessary, e.g. for internal logic with various situations and edge cases, in which case it's usually easier to cover various scenarios with unit tests.

When tests fail, fix the underlying issue. Don't skip, ignore, or exclude to get a green result.
