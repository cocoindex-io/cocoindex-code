from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from cocoindex_code.project import Project
from cocoindex_code.protocol import IndexingProgress
from cocoindex_code.shared import Embedder

from .layer import Layer


class LayerRuntime:
    """CocoIndex adapter for one immutable overlay layer."""

    def __init__(
        self,
        *,
        layer: Layer,
        project: Project,
        environment_strategy: str = "per-layer",
    ) -> None:
        self.layer = layer
        self.project = project
        self.environment_strategy = environment_strategy

    @classmethod
    async def create(
        cls,
        *,
        layer: Layer,
        project_root: Path,
        embedder: Embedder,
        indexing_params: dict[str, Any],
        query_params: dict[str, Any],
        chunker_registry: dict[str, Any],
        project_cache: dict[str, Project],
    ) -> LayerRuntime:
        cached_project = project_cache.get(layer.id)
        if cached_project is None:
            cached_project = await Project.create(
                project_root,
                embedder,
                indexing_params=indexing_params,
                query_params=query_params,
                chunker_registry=chunker_registry,
                source_root=layer.paths.source,
                db_dir=layer.paths.db_dir,
            )
            project_cache[layer.id] = cached_project
        return cls(layer=layer, project=cached_project)

    async def run_index(
        self, on_progress: Callable[[IndexingProgress], None] | None = None
    ) -> None:
        await self.project.run_index(on_progress=on_progress)
