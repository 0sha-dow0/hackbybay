import json
import re
from collections.abc import Mapping, Sequence
from typing import Final

from backend.domain.errors import DepCoverError, Err, Ok, Result
from backend.domain.models import JsonValue, NormalizedOutput

_NOISE_KEY_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "timestamp",
        "time",
        "datetime",
        "date",
        "id",
        "requestid",
        "correlationid",
        "traceid",
        "createdat",
        "updatedat",
    }
)
_WHITESPACE_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")
_COMPACT_SEPARATORS: Final[tuple[str, str]] = (",", ":")
_SINGLE_SPACE: Final[str] = " "
_EMPTY_CASE_ID_MESSAGE: Final[str] = "case_id must be a non-empty identifier"


def _canonical_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def _is_noise_key(key: str) -> bool:
    return _canonical_key(key) in _NOISE_KEY_TOKENS


def normalize_json(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key in sorted(value):
            if _is_noise_key(key):
                continue
            normalized[key] = normalize_json(value[key])
        return normalized
    if isinstance(value, Sequence):
        return [normalize_json(item) for item in value]
    return value


def _collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RUN.sub(_SINGLE_SPACE, text).strip()


def _serialize(value: JsonValue) -> str:
    return json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        separators=_COMPACT_SEPARATORS,
    )


def _canonicalize(raw: str) -> str:
    try:
        parsed: JsonValue = json.loads(raw)
    except json.JSONDecodeError:
        collapsed = _collapse_whitespace(raw)
        try:
            reparsed: JsonValue = json.loads(collapsed)
        except json.JSONDecodeError:
            return collapsed
        return _serialize(reparsed)
    return _serialize(parsed)


def normalize_output(case_id: str, raw: str) -> Result[NormalizedOutput, DepCoverError]:
    if case_id.strip() == "":
        return Err(DepCoverError(_EMPTY_CASE_ID_MESSAGE, {"case_id": case_id}))
    return Ok(NormalizedOutput(case_id=case_id, normalized=_canonicalize(raw)))


__all__ = ("normalize_json", "normalize_output")
