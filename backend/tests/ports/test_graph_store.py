"""Tests for Unit 7: GraphStore port + FakeGraphStore.

Every test graph is built from the coder's canonical encoding: PACKAGE nodes,
CALL_SITE nodes whose ``attrs`` come from ``call_site_to_node_attrs``, and
IMPORTS/CALLS edges that point AWAY from the package toward using code (traverse
follows them forward). No hand-written attr strings are used for well-formed
graphs; the single malformed-attrs case deliberately corrupts one field of an
otherwise-canonical encoding to exercise the decode-error path.

Coverage maps to the must-cover cases:

* Aliased chain ``Package -IMPORTS-> CS(is_aliased) -CALLS-> CS(usage)`` — the
  alias usage whose snippet lacks the literal "axios" is still returned (case 1).
* Deterministic traverse ordering (file then line) and no duplicates, including a
  diamond where a call site is reachable via two paths (cases 1, 2).
* Empty graph after reset and after ``load([], [])`` (case 3).
* Dangling edge (unknown src or dst) -> ``Err``; graph state unchanged (case 4).
* Duplicate node id -> ``Err``; graph state unchanged (case 5).
* Determinism of centrality + layout across reset/load cycles and instances (6).
* Layout node-id set is a superset of every edge endpoint (case 7).
* ``call_site_to_node_attrs`` / ``node_attrs_to_call_site`` round-trip for
  alias=None with is_aliased True/False and for a set alias (case 8).
* Structural conformance: ``store: GraphStore = FakeGraphStore()`` type-checks (9).

Plus adversarial cases the contract implies: zero-denominator centrality, degree
ordering and label tie-breaks, packages absent, unknown / duplicated target
package, DEPENDS_ON edges excluded from traversal, malformed call-site attrs
propagated as ``Err``, and full state replacement on reload.
"""

from __future__ import annotations

from typing import TypeVar

from backend.adapters.fake.fake_graph_store import FakeGraphStore
from backend.domain.enums import GraphEdgeKind, GraphNodeKind
from backend.domain.errors import Err, GraphError, Ok, Result
from backend.domain.models import (
    CallSite,
    CentralityScore,
    GraphEdge,
    GraphLayout,
    GraphNode,
)
from backend.ports.graph_store import (
    CALL_SITE_ALIAS_ATTR,
    CALL_SITE_LINE_ATTR,
    GraphStore,
    call_site_to_node_attrs,
    node_attrs_to_call_site,
)

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _unwrap(result: Result[T, GraphError]) -> T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _expect_err(result: Result[T, GraphError]) -> GraphError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


def _pkg(node_id: str, label: str) -> GraphNode:
    return GraphNode(
        id=node_id, kind=GraphNodeKind.PACKAGE, label=label, attrs={}
    )


def _cs_node(node_id: str, call_site: CallSite) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind=GraphNodeKind.CALL_SITE,
        label=node_id,
        attrs=call_site_to_node_attrs(call_site),
    )


def _edge(src: str, dst: str, kind: GraphEdgeKind) -> GraphEdge:
    return GraphEdge(src=src, dst=dst, kind=kind)


def _fresh(
    nodes: list[GraphNode], edges: list[GraphEdge]
) -> FakeGraphStore:
    store = FakeGraphStore()
    _unwrap(store.reset())
    _unwrap(store.load(nodes, edges))
    return store


# --------------------------------------------------------------------------- #
# Case 1: aliased chain — grep-flexing graph reachability                      #
# --------------------------------------------------------------------------- #
def test_aliased_chain_traverse_includes_alias_usage_without_literal() -> None:
    alias_binding = CallSite(
        file_path="src/client.js",
        line=1,
        symbol="require",
        is_aliased=True,
        alias="http",
        snippet="const http = require('axios')",
    )
    alias_usage = CallSite(
        file_path="src/client.js",
        line=5,
        symbol="post",
        is_aliased=True,
        alias="http",
        snippet="http.post(url, body)",
    )
    # Sanity: the usage snippet does not mention the package literal at all.
    assert "axios" not in alias_usage.snippet

    nodes = [
        _pkg("pkg_axios", "axios"),
        _cs_node("cs_alias", alias_binding),
        _cs_node("cs_usage", alias_usage),
    ]
    edges = [
        _edge("pkg_axios", "cs_alias", GraphEdgeKind.IMPORTS),
        _edge("cs_alias", "cs_usage", GraphEdgeKind.CALLS),
    ]
    store = _fresh(nodes, edges)

    result = _unwrap(store.traverse_call_sites("axios"))
    assert alias_usage in result, (
        "alias usage reachable via IMPORTS->CALLS must be returned even though "
        "its snippet lacks the literal package name"
    )
    assert alias_binding in result
    # Ordering is by file then line; both are src/client.js at lines 1 and 5.
    assert result == (alias_binding, alias_usage)


# --------------------------------------------------------------------------- #
# Case 2: deterministic traverse ordering + no duplicates                      #
# --------------------------------------------------------------------------- #
def test_traverse_orders_by_file_then_line_not_by_visit_order() -> None:
    # BFS visits neighbours by node-id order (a_node, b_node, c_node), which is
    # deliberately NOT the file/line sort order, proving the output is sorted.
    cs_a = CallSite(
        file_path="z.js", line=1, symbol="use", is_aliased=False,
        alias=None, snippet="z1",
    )
    cs_b = CallSite(
        file_path="a.js", line=99, symbol="use", is_aliased=False,
        alias=None, snippet="a99",
    )
    cs_c = CallSite(
        file_path="a.js", line=2, symbol="use", is_aliased=False,
        alias=None, snippet="a2",
    )
    nodes = [
        _pkg("p", "axios"),
        _cs_node("a_node", cs_a),
        _cs_node("b_node", cs_b),
        _cs_node("c_node", cs_c),
    ]
    edges = [
        _edge("p", "a_node", GraphEdgeKind.IMPORTS),
        _edge("p", "b_node", GraphEdgeKind.IMPORTS),
        _edge("p", "c_node", GraphEdgeKind.IMPORTS),
    ]
    store = _fresh(nodes, edges)

    result = _unwrap(store.traverse_call_sites("axios"))
    assert result == (cs_c, cs_b, cs_a)  # a.js:2, a.js:99, z.js:1


def test_traverse_diamond_yields_no_duplicate_call_sites() -> None:
    cs_x = CallSite(
        file_path="src/a.js", line=30, symbol="x", is_aliased=False,
        alias=None, snippet="x()",
    )
    cs_y = CallSite(
        file_path="src/b.js", line=10, symbol="y", is_aliased=False,
        alias=None, snippet="y()",
    )
    cs_shared = CallSite(
        file_path="src/z.js", line=5, symbol="z", is_aliased=False,
        alias=None, snippet="z()",
    )
    nodes = [
        _pkg("p", "axios"),
        _cs_node("c1", cs_x),
        _cs_node("c2", cs_y),
        _cs_node("shared", cs_shared),
    ]
    edges = [
        _edge("p", "c1", GraphEdgeKind.IMPORTS),
        _edge("p", "c2", GraphEdgeKind.IMPORTS),
        _edge("c1", "shared", GraphEdgeKind.CALLS),
        _edge("c2", "shared", GraphEdgeKind.CALLS),
    ]
    store = _fresh(nodes, edges)

    result = _unwrap(store.traverse_call_sites("axios"))
    assert result == (cs_x, cs_y, cs_shared)
    assert len(set(result)) == len(result)


def test_traverse_ignores_depends_on_edges() -> None:
    cs = CallSite(
        file_path="src/a.js", line=1, symbol="s", is_aliased=False,
        alias=None, snippet="s()",
    )
    nodes = [_pkg("p", "axios"), _cs_node("cs", cs)]
    # Only edge reaching the call site is DEPENDS_ON, which is not traversable.
    edges = [_edge("p", "cs", GraphEdgeKind.DEPENDS_ON)]
    store = _fresh(nodes, edges)

    assert _unwrap(store.traverse_call_sites("axios")) == ()


def test_traverse_unknown_package_returns_empty() -> None:
    store = _fresh([_pkg("p", "axios")], [])
    assert _unwrap(store.traverse_call_sites("lodash")) == ()


def test_traverse_multiple_packages_same_label_returns_err() -> None:
    nodes = [_pkg("p1", "axios"), _pkg("p2", "axios")]
    store = _fresh(nodes, [])
    error = _expect_err(store.traverse_call_sites("axios"))
    assert isinstance(error, GraphError)


def test_traverse_malformed_call_site_attrs_returns_err() -> None:
    cs = CallSite(
        file_path="src/a.js", line=1, symbol="s", is_aliased=False,
        alias=None, snippet="s()",
    )
    corrupt = dict(call_site_to_node_attrs(cs))
    corrupt[CALL_SITE_LINE_ATTR] = "not-an-int"
    nodes = [
        _pkg("p", "axios"),
        GraphNode(
            id="cs", kind=GraphNodeKind.CALL_SITE, label="cs", attrs=corrupt
        ),
    ]
    edges = [_edge("p", "cs", GraphEdgeKind.IMPORTS)]
    store = _fresh(nodes, edges)
    error = _expect_err(store.traverse_call_sites("axios"))
    assert isinstance(error, GraphError)


# --------------------------------------------------------------------------- #
# Case 3: empty graph                                                          #
# --------------------------------------------------------------------------- #
def test_empty_graph_after_reset() -> None:
    store = FakeGraphStore()
    _unwrap(store.reset())
    assert _unwrap(store.centrality()) == ()
    assert _unwrap(store.traverse_call_sites("axios")) == ()
    assert _unwrap(store.layout()) == GraphLayout(nodes=(), edges=())


def test_empty_graph_after_load_empty() -> None:
    store = FakeGraphStore()
    _unwrap(store.reset())
    _unwrap(store.load([], []))
    assert _unwrap(store.centrality()) == ()
    assert _unwrap(store.traverse_call_sites("axios")) == ()
    assert _unwrap(store.layout()) == GraphLayout(nodes=(), edges=())


# --------------------------------------------------------------------------- #
# Case 4: dangling edge -> Err, state unchanged (never a partial load)         #
# --------------------------------------------------------------------------- #
def test_dangling_dst_edge_load_returns_err() -> None:
    store = FakeGraphStore()
    _unwrap(store.reset())
    error = _expect_err(
        store.load(
            [_pkg("p", "axios")],
            [_edge("p", "ghost", GraphEdgeKind.IMPORTS)],
        )
    )
    assert isinstance(error, GraphError)


def test_dangling_src_edge_load_returns_err() -> None:
    store = FakeGraphStore()
    _unwrap(store.reset())
    error = _expect_err(
        store.load(
            [_pkg("p", "axios")],
            [_edge("ghost", "p", GraphEdgeKind.IMPORTS)],
        )
    )
    assert isinstance(error, GraphError)


def test_dangling_edge_leaves_prior_state_unchanged() -> None:
    cs = CallSite(
        file_path="src/a.js", line=7, symbol="get", is_aliased=False,
        alias=None, snippet="axios.get(x)",
    )
    good_nodes = [_pkg("p", "axios"), _cs_node("cs", cs)]
    good_edges = [_edge("p", "cs", GraphEdgeKind.IMPORTS)]
    store = _fresh(good_nodes, good_edges)

    centrality_before = store.centrality()
    traverse_before = store.traverse_call_sites("axios")
    layout_before = store.layout()

    # A dangling-edge load must not partially mutate the graph.
    _expect_err(
        store.load(
            [_pkg("q", "lodash")],
            [_edge("q", "ghost", GraphEdgeKind.CALLS)],
        )
    )

    assert store.centrality() == centrality_before
    assert store.traverse_call_sites("axios") == traverse_before
    assert store.layout() == layout_before
    # Still exactly the prior graph, not a partial "lodash" load.
    assert _unwrap(store.traverse_call_sites("lodash")) == ()
    assert _unwrap(store.traverse_call_sites("axios")) == (cs,)


def test_dangling_edge_on_empty_store_leaves_it_empty() -> None:
    store = FakeGraphStore()
    _unwrap(store.reset())
    _expect_err(
        store.load(
            [_pkg("p", "axios")],
            [_edge("p", "ghost", GraphEdgeKind.IMPORTS)],
        )
    )
    assert _unwrap(store.centrality()) == ()
    assert _unwrap(store.traverse_call_sites("axios")) == ()
    assert _unwrap(store.layout()) == GraphLayout(nodes=(), edges=())


# --------------------------------------------------------------------------- #
# Case 5: duplicate node id -> Err, state unchanged                            #
# --------------------------------------------------------------------------- #
def test_duplicate_node_id_returns_err() -> None:
    store = FakeGraphStore()
    _unwrap(store.reset())
    error = _expect_err(
        store.load([_pkg("dup", "axios"), _pkg("dup", "lodash")], [])
    )
    assert isinstance(error, GraphError)


def test_duplicate_node_id_leaves_prior_state_unchanged() -> None:
    store = _fresh([_pkg("p", "axios")], [])
    layout_before = store.layout()
    centrality_before = store.centrality()

    _expect_err(
        store.load([_pkg("dup", "x"), _pkg("dup", "y")], [])
    )

    assert store.layout() == layout_before
    assert store.centrality() == centrality_before
    # The original single package is still present.
    assert len(_unwrap(store.centrality())) == 1


# --------------------------------------------------------------------------- #
# Case 6: determinism of centrality + layout                                   #
# --------------------------------------------------------------------------- #
def _sample_graph() -> tuple[list[GraphNode], list[GraphEdge]]:
    cs1 = CallSite(
        file_path="src/a.js", line=3, symbol="get", is_aliased=False,
        alias=None, snippet="axios.get(a)",
    )
    cs2 = CallSite(
        file_path="src/b.js", line=9, symbol="post", is_aliased=True,
        alias="http", snippet="http.post(b)",
    )
    nodes = [
        _pkg("pkg_axios", "axios"),
        _pkg("pkg_lodash", "lodash"),
        _cs_node("cs1", cs1),
        _cs_node("cs2", cs2),
    ]
    edges = [
        _edge("pkg_axios", "cs1", GraphEdgeKind.IMPORTS),
        _edge("cs1", "cs2", GraphEdgeKind.CALLS),
        _edge("pkg_lodash", "cs1", GraphEdgeKind.IMPORTS),
    ]
    return nodes, edges


def test_repeated_reset_load_is_output_idempotent() -> None:
    nodes, edges = _sample_graph()
    store = FakeGraphStore()

    _unwrap(store.reset())
    _unwrap(store.load(nodes, edges))
    centrality_first = _unwrap(store.centrality())
    layout_first = _unwrap(store.layout())

    _unwrap(store.reset())
    _unwrap(store.load(nodes, edges))
    centrality_second = _unwrap(store.centrality())
    layout_second = _unwrap(store.layout())

    assert centrality_first == centrality_second
    assert layout_first == layout_second


def test_determinism_across_independent_instances() -> None:
    nodes, edges = _sample_graph()
    store_a = _fresh(nodes, edges)
    store_b = _fresh(nodes, edges)
    assert _unwrap(store_a.centrality()) == _unwrap(store_b.centrality())
    assert _unwrap(store_a.layout()) == _unwrap(store_b.layout())


# --------------------------------------------------------------------------- #
# Case 7: layout node ids superset of edge endpoints                           #
# --------------------------------------------------------------------------- #
def test_layout_node_ids_superset_of_edge_endpoints() -> None:
    nodes, edges = _sample_graph()
    store = _fresh(nodes, edges)
    layout = _unwrap(store.layout())

    layout_ids = {node.id for node in layout.nodes}
    endpoint_ids = {edge.src for edge in layout.edges} | {
        edge.dst for edge in layout.edges
    }
    assert endpoint_ids <= layout_ids
    # No dangling edge slipped through in the returned layout.
    assert set(layout.edges) == set(edges)


def test_layout_coordinates_are_level_and_index_based() -> None:
    cs_a = CallSite(
        file_path="src/a.js", line=1, symbol="a", is_aliased=False,
        alias=None, snippet="a()",
    )
    cs_b = CallSite(
        file_path="src/b.js", line=2, symbol="b", is_aliased=False,
        alias=None, snippet="b()",
    )
    nodes = [
        _pkg("n0_pkg", "axios"),
        _cs_node("n1_cs", cs_a),
        _cs_node("n2_cs", cs_b),
    ]
    edges = [
        _edge("n0_pkg", "n1_cs", GraphEdgeKind.IMPORTS),
        _edge("n0_pkg", "n2_cs", GraphEdgeKind.IMPORTS),
    ]
    store = _fresh(nodes, edges)
    layout = _unwrap(store.layout())
    by_id = {node.id: node for node in layout.nodes}

    assert (by_id["n0_pkg"].x, by_id["n0_pkg"].y) == (0.0, 0.0)
    assert (by_id["n1_cs"].x, by_id["n1_cs"].y) == (240.0, 0.0)
    assert (by_id["n2_cs"].x, by_id["n2_cs"].y) == (240.0, 120.0)


# --------------------------------------------------------------------------- #
# Case 8: attrs <-> CallSite round-trip identity                               #
# --------------------------------------------------------------------------- #
def test_call_site_attrs_round_trip_identity() -> None:
    cases = [
        CallSite(
            file_path="a.js", line=3, symbol="get", is_aliased=False,
            alias=None, snippet="axios.get(x)",
        ),
        CallSite(
            file_path="pkg/b.ts", line=42, symbol="post", is_aliased=True,
            alias="http", snippet="http.post(y)",
        ),
        CallSite(
            file_path="d.js", line=1, symbol="require", is_aliased=True,
            alias=None, snippet="require('axios')",
        ),
    ]
    for call_site in cases:
        attrs = call_site_to_node_attrs(call_site)
        if call_site.alias is None:
            assert CALL_SITE_ALIAS_ATTR not in attrs
        decoded = node_attrs_to_call_site(attrs)
        assert isinstance(decoded, Ok), f"decode failed for {call_site!r}"
        assert decoded.value == call_site


# --------------------------------------------------------------------------- #
# Case 9: structural conformance to the GraphStore protocol                    #
# --------------------------------------------------------------------------- #
def test_fake_graph_store_conforms_to_graph_store_protocol() -> None:
    store: GraphStore = FakeGraphStore()
    assert isinstance(store.reset(), Ok)


# --------------------------------------------------------------------------- #
# Adversarial: centrality degree/ordering/tie-break/denominator                #
# --------------------------------------------------------------------------- #
def test_centrality_empty_when_no_package_nodes() -> None:
    cs = CallSite(
        file_path="a.js", line=1, symbol="s", is_aliased=False,
        alias=None, snippet="s()",
    )
    store = _fresh([_cs_node("cs", cs)], [])
    assert _unwrap(store.centrality()) == ()


def test_centrality_single_package_zero_denominator() -> None:
    store = _fresh([_pkg("p", "axios")], [])
    result = _unwrap(store.centrality())
    assert result == (CentralityScore(package="axios", score=0.0),)


def test_centrality_degree_scores_and_descending_order() -> None:
    cs1 = CallSite(
        file_path="a.js", line=1, symbol="a", is_aliased=False,
        alias=None, snippet="a()",
    )
    cs2 = CallSite(
        file_path="b.js", line=1, symbol="b", is_aliased=False,
        alias=None, snippet="b()",
    )
    cs3 = CallSite(
        file_path="c.js", line=1, symbol="c", is_aliased=False,
        alias=None, snippet="c()",
    )
    nodes = [
        _pkg("pkg_high", "zzz"),
        _pkg("pkg_low", "aaa"),
        _cs_node("cs1", cs1),
        _cs_node("cs2", cs2),
        _cs_node("cs3", cs3),
    ]
    # 5 nodes -> denominator 4. high has degree 3 (0.75), low has degree 1 (0.25).
    edges = [
        _edge("pkg_high", "cs1", GraphEdgeKind.IMPORTS),
        _edge("pkg_high", "cs2", GraphEdgeKind.IMPORTS),
        _edge("pkg_high", "cs3", GraphEdgeKind.IMPORTS),
        _edge("pkg_low", "cs1", GraphEdgeKind.IMPORTS),
    ]
    store = _fresh(nodes, edges)
    result = _unwrap(store.centrality())
    assert result == (
        CentralityScore(package="zzz", score=0.75),
        CentralityScore(package="aaa", score=0.25),
    )


def test_centrality_ties_break_by_label_ascending() -> None:
    cs = CallSite(
        file_path="a.js", line=1, symbol="s", is_aliased=False,
        alias=None, snippet="s()",
    )
    nodes = [
        _pkg("pkg_b", "bbb"),
        _pkg("pkg_a", "aaa"),
        _cs_node("cs", cs),
    ]
    # Equal degree (1) for both packages -> equal score -> label ascending.
    edges = [
        _edge("pkg_b", "cs", GraphEdgeKind.IMPORTS),
        _edge("pkg_a", "cs", GraphEdgeKind.IMPORTS),
    ]
    store = _fresh(nodes, edges)
    result = _unwrap(store.centrality())
    assert [score.package for score in result] == ["aaa", "bbb"]


# --------------------------------------------------------------------------- #
# Adversarial: reload fully replaces prior graph state                         #
# --------------------------------------------------------------------------- #
def test_reload_fully_replaces_prior_graph() -> None:
    cs = CallSite(
        file_path="a.js", line=1, symbol="s", is_aliased=False,
        alias=None, snippet="s()",
    )
    store = _fresh([_pkg("p", "axios"), _cs_node("cs", cs)], [
        _edge("p", "cs", GraphEdgeKind.IMPORTS)
    ])
    assert _unwrap(store.traverse_call_sites("axios")) == (cs,)

    _unwrap(store.reset())
    _unwrap(store.load([_pkg("q", "lodash")], []))
    assert _unwrap(store.traverse_call_sites("axios")) == ()
    assert _unwrap(store.traverse_call_sites("lodash")) == ()
    assert _unwrap(store.centrality()) == (
        CentralityScore(package="lodash", score=0.0),
    )
