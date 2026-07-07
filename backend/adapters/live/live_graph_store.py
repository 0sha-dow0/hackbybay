from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import DriverError, Neo4jError

from backend.domain.enums import GraphEdgeKind, GraphNodeKind
from backend.domain.errors import Err, GraphError, Ok, Result
from backend.domain.models import (
    CallSite,
    CentralityScore,
    GraphEdge,
    GraphLayout,
    GraphLayoutNode,
    GraphNode,
)
from backend.ports.graph_store import GraphStore, node_attrs_to_call_site

_DEFAULT_DATABASE: Final = "neo4j"

_LEVEL_X_SPACING: Final = 240.0
_INTRA_LEVEL_Y_SPACING: Final = 120.0

_PROP_ID: Final = "id"
_PROP_KIND: Final = "kind"
_PROP_LABEL: Final = "label"
_PROP_ATTR_KEYS: Final = "attr_keys"
_PROP_ATTR_VALUES: Final = "attr_values"
_PROP_SRC: Final = "src"
_PROP_DST: Final = "dst"
_COL_REL_TYPE: Final = "rel_type"

_OP_KEY: Final = "op"
_OP_RESET: Final = "reset"
_OP_LOAD: Final = "load"
_OP_CENTRALITY: Final = "centrality"
_OP_TRAVERSE: Final = "traverse_call_sites"
_OP_LAYOUT: Final = "layout"

_FIELD_KEY: Final = "field"

_NEO4J_FAILURE_MESSAGE: Final = "neo4j failure"
_MALFORMED_MESSAGE: Final = "neo4j graph data is malformed"

_REL_TYPE_IMPORTS: Final = "IMPORTS"
_REL_TYPE_CALLS: Final = "CALLS"
_REL_TYPE_DEPENDS_ON: Final = "DEPENDS_ON"

_PACKAGE_KIND: Final = GraphNodeKind.PACKAGE.value
_CALL_SITE_KIND: Final = GraphNodeKind.CALL_SITE.value

_EDGE_KIND_BY_REL_TYPE: Final[Mapping[str, GraphEdgeKind]] = MappingProxyType(
    {
        _REL_TYPE_IMPORTS: GraphEdgeKind.IMPORTS,
        _REL_TYPE_CALLS: GraphEdgeKind.CALLS,
        _REL_TYPE_DEPENDS_ON: GraphEdgeKind.DEPENDS_ON,
    }
)

_DELETE_ALL_CYPHER: Final = "MATCH (n) DETACH DELETE n"

_CREATE_NODES_CYPHER: Final = """
UNWIND $nodes AS node
CREATE (n:GraphNode)
SET n.id = node.id,
    n.kind = node.kind,
    n.label = node.label,
    n.attr_keys = node.attr_keys,
    n.attr_values = node.attr_values
"""

_CREATE_IMPORTS_EDGES_CYPHER: Final = """
UNWIND $edges AS edge
MATCH (src:GraphNode {id: edge.src})
MATCH (dst:GraphNode {id: edge.dst})
CREATE (src)-[:IMPORTS]->(dst)
"""

_CREATE_CALLS_EDGES_CYPHER: Final = """
UNWIND $edges AS edge
MATCH (src:GraphNode {id: edge.src})
MATCH (dst:GraphNode {id: edge.dst})
CREATE (src)-[:CALLS]->(dst)
"""

_CREATE_DEPENDS_ON_EDGES_CYPHER: Final = """
UNWIND $edges AS edge
MATCH (src:GraphNode {id: edge.src})
MATCH (dst:GraphNode {id: edge.dst})
CREATE (src)-[:DEPENDS_ON]->(dst)
"""

_READ_NODES_CYPHER: Final = """
MATCH (n:GraphNode)
RETURN n.id AS id,
       n.kind AS kind,
       n.label AS label,
       n.attr_keys AS attr_keys,
       n.attr_values AS attr_values
"""

_READ_EDGES_CYPHER: Final = """
MATCH (src:GraphNode)-[rel]->(dst:GraphNode)
RETURN src.id AS src, dst.id AS dst, type(rel) AS rel_type
"""

_FIND_PACKAGE_ROOTS_CYPHER: Final = """
MATCH (p:GraphNode {kind: $package_kind, label: $target})
RETURN p.id AS id
"""

_TRAVERSE_CALL_SITES_CYPHER: Final = """
MATCH (root:GraphNode {id: $root_id})-[:IMPORTS|CALLS*1..]->(cs:GraphNode)
WHERE cs.kind = $call_site_kind
RETURN DISTINCT cs.attr_keys AS attr_keys, cs.attr_values AS attr_values
"""


class _MalformedGraphDataError(Exception):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field: str = field


@dataclass(frozen=True)
class _TraverseRead:
    root_count: int
    attr_maps: tuple[Mapping[str, str], ...]


def _require_str(value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    raise _MalformedGraphDataError(field)


def _require_str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise _MalformedGraphDataError(field)
    return [_require_str(item, field) for item in value]


def _zip_attrs(keys: list[str], values: list[str], field: str) -> dict[str, str]:
    if len(keys) != len(values):
        raise _MalformedGraphDataError(field)
    return dict(zip(keys, values, strict=True))


def _node_kind_from_value(value: str) -> GraphNodeKind:
    try:
        return GraphNodeKind(value)
    except ValueError as error:
        raise _MalformedGraphDataError(_PROP_KIND) from error


def _edge_kind_from_rel_type(rel_type: str) -> GraphEdgeKind:
    kind = _EDGE_KIND_BY_REL_TYPE.get(rel_type)
    if kind is None:
        raise _MalformedGraphDataError(_COL_REL_TYPE)
    return kind


def _edge_sort_key(edge: GraphEdge) -> tuple[str, str, str]:
    return (edge.src, edge.dst, edge.kind.value)


def _call_site_sort_key(
    call_site: CallSite,
) -> tuple[str, int, str, bool, bool, str, str]:
    return (
        call_site.file_path,
        call_site.line,
        call_site.symbol,
        call_site.is_aliased,
        call_site.alias is None,
        call_site.alias or "",
        call_site.snippet,
    )


def _node_payload(node: GraphNode) -> dict[str, str | list[str]]:
    items = list(node.attrs.items())
    payload: dict[str, str | list[str]] = {
        _PROP_ID: node.id,
        _PROP_KIND: node.kind.value,
        _PROP_LABEL: node.label,
        _PROP_ATTR_KEYS: [key for key, _ in items],
        _PROP_ATTR_VALUES: [value for _, value in items],
    }
    return payload


def _group_edges(
    edges: Sequence[GraphEdge],
) -> dict[GraphEdgeKind, list[dict[str, str]]]:
    grouped: dict[GraphEdgeKind, list[dict[str, str]]] = {}
    for edge in edges:
        grouped.setdefault(edge.kind, []).append(
            {_PROP_SRC: edge.src, _PROP_DST: edge.dst}
        )
    return grouped


def _reset_tx(tx: ManagedTransaction) -> None:
    tx.run(_DELETE_ALL_CYPHER)


def _create_edges(
    tx: ManagedTransaction, kind: GraphEdgeKind, edge_rows: list[dict[str, str]]
) -> None:
    match kind:
        case GraphEdgeKind.IMPORTS:
            tx.run(_CREATE_IMPORTS_EDGES_CYPHER, edges=edge_rows)
        case GraphEdgeKind.CALLS:
            tx.run(_CREATE_CALLS_EDGES_CYPHER, edges=edge_rows)
        case GraphEdgeKind.DEPENDS_ON:
            tx.run(_CREATE_DEPENDS_ON_EDGES_CYPHER, edges=edge_rows)


def _load_tx(
    tx: ManagedTransaction,
    nodes_payload: list[dict[str, str | list[str]]],
    edges_by_kind: Mapping[GraphEdgeKind, list[dict[str, str]]],
) -> None:
    tx.run(_DELETE_ALL_CYPHER)
    if nodes_payload:
        tx.run(_CREATE_NODES_CYPHER, nodes=nodes_payload)
    for kind, edge_rows in edges_by_kind.items():
        _create_edges(tx, kind, edge_rows)


def _read_graph_tx(
    tx: ManagedTransaction,
) -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    nodes: list[GraphNode] = []
    for record in tx.run(_READ_NODES_CYPHER):
        keys = _require_str_list(record[_PROP_ATTR_KEYS], _PROP_ATTR_KEYS)
        values = _require_str_list(record[_PROP_ATTR_VALUES], _PROP_ATTR_VALUES)
        nodes.append(
            GraphNode(
                id=_require_str(record[_PROP_ID], _PROP_ID),
                kind=_node_kind_from_value(
                    _require_str(record[_PROP_KIND], _PROP_KIND)
                ),
                label=_require_str(record[_PROP_LABEL], _PROP_LABEL),
                attrs=_zip_attrs(keys, values, _PROP_ATTR_VALUES),
            )
        )
    edges: list[GraphEdge] = []
    for record in tx.run(_READ_EDGES_CYPHER):
        edges.append(
            GraphEdge(
                src=_require_str(record[_PROP_SRC], _PROP_SRC),
                dst=_require_str(record[_PROP_DST], _PROP_DST),
                kind=_edge_kind_from_rel_type(
                    _require_str(record[_COL_REL_TYPE], _COL_REL_TYPE)
                ),
            )
        )
    return (tuple(nodes), tuple(edges))


def _traverse_read_tx(tx: ManagedTransaction, target: str) -> _TraverseRead:
    root_ids = [
        _require_str(record[_PROP_ID], _PROP_ID)
        for record in tx.run(
            _FIND_PACKAGE_ROOTS_CYPHER, package_kind=_PACKAGE_KIND, target=target
        )
    ]
    if len(root_ids) != 1:
        return _TraverseRead(root_count=len(root_ids), attr_maps=())
    attr_maps: list[Mapping[str, str]] = []
    for record in tx.run(
        _TRAVERSE_CALL_SITES_CYPHER,
        root_id=root_ids[0],
        call_site_kind=_CALL_SITE_KIND,
    ):
        keys = _require_str_list(record[_PROP_ATTR_KEYS], _PROP_ATTR_KEYS)
        values = _require_str_list(record[_PROP_ATTR_VALUES], _PROP_ATTR_VALUES)
        attr_maps.append(_zip_attrs(keys, values, _PROP_ATTR_VALUES))
    return _TraverseRead(root_count=1, attr_maps=tuple(attr_maps))


def _degree_by_node(
    node_by_id: Mapping[str, GraphNode], edges: tuple[GraphEdge, ...]
) -> dict[str, int]:
    degree = {node_id: 0 for node_id in node_by_id}
    for edge in edges:
        degree[edge.src] += 1
        degree[edge.dst] += 1
    return degree


def _drain_levels(
    queue: deque[str],
    out_adjacency: dict[str, list[str]],
    levels: dict[str, int],
) -> None:
    while queue:
        current_id = queue.popleft()
        for neighbor_id in out_adjacency[current_id]:
            if neighbor_id not in levels:
                levels[neighbor_id] = levels[current_id] + 1
                queue.append(neighbor_id)


def _bfs_levels(
    node_by_id: Mapping[str, GraphNode], edges: tuple[GraphEdge, ...]
) -> dict[str, int]:
    sorted_ids = sorted(node_by_id)
    out_adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    in_degree = {node_id: 0 for node_id in node_by_id}
    for edge in edges:
        out_adjacency[edge.src].append(edge.dst)
        in_degree[edge.dst] += 1
    for neighbors in out_adjacency.values():
        neighbors.sort()
    levels: dict[str, int] = {}
    queue: deque[str] = deque()
    for node_id in sorted_ids:
        if in_degree[node_id] == 0:
            levels[node_id] = 0
            queue.append(node_id)
    _drain_levels(queue, out_adjacency, levels)
    for node_id in sorted_ids:
        if node_id not in levels:
            levels[node_id] = 0
            queue.append(node_id)
            _drain_levels(queue, out_adjacency, levels)
    return levels


def _compute_centrality(
    node_by_id: Mapping[str, GraphNode], edges: tuple[GraphEdge, ...]
) -> tuple[CentralityScore, ...]:
    package_nodes = [
        node for node in node_by_id.values() if node.kind == GraphNodeKind.PACKAGE
    ]
    if not package_nodes:
        return ()
    degree = _degree_by_node(node_by_id, edges)
    denominator = len(node_by_id) - 1
    scored = [
        (node, degree[node.id] / denominator if denominator > 0 else 0.0)
        for node in package_nodes
    ]
    scored.sort(key=lambda item: (-item[1], item[0].label, item[0].id))
    return tuple(
        CentralityScore(package=node.label, score=score) for node, score in scored
    )


def _compute_layout(
    node_by_id: Mapping[str, GraphNode], edges: tuple[GraphEdge, ...]
) -> GraphLayout:
    if not node_by_id:
        return GraphLayout(nodes=(), edges=())
    levels = _bfs_levels(node_by_id, edges)
    by_level: dict[int, list[str]] = {}
    for node_id, level in levels.items():
        by_level.setdefault(level, []).append(node_id)
    layout_nodes: list[GraphLayoutNode] = []
    for level in sorted(by_level):
        for index, node_id in enumerate(sorted(by_level[level])):
            node = node_by_id[node_id]
            layout_nodes.append(
                GraphLayoutNode(
                    id=node_id,
                    x=level * _LEVEL_X_SPACING,
                    y=index * _INTRA_LEVEL_Y_SPACING,
                    kind=node.kind,
                    label=node.label,
                )
            )
    return GraphLayout(nodes=tuple(layout_nodes), edges=edges)


class LiveGraphStore(GraphStore):
    def __init__(
        self, uri: str, user: str, password: str, database: str = _DEFAULT_DATABASE
    ) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database: str = database

    def reset(self) -> Result[None, GraphError]:
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(_reset_tx)
        except (Neo4jError, DriverError):
            return Err(GraphError(_NEO4J_FAILURE_MESSAGE, {_OP_KEY: _OP_RESET}))
        return Ok(None)

    def load(
        self, nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]
    ) -> Result[None, GraphError]:
        new_nodes: dict[str, GraphNode] = {}
        for node in nodes:
            if node.id in new_nodes:
                return Err(
                    GraphError(
                        "duplicate node id in graph load",
                        {"node_id": node.id},
                    )
                )
            new_nodes[node.id] = node
        for edge in edges:
            if edge.src not in new_nodes:
                return Err(
                    GraphError(
                        "edge references unknown source node",
                        {"src": edge.src, "dst": edge.dst},
                    )
                )
            if edge.dst not in new_nodes:
                return Err(
                    GraphError(
                        "edge references unknown destination node",
                        {"src": edge.src, "dst": edge.dst},
                    )
                )
        nodes_payload = [_node_payload(node) for node in new_nodes.values()]
        edges_by_kind = _group_edges(edges)
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(_load_tx, nodes_payload, edges_by_kind)
        except (Neo4jError, DriverError):
            return Err(GraphError(_NEO4J_FAILURE_MESSAGE, {_OP_KEY: _OP_LOAD}))
        return Ok(None)

    def centrality(self) -> Result[tuple[CentralityScore, ...], GraphError]:
        try:
            with self._driver.session(database=self._database) as session:
                nodes, edges = session.execute_read(_read_graph_tx)
        except (Neo4jError, DriverError):
            return Err(GraphError(_NEO4J_FAILURE_MESSAGE, {_OP_KEY: _OP_CENTRALITY}))
        except _MalformedGraphDataError as error:
            return Err(
                GraphError(
                    _MALFORMED_MESSAGE,
                    {_FIELD_KEY: error.field, _OP_KEY: _OP_CENTRALITY},
                )
            )
        node_by_id = {node.id: node for node in nodes}
        sorted_edges = tuple(sorted(edges, key=_edge_sort_key))
        return Ok(_compute_centrality(node_by_id, sorted_edges))

    def traverse_call_sites(
        self, target_package: str
    ) -> Result[tuple[CallSite, ...], GraphError]:
        try:
            with self._driver.session(database=self._database) as session:
                read = session.execute_read(_traverse_read_tx, target_package)
        except (Neo4jError, DriverError):
            return Err(GraphError(_NEO4J_FAILURE_MESSAGE, {_OP_KEY: _OP_TRAVERSE}))
        except _MalformedGraphDataError as error:
            return Err(
                GraphError(
                    _MALFORMED_MESSAGE,
                    {_FIELD_KEY: error.field, _OP_KEY: _OP_TRAVERSE},
                )
            )
        if read.root_count == 0:
            return Ok(())
        if read.root_count > 1:
            return Err(
                GraphError(
                    "target package label matches multiple package nodes",
                    {
                        "target_package": target_package,
                        "matches": str(read.root_count),
                    },
                )
            )
        collected: list[CallSite] = []
        for attrs in read.attr_maps:
            decoded = node_attrs_to_call_site(attrs)
            if isinstance(decoded, Err):
                return decoded
            collected.append(decoded.value)
        return Ok(tuple(sorted(set(collected), key=_call_site_sort_key)))

    def layout(self) -> Result[GraphLayout, GraphError]:
        try:
            with self._driver.session(database=self._database) as session:
                nodes, edges = session.execute_read(_read_graph_tx)
        except (Neo4jError, DriverError):
            return Err(GraphError(_NEO4J_FAILURE_MESSAGE, {_OP_KEY: _OP_LAYOUT}))
        except _MalformedGraphDataError as error:
            return Err(
                GraphError(
                    _MALFORMED_MESSAGE,
                    {_FIELD_KEY: error.field, _OP_KEY: _OP_LAYOUT},
                )
            )
        node_by_id = {node.id: node for node in nodes}
        sorted_edges = tuple(sorted(edges, key=_edge_sort_key))
        return Ok(_compute_layout(node_by_id, sorted_edges))

    def close(self) -> None:
        self._driver.close()


__all__ = ("LiveGraphStore",)
