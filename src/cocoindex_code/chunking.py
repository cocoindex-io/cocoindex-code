"""Public API for writing custom chunkers.

Example usage::

    from pathlib import Path
    from cocoindex_code.chunking import Chunk, ChunkerFn, TextPosition

    def my_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
        pos = TextPosition(byte_offset=0, char_offset=0, line=1, column=0)
        return "mylang", [Chunk(text=content, start=pos, end=pos)]
"""

from __future__ import annotations

import hashlib as _hashlib
import importlib as _importlib
import importlib.util as _importlib_util
import pathlib as _pathlib
import sys as _sys
from collections.abc import Callable as _Callable
from collections.abc import Iterator as _Iterator
from collections.abc import Sequence as _Sequence
from contextlib import contextmanager as _contextmanager
from typing import Protocol as _Protocol
from typing import cast as _cast

import cocoindex as _coco
from cocoindex.resources.chunk import Chunk, TextPosition

# Callable alias (not Protocol) — consistent with codebase style.
# language_override=None keeps the language detected by detect_code_language.
# path is not resolved (no syscall); call path.resolve() inside the chunker if needed.
ChunkerFn = _Callable[[_pathlib.Path, str], tuple[str | None, list[Chunk]]]

# tracked=False: callables are not fingerprint-able; daemon restart re-indexes anyway.
CHUNKER_REGISTRY = _coco.ContextKey[dict[str, ChunkerFn]]("chunker_registry")
_PERSISTENT_CHUNKER_IMPORT_PATHS: set[str] = set()
_PLUGIN_PACKAGE_PREFIX = "_cocoindex_code_plugins"


class _ChunkerMappingLike(_Protocol):
    ext: str
    module: str


@_contextmanager
def _temporary_import_paths(paths: _Sequence[_pathlib.Path]) -> _Iterator[None]:
    inserted: list[str] = []
    try:
        for path in paths:
            path_str = str(path)
            if path_str in _sys.path:
                continue
            _sys.path.insert(0, path_str)
            inserted.append(path_str)
        yield
    finally:
        for path_str in reversed(inserted):
            try:
                _sys.path.remove(path_str)
            except ValueError:
                pass


def _register_persistent_import_paths(paths: _Sequence[_pathlib.Path]) -> None:
    for path in paths:
        path_str = str(path)
        if path_str in _PERSISTENT_CHUNKER_IMPORT_PATHS:
            continue
        if path_str not in _sys.path:
            _sys.path.insert(0, path_str)
        _PERSISTENT_CHUNKER_IMPORT_PATHS.add(path_str)


def _ensure_plugin_package(package_name: str, package_path: _pathlib.Path) -> None:
    package = _sys.modules.get(package_name)
    if package is not None:
        return
    spec = _importlib_util.spec_from_loader(package_name, loader=None, is_package=True)
    if spec is None:
        raise ImportError(f"Could not create plugin package {package_name}")
    module = _importlib_util.module_from_spec(spec)
    module.__path__ = [str(package_path)]
    module.__package__ = package_name
    _sys.modules[package_name] = module


def _plugin_package_name(search_root: _pathlib.Path) -> str:
    digest = _hashlib.md5(str(search_root).encode("utf-8")).hexdigest()[:12]
    return f"{_PLUGIN_PACKAGE_PREFIX}.{search_root.name.replace('-', '_')}_{digest}"


def _load_project_chunker_module(
    module_path: str,
    search_root: _pathlib.Path,
) -> object | None:
    module_relpath = _pathlib.Path(*module_path.split(".")).with_suffix(".py")
    candidates = [
        search_root / module_relpath,
        search_root / "chunkers" / module_relpath,
    ]
    module_file = next((candidate for candidate in candidates if candidate.is_file()), None)
    if module_file is None:
        return None

    plugin_root = module_file.parent
    package_name = _plugin_package_name(plugin_root)
    _ensure_plugin_package(_PLUGIN_PACKAGE_PREFIX, plugin_root.parent)
    _ensure_plugin_package(package_name, plugin_root)
    spec_name = f"{package_name}.{module_relpath.stem}"
    spec = _importlib_util.spec_from_file_location(spec_name, module_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load chunker module from {module_file}")

    module = _importlib_util.module_from_spec(spec)
    _sys.modules[spec_name] = module
    _register_persistent_import_paths((module_file.parent, search_root, search_root / "chunkers"))
    try:
        with _temporary_import_paths((module_file.parent, search_root, search_root / "chunkers")):
            spec.loader.exec_module(module)
    except Exception:
        _sys.modules.pop(spec_name, None)
        raise
    return module


def _resolve_chunker_callable(
    module_path: str,
    attr: str,
    *,
    search_roots: _Sequence[_pathlib.Path] = (),
) -> ChunkerFn:
    for root in search_roots:
        mod = _load_project_chunker_module(module_path, root)
        if mod is None:
            continue
        fn = getattr(mod, attr)
        if not callable(fn):
            raise ValueError(f"chunker {module_path}:{attr!r}: {attr!r} is not callable")
        return _cast(ChunkerFn, fn)

    mod = _importlib.import_module(module_path)
    fn = getattr(mod, attr)
    if not callable(fn):
        raise ValueError(f"chunker {module_path}:{attr!r}: {attr!r} is not callable")
    return _cast(ChunkerFn, fn)


def resolve_chunker_registry(
    mappings: _Sequence[_ChunkerMappingLike],
    *,
    project_root: _pathlib.Path | None = None,
    shared_roots: _Sequence[_pathlib.Path] = (),
) -> dict[str, ChunkerFn]:
    """Resolve chunker mapping entries to a ``{".ext": fn}`` dict.

    Each ``mapping.module`` must be a ``"module.path:callable"`` string importable
    from the current environment.
    """
    registry: dict[str, ChunkerFn] = {}
    for cm in mappings:
        module_path, _, attr = cm.module.partition(":")
        if not attr:
            raise ValueError(f"chunker module {cm.module!r} must use 'module.path:callable' format")
        search_roots: list[_pathlib.Path] = []
        if project_root is not None:
            search_roots.append(project_root)
        search_roots.extend(shared_roots)
        try:
            fn = _resolve_chunker_callable(module_path, attr, search_roots=search_roots)
        except ModuleNotFoundError as exc:
            if (
                project_root is not None
                or shared_roots
                or "." in module_path
                or exc.name != module_path
            ):
                raise
            mod = _importlib.import_module(f"cocoindex_code.builtin_chunkers.{module_path}")
            fn = getattr(mod, attr)
        if not callable(fn):
            raise ValueError(f"chunker {cm.module!r}: {attr!r} is not callable")
        registry[f".{cm.ext}"] = fn
    return registry


__all__ = [
    "CHUNKER_REGISTRY",
    "Chunk",
    "ChunkerFn",
    "TextPosition",
    "resolve_chunker_registry",
]
