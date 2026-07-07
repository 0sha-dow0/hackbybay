from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from backend.domain.enums import GraphEdgeKind, GraphNodeKind
from backend.domain.errors import Err, GraphError, Ok, Result
from backend.domain.models import (
    CallSite,
    CentralityScore,
    FileContent,
    GraphEdge,
    GraphLayout,
    GraphNode,
    SurgeryPlan,
)
from backend.ports.graph_store import GraphStore, call_site_to_node_attrs
from backend.services.manifest_parser import DependencyEntry

__all__ = ("GraphBuilder",)

_PACKAGE_ID_PREFIX: Final = "package"
_FILE_ID_PREFIX: Final = "file"
_CALL_SITE_ID_PREFIX: Final = "call_site"
_ID_SEPARATOR: Final = ":"


def _package_id(label: str) -> str:
    return f"{_PACKAGE_ID_PREFIX}{_ID_SEPARATOR}{label}"


def _file_id(path: str) -> str:
    return f"{_FILE_ID_PREFIX}{_ID_SEPARATOR}{path}"


def _call_site_id(index: int) -> str:
    return f"{_CALL_SITE_ID_PREFIX}{_ID_SEPARATOR}{index}"


def _call_site_key(call_site: CallSite) -> tuple[str, int, str, bool, bool, str, str]:
    return (
        call_site.file_path,
        call_site.line,
        call_site.symbol,
        call_site.is_aliased,
        call_site.alias is None,
        call_site.alias or "",
        call_site.snippet,
    )


def _edge_key(edge: GraphEdge) -> tuple[str, str, str]:
    return (edge.src, edge.dst, edge.kind.value)


def _package_labels(
    deps: Sequence[DependencyEntry], target_package: str
) -> tuple[str, ...]:
    labels: list[str] = [target_package]
    seen: set[str] = {target_package}
    for dep in sorted(deps, key=lambda entry: entry.name):
        if dep.name not in seen:
            seen.add(dep.name)
            labels.append(dep.name)
    return tuple(labels)


def _file_paths(
    files: Sequence[FileContent], call_sites: Sequence[CallSite]
) -> tuple[str, ...]:
    paths: set[str] = {file.path for file in files}
    paths.update(call_site.file_path for call_site in call_sites)
    return tuple(sorted(paths))


def _nodes(
    deps: Sequence[DependencyEntry],
    files: Sequence[FileContent],
    call_sites: Sequence[CallSite],
    target_package: str,
) -> tuple[GraphNode, ...]:
    nodes: list[GraphNode] = [
        GraphNode(
            id=_package_id(label),
            kind=GraphNodeKind.PACKAGE,
            label=label,
            attrs={},
        )
        for label in _package_labels(deps, target_package)
    ]
    nodes.extend(
        GraphNode(
            id=_file_id(path),
            kind=GraphNodeKind.FILE,
            label=path,
            attrs={},
        )
        for path in _file_paths(files, call_sites)
    )
    nodes.extend(
        GraphNode(
            id=_call_site_id(index),
            kind=GraphNodeKind.CALL_SITE,
            label=call_site.symbol,
            attrs=call_site_to_node_attrs(call_site),
        )
        for index, call_site in enumerate(call_sites)
    )
    return tuple(nodes)


def _edges(
    files: Sequence[FileContent],
    call_sites: Sequence[CallSite],
    target_package: str,
) -> tuple[GraphEdge, ...]:
    target_id = _package_id(target_package)
    edges: list[GraphEdge] = []
    for path in sorted({call_site.file_path for call_site in call_sites}):
        edges.append(
            GraphEdge(src=target_id, dst=_file_id(path), kind=GraphEdgeKind.IMPORTS)
        )
    for index, call_site in enumerate(call_sites):
        edges.append(
            GraphEdge(
                src=_file_id(call_site.file_path),
                dst=_call_site_id(index),
                kind=GraphEdgeKind.CALLS,
            )
        )
    for path in _file_paths(files, call_sites):
        edges.append(
            GraphEdge(
                src=_file_id(path), dst=target_id, kind=GraphEdgeKind.DEPENDS_ON
            )
        )
    return tuple(sorted(edges, key=_edge_key))


def _affected_files(call_sites: Sequence[CallSite]) -> tuple[str, ...]:
    return tuple(sorted({call_site.file_path for call_site in call_sites}))


class GraphBuilder:
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    def build(
        self,
        deps: Sequence[DependencyEntry],
        files: Sequence[FileContent],
        call_sites: Sequence[CallSite],
        target_package: str,
    ) -> Result[
        tuple[SurgeryPlan, tuple[CentralityScore, ...], GraphLayout], GraphError
    ]:
        reset_result = self._store.reset()
        if isinstance(reset_result, Err):
            return reset_result

        unique_call_sites = tuple(sorted(set(call_sites), key=_call_site_key))
        nodes = _nodes(deps, files, unique_call_sites, target_package)
        edges = _edges(files, unique_call_sites, target_package)

        load_result = self._store.load(nodes, edges)
        if isinstance(load_result, Err):
            return load_result

        traverse_result = self._store.traverse_call_sites(target_package)
        if isinstance(traverse_result, Err):
            return traverse_result

        centrality_result = self._store.centrality()
        if isinstance(centrality_result, Err):
            return centrality_result

        layout_result = self._store.layout()
        if isinstance(layout_result, Err):
            return layout_result

        plan = SurgeryPlan(
            target_package=target_package,
            call_sites=traverse_result.value,
            affected_files=_affected_files(traverse_result.value),
        )
        return Ok((plan, centrality_result.value, layout_result.value))
