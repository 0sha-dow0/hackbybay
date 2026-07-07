from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from backend.domain.errors import Err, GraphError, Ok, Result
from backend.domain.models import (
    CallSite,
    CentralityScore,
    GraphEdge,
    GraphLayout,
    GraphNode,
)

CALL_SITE_FILE_PATH_ATTR: Final = "file_path"
CALL_SITE_LINE_ATTR: Final = "line"
CALL_SITE_SYMBOL_ATTR: Final = "symbol"
CALL_SITE_IS_ALIASED_ATTR: Final = "is_aliased"
CALL_SITE_ALIAS_ATTR: Final = "alias"
CALL_SITE_SNIPPET_ATTR: Final = "snippet"

CALL_SITE_ALIASED_TRUE: Final = "true"
CALL_SITE_ALIASED_FALSE: Final = "false"

_REQUIRED_CALL_SITE_ATTRS: Final = (
    CALL_SITE_FILE_PATH_ATTR,
    CALL_SITE_LINE_ATTR,
    CALL_SITE_SYMBOL_ATTR,
    CALL_SITE_IS_ALIASED_ATTR,
    CALL_SITE_SNIPPET_ATTR,
)


class GraphStore(Protocol):
    def reset(self) -> Result[None, GraphError]: ...

    def load(
        self, nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]
    ) -> Result[None, GraphError]: ...

    def centrality(self) -> Result[tuple[CentralityScore, ...], GraphError]: ...

    def traverse_call_sites(
        self, target_package: str
    ) -> Result[tuple[CallSite, ...], GraphError]: ...

    def layout(self) -> Result[GraphLayout, GraphError]: ...


def call_site_to_node_attrs(call_site: CallSite) -> Mapping[str, str]:
    attrs: dict[str, str] = {
        CALL_SITE_FILE_PATH_ATTR: call_site.file_path,
        CALL_SITE_LINE_ATTR: str(call_site.line),
        CALL_SITE_SYMBOL_ATTR: call_site.symbol,
        CALL_SITE_IS_ALIASED_ATTR: (
            CALL_SITE_ALIASED_TRUE if call_site.is_aliased else CALL_SITE_ALIASED_FALSE
        ),
        CALL_SITE_SNIPPET_ATTR: call_site.snippet,
    }
    if call_site.alias is not None:
        attrs[CALL_SITE_ALIAS_ATTR] = call_site.alias
    return attrs


def node_attrs_to_call_site(attrs: Mapping[str, str]) -> Result[CallSite, GraphError]:
    missing = [key for key in _REQUIRED_CALL_SITE_ATTRS if key not in attrs]
    if missing:
        return Err(
            GraphError(
                "call-site node is missing required attributes",
                {"missing": ",".join(missing)},
            )
        )
    line_text = attrs[CALL_SITE_LINE_ATTR]
    try:
        line = int(line_text)
    except ValueError:
        return Err(
            GraphError(
                "call-site node line attribute is not an integer",
                {CALL_SITE_LINE_ATTR: line_text},
            )
        )
    is_aliased_text = attrs[CALL_SITE_IS_ALIASED_ATTR]
    if is_aliased_text == CALL_SITE_ALIASED_TRUE:
        is_aliased = True
    elif is_aliased_text == CALL_SITE_ALIASED_FALSE:
        is_aliased = False
    else:
        return Err(
            GraphError(
                "call-site node is_aliased attribute is not a boolean literal",
                {CALL_SITE_IS_ALIASED_ATTR: is_aliased_text},
            )
        )
    return Ok(
        CallSite(
            file_path=attrs[CALL_SITE_FILE_PATH_ATTR],
            line=line,
            symbol=attrs[CALL_SITE_SYMBOL_ATTR],
            is_aliased=is_aliased,
            alias=attrs.get(CALL_SITE_ALIAS_ATTR),
            snippet=attrs[CALL_SITE_SNIPPET_ATTR],
        )
    )


__all__ = (
    "CALL_SITE_ALIAS_ATTR",
    "CALL_SITE_ALIASED_FALSE",
    "CALL_SITE_ALIASED_TRUE",
    "CALL_SITE_FILE_PATH_ATTR",
    "CALL_SITE_IS_ALIASED_ATTR",
    "CALL_SITE_LINE_ATTR",
    "CALL_SITE_SNIPPET_ATTR",
    "CALL_SITE_SYMBOL_ATTR",
    "GraphStore",
    "call_site_to_node_attrs",
    "node_attrs_to_call_site",
)
