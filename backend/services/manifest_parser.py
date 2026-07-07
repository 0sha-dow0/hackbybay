from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

from backend.domain.errors import Err, IngestError, LockfileParseError, Ok, Result
from backend.domain.models import FileContent, LockfileWarning

__all__ = ("DependencyEntry", "ManifestParse", "parse_manifest")


_FIELD_DEPENDENCIES: Final[str] = "dependencies"
_FIELD_DEV_DEPENDENCIES: Final[str] = "devDependencies"
_FIELD_PACKAGES: Final[str] = "packages"
_FIELD_VERSION: Final[str] = "version"
_FIELD_RESOLVED: Final[str] = "resolved"
_FIELD_LINK: Final[str] = "link"
_FIELD_WORKSPACES: Final[str] = "workspaces"

_NODE_MODULES_MARKER: Final[str] = "node_modules/"
_ROOT_PACKAGE_KEY: Final[str] = ""

_GIT_URL_PREFIXES: Final[tuple[str, ...]] = (
    "git+",
    "git://",
    "git@",
    "github:",
    "gitlab:",
    "bitbucket:",
)

_SHAPE_MISSING_LOCKFILE: Final[str] = "missing_lockfile"
_SHAPE_MALFORMED_LOCKFILE: Final[str] = "malformed_lockfile"
_SHAPE_UNKNOWN_LOCKFILE: Final[str] = "unknown_lockfile_shape"
_SHAPE_WORKSPACE: Final[str] = "workspace"
_SHAPE_GIT_URL: Final[str] = "git_url"
_SHAPE_DUPLICATE_VERSION: Final[str] = "duplicate_version"

_MSG_MANIFEST_INVALID_JSON: Final[str] = "manifest is not valid JSON"
_MSG_MANIFEST_NOT_OBJECT: Final[str] = "manifest is not a JSON object"
_MSG_MANIFEST_SECTION_NOT_OBJECT: Final[str] = (
    "manifest dependency section is not a JSON object"
)
_MSG_MANIFEST_SPEC_NOT_STRING: Final[str] = (
    "manifest dependency version specifier is not a string"
)

_REASON_MISSING_LOCKFILE: Final[str] = (
    "no lockfile provided; dependency versions are unresolved"
)


@dataclass(frozen=True)
class DependencyEntry:
    name: str
    version_spec: str
    resolved: str | None


@dataclass(frozen=True)
class ManifestParse:
    dependencies: tuple[DependencyEntry, ...]
    warnings: tuple[LockfileWarning, ...]


class _LockClass(Enum):
    GIT = auto()
    WORKSPACE = auto()
    DUPLICATE = auto()
    RESOLVED = auto()
    ABSENT = auto()


@dataclass
class _LockfileScan:
    versions: dict[str, set[str]] = field(default_factory=dict)
    git_names: set[str] = field(default_factory=set)
    workspace_names: set[str] = field(default_factory=set)
    workspace_member_paths: set[str] = field(default_factory=set)
    workspace_root_declared: bool = False


def parse_manifest(
    manifest: FileContent, lockfile: FileContent | None
) -> Result[ManifestParse, IngestError]:
    decoded_manifest = _decode_json(manifest.text)
    if isinstance(decoded_manifest, Err):
        return Err(
            LockfileParseError(
                _MSG_MANIFEST_INVALID_JSON,
                {"path": manifest.path, "detail": decoded_manifest.error.message},
            )
        )
    manifest_root = _as_object_mapping(decoded_manifest.value)
    if manifest_root is None:
        return Err(
            LockfileParseError(_MSG_MANIFEST_NOT_OBJECT, {"path": manifest.path})
        )
    dependency_specs = _extract_manifest_dependencies(manifest_root)
    if isinstance(dependency_specs, Err):
        return Err(dependency_specs.error)

    scan, warnings = _scan_lockfile(lockfile)
    entries = tuple(
        DependencyEntry(
            name=name,
            version_spec=dependency_specs.value[name],
            resolved=_resolve_version(name, scan),
        )
        for name in sorted(dependency_specs.value)
    )
    return Ok(
        ManifestParse(
            dependencies=entries,
            warnings=_dedupe_and_sort_warnings(warnings),
        )
    )


def _extract_manifest_dependencies(
    manifest_root: Mapping[str, object],
) -> Result[dict[str, str], LockfileParseError]:
    merged: dict[str, str] = {}
    for section_field in (_FIELD_DEPENDENCIES, _FIELD_DEV_DEPENDENCIES):
        section_value = manifest_root.get(section_field)
        if section_value is None:
            continue
        section = _as_object_mapping(section_value)
        if section is None:
            return Err(
                LockfileParseError(
                    _MSG_MANIFEST_SECTION_NOT_OBJECT, {"section": section_field}
                )
            )
        for dependency_name, spec_value in section.items():
            if not isinstance(spec_value, str):
                return Err(
                    LockfileParseError(
                        _MSG_MANIFEST_SPEC_NOT_STRING,
                        {"section": section_field, "dependency": dependency_name},
                    )
                )
            if dependency_name not in merged:
                merged[dependency_name] = spec_value
    return Ok(merged)


def _scan_lockfile(
    lockfile: FileContent | None,
) -> tuple[_LockfileScan | None, list[LockfileWarning]]:
    if lockfile is None:
        return None, [
            LockfileWarning(
                shape=_SHAPE_MISSING_LOCKFILE, reason=_REASON_MISSING_LOCKFILE
            )
        ]
    decoded = _decode_json(lockfile.text)
    if isinstance(decoded, Err):
        return None, [
            LockfileWarning(
                shape=_SHAPE_MALFORMED_LOCKFILE, reason=_reason_malformed(lockfile.path)
            )
        ]
    root = _as_object_mapping(decoded.value)
    if root is None:
        return None, [
            LockfileWarning(
                shape=_SHAPE_UNKNOWN_LOCKFILE, reason=_reason_unknown(lockfile.path)
            )
        ]
    scan = _LockfileScan()
    packages = _as_object_mapping(root.get(_FIELD_PACKAGES))
    if packages is not None:
        _collect_from_packages(packages, scan)
        return scan, _scan_shape_warnings(scan)
    nested = _as_object_mapping(root.get(_FIELD_DEPENDENCIES))
    if nested is not None:
        _collect_from_nested_dependencies(nested, scan)
        return scan, _scan_shape_warnings(scan)
    return None, [
        LockfileWarning(
            shape=_SHAPE_UNKNOWN_LOCKFILE, reason=_reason_unknown(lockfile.path)
        )
    ]


def _collect_from_packages(
    packages: Mapping[str, object], scan: _LockfileScan
) -> None:
    for path_key, entry_value in packages.items():
        entry = _as_object_mapping(entry_value)
        if entry is None:
            continue
        if path_key == _ROOT_PACKAGE_KEY:
            if _declares_workspaces(entry):
                scan.workspace_root_declared = True
            continue
        name = _package_name_from_path(path_key)
        if name is None:
            scan.workspace_member_paths.add(path_key)
            continue
        _record_entry(name, entry, scan)


def _collect_from_nested_dependencies(
    dependencies: Mapping[str, object], scan: _LockfileScan
) -> None:
    for name, entry_value in dependencies.items():
        entry = _as_object_mapping(entry_value)
        if entry is None:
            continue
        _record_entry(name, entry, scan)
        child = _as_object_mapping(entry.get(_FIELD_DEPENDENCIES))
        if child is not None:
            _collect_from_nested_dependencies(child, scan)


def _record_entry(
    name: str, entry: Mapping[str, object], scan: _LockfileScan
) -> None:
    if _is_link_entry(entry):
        scan.workspace_names.add(name)
        return
    resolved_url = _string_field(entry, _FIELD_RESOLVED)
    version = _string_field(entry, _FIELD_VERSION)
    if (resolved_url is not None and _is_git_reference(resolved_url)) or (
        version is not None and _is_git_reference(version)
    ):
        scan.git_names.add(name)
        return
    if version is not None:
        scan.versions.setdefault(name, set()).add(version)


def _scan_shape_warnings(scan: _LockfileScan) -> list[LockfileWarning]:
    warnings: list[LockfileWarning] = []
    if scan.workspace_root_declared or scan.workspace_member_paths:
        warnings.append(
            LockfileWarning(
                shape=_SHAPE_WORKSPACE, reason=_reason_workspace_root(scan)
            )
        )
    observed_names = (
        set(scan.git_names) | set(scan.workspace_names) | set(scan.versions)
    )
    for name in sorted(observed_names):
        classification = _classify(name, scan)
        if classification is _LockClass.GIT:
            warnings.append(
                LockfileWarning(shape=_SHAPE_GIT_URL, reason=_reason_git(name))
            )
        elif classification is _LockClass.WORKSPACE:
            warnings.append(
                LockfileWarning(
                    shape=_SHAPE_WORKSPACE, reason=_reason_workspace_name(name)
                )
            )
        elif classification is _LockClass.DUPLICATE:
            warnings.append(
                LockfileWarning(
                    shape=_SHAPE_DUPLICATE_VERSION,
                    reason=_reason_duplicate(name, scan.versions[name]),
                )
            )
    return warnings


def _resolve_version(name: str, scan: _LockfileScan | None) -> str | None:
    if scan is None:
        return None
    if _classify(name, scan) is not _LockClass.RESOLVED:
        return None
    return next(iter(scan.versions[name]))


def _classify(name: str, scan: _LockfileScan) -> _LockClass:
    if name in scan.git_names:
        return _LockClass.GIT
    if name in scan.workspace_names:
        return _LockClass.WORKSPACE
    versions = scan.versions.get(name)
    if versions is None:
        return _LockClass.ABSENT
    if len(versions) > 1:
        return _LockClass.DUPLICATE
    return _LockClass.RESOLVED


def _decode_json(text: str) -> Result[object, LockfileParseError]:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as error:
        return Err(LockfileParseError(_MSG_MANIFEST_INVALID_JSON, {"detail": str(error)}))
    return Ok(parsed)


def _as_object_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    typed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        typed[key] = item
    return typed


def _string_field(entry: Mapping[str, object], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) else None


def _is_link_entry(entry: Mapping[str, object]) -> bool:
    return entry.get(_FIELD_LINK) is True


def _declares_workspaces(entry: Mapping[str, object]) -> bool:
    value = entry.get(_FIELD_WORKSPACES)
    if isinstance(value, (str, bytes)):
        return False
    if isinstance(value, Sequence):
        return len(value) > 0
    if isinstance(value, Mapping):
        return len(value) > 0
    return False


def _package_name_from_path(path_key: str) -> str | None:
    marker_index = path_key.rfind(_NODE_MODULES_MARKER)
    if marker_index == -1:
        return None
    name = path_key[marker_index + len(_NODE_MODULES_MARKER) :]
    return name if name != "" else None


def _is_git_reference(value: str) -> bool:
    return value.lower().startswith(_GIT_URL_PREFIXES)


def _reason_malformed(path: str) -> str:
    return f"lockfile {path!r} is not valid JSON and is skipped"


def _reason_unknown(path: str) -> str:
    return f"lockfile {path!r} has an unrecognized shape and is skipped"


def _reason_workspace_root(scan: _LockfileScan) -> str:
    count = len(scan.workspace_member_paths)
    return (
        f"lockfile declares workspace members ({count} member path(s)); "
        "workspace resolution is skipped"
    )


def _reason_workspace_name(name: str) -> str:
    return (
        f"dependency {name!r} is a workspace link entry and is not locked to a "
        "registry version"
    )


def _reason_git(name: str) -> str:
    return (
        f"dependency {name!r} resolves to a git reference and is not locked to a "
        "registry version"
    )


def _reason_duplicate(name: str, versions: set[str]) -> str:
    listed = ", ".join(sorted(versions))
    return (
        f"dependency {name!r} resolves to multiple versions ({listed}); "
        "resolution is skipped"
    )


def _dedupe_and_sort_warnings(
    warnings: list[LockfileWarning],
) -> tuple[LockfileWarning, ...]:
    unique: dict[tuple[str, str], LockfileWarning] = {
        (warning.shape, warning.reason): warning for warning in warnings
    }
    return tuple(
        sorted(unique.values(), key=lambda warning: (warning.shape, warning.reason))
    )
