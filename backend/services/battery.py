from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from backend.domain.constants import BEHAVIORAL_BATTERY_SIZE
from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import BehavioralCase, NormalizedOutput

_CATEGORY_SUCCESS: Final[str] = "success"
_CATEGORY_NOT_FOUND: Final[str] = "404"
_CATEGORY_SERVER_ERROR: Final[str] = "500"
_CATEGORY_MALFORMED_JSON: Final[str] = "malformed-JSON"
_CATEGORY_EMPTY_BODY: Final[str] = "empty-body"
_CATEGORY_LARGE_PAYLOAD: Final[str] = "large-payload"
_CATEGORY_SPECIAL_CHARS: Final[str] = "special-chars"
_CATEGORY_TIMEOUT: Final[str] = "timeout"
_CATEGORY_QUERY_PARAMS: Final[str] = "query-params"
_CATEGORY_POST: Final[str] = "POST"

_REQUIRED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {_CATEGORY_NOT_FOUND, _CATEGORY_SERVER_ERROR, _CATEGORY_MALFORMED_JSON}
)

_JSON_CONTENT_TYPE: Final[str] = "application/json"
_LARGE_PAYLOAD_PAGE_SIZE: Final[int] = 1000

_BATTERY: Final[tuple[BehavioralCase, ...]] = (
    BehavioralCase(
        id="get-success",
        description="GET an existing user returns 200 with a JSON body",
        request={"method": "GET", "path": "/api/users/1"},
        category=_CATEGORY_SUCCESS,
    ),
    BehavioralCase(
        id="get-not-found",
        description="GET a missing user returns 404",
        request={"method": "GET", "path": "/api/users/999999"},
        category=_CATEGORY_NOT_FOUND,
    ),
    BehavioralCase(
        id="get-server-error",
        description="GET an endpoint that raises returns 500",
        request={"method": "GET", "path": "/api/faults/server-error"},
        category=_CATEGORY_SERVER_ERROR,
    ),
    BehavioralCase(
        id="get-malformed-json",
        description="GET an endpoint whose 200 body is not valid JSON",
        request={"method": "GET", "path": "/api/faults/malformed-json"},
        category=_CATEGORY_MALFORMED_JSON,
    ),
    BehavioralCase(
        id="get-empty-body",
        description="GET a 200 response with an empty body",
        request={"method": "GET", "path": "/api/faults/empty-body"},
        category=_CATEGORY_EMPTY_BODY,
    ),
    BehavioralCase(
        id="get-large-payload",
        description="GET a large paginated collection",
        request={
            "method": "GET",
            "path": "/api/users",
            "query": {"page": 1, "page_size": _LARGE_PAYLOAD_PAGE_SIZE},
        },
        category=_CATEGORY_LARGE_PAYLOAD,
    ),
    BehavioralCase(
        id="post-special-chars",
        description="POST a body containing unicode and markup characters",
        request={
            "method": "POST",
            "path": "/api/messages",
            "headers": {"content-type": _JSON_CONTENT_TYPE},
            "body": {"text": "héllo — 日本語 \"quotes\" & <tags>"},
        },
        category=_CATEGORY_SPECIAL_CHARS,
    ),
    BehavioralCase(
        id="get-slow-timeout",
        description="GET a deliberately slow endpoint that exercises the timeout path",
        request={"method": "GET", "path": "/api/faults/slow"},
        category=_CATEGORY_TIMEOUT,
    ),
    BehavioralCase(
        id="get-query-params",
        description="GET a collection with filtering and sorting query parameters",
        request={
            "method": "GET",
            "path": "/api/users",
            "query": {"role": "admin", "sort": "created_at", "order": "desc"},
        },
        category=_CATEGORY_QUERY_PARAMS,
    ),
    BehavioralCase(
        id="post-create-resource",
        description="POST creates a resource and returns 201",
        request={
            "method": "POST",
            "path": "/api/users",
            "headers": {"content-type": _JSON_CONTENT_TYPE},
            "body": {"name": "Ada", "email": "ada@example.com"},
        },
        category=_CATEGORY_POST,
    ),
)

_BATTERY_IDS: Final[frozenset[str]] = frozenset(case.id for case in _BATTERY)
_BATTERY_CATEGORIES: Final[frozenset[str]] = frozenset(case.category for case in _BATTERY)

assert len(_BATTERY) == BEHAVIORAL_BATTERY_SIZE
assert len(_BATTERY_IDS) == len(_BATTERY)
assert _REQUIRED_CATEGORIES <= _BATTERY_CATEGORIES


def input_battery() -> tuple[BehavioralCase, ...]:
    return _BATTERY


def load_golden_outputs(
    source: Mapping[str, str],
) -> Result[Mapping[str, NormalizedOutput], DepCoverError]:
    provided_ids = frozenset(source)
    missing_ids = _BATTERY_IDS - provided_ids
    extra_ids = provided_ids - _BATTERY_IDS
    if missing_ids or extra_ids:
        return Err(
            DepCoverError(
                "golden outputs must cover exactly the battery case ids",
                {
                    "missing_ids": ", ".join(sorted(missing_ids)),
                    "extra_ids": ", ".join(sorted(extra_ids)),
                },
            )
        )
    goldens: Mapping[str, NormalizedOutput] = MappingProxyType(
        {
            case_id: NormalizedOutput(case_id=case_id, normalized=source[case_id])
            for case_id in sorted(_BATTERY_IDS)
        }
    )
    return Ok(goldens)


__all__ = ("input_battery", "load_golden_outputs")
