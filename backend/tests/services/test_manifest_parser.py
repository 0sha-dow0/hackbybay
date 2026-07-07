from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from backend.domain.errors import Err, IngestError, LockfileParseError, Ok
from backend.domain.models import FileContent, LockfileWarning
from backend.services.manifest_parser import (
    DependencyEntry,
    ManifestParse,
    parse_manifest,
)

_MANIFEST_PATH = "package.json"
_LOCKFILE_PATH = "package-lock.json"


def _json(obj: object) -> str:
    return json.dumps(obj)


def _manifest(obj: object) -> FileContent:
    return FileContent(path=_MANIFEST_PATH, text=_json(obj))


def _manifest_raw(text: str) -> FileContent:
    return FileContent(path=_MANIFEST_PATH, text=text)


def _lockfile(obj: object) -> FileContent:
    return FileContent(path=_LOCKFILE_PATH, text=_json(obj))


def _lockfile_raw(text: str) -> FileContent:
    return FileContent(path=_LOCKFILE_PATH, text=text)


def _parse_ok(manifest: FileContent, lockfile: FileContent | None) -> ManifestParse:
    result = parse_manifest(manifest, lockfile)
    if isinstance(result, Err):
        raise AssertionError(f"expected Ok, got Err({result.error!s})")
    return result.value


def _parse_err(manifest: FileContent, lockfile: FileContent | None) -> IngestError:
    result = parse_manifest(manifest, lockfile)
    if isinstance(result, Ok):
        raise AssertionError(f"expected Err, got Ok({result.value!r})")
    return result.error


def _shapes(parse: ManifestParse) -> list[str]:
    return [warning.shape for warning in parse.warnings]


def _entry(parse: ManifestParse, name: str) -> DependencyEntry:
    matches = [dep for dep in parse.dependencies if dep.name == name]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one entry named {name!r}, got {matches!r}")
    return matches[0]


def _names(parse: ManifestParse) -> list[str]:
    return [dep.name for dep in parse.dependencies]


# --- Case 1: valid manifest + valid package-lock.json (npm v2/v3 packages shape) ---


def test_valid_manifest_and_lockfile_resolves_sorted_deduped() -> None:
    manifest = _manifest({"dependencies": {"lodash": "^4.17.0", "axios": "^1.2.0"}})
    lockfile = _lockfile(
        {
            "packages": {
                "": {"name": "root"},
                "node_modules/axios": {
                    "version": "1.2.3",
                    "resolved": "https://registry.npmjs.org/axios",
                },
                "node_modules/lodash": {"version": "4.17.21"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)

    assert parse.dependencies == (
        DependencyEntry(name="axios", version_spec="^1.2.0", resolved="1.2.3"),
        DependencyEntry(name="lodash", version_spec="^4.17.0", resolved="4.17.21"),
    )
    assert parse.warnings == ()


def test_nested_v1_dependencies_shape_resolves() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    lockfile = _lockfile(
        {"dependencies": {"axios": {"version": "1.2.3", "resolved": "https://reg"}}}
    )
    parse = _parse_ok(manifest, lockfile)

    assert parse.warnings == ()
    assert _entry(parse, "axios").resolved == "1.2.3"


def test_output_names_are_unique_and_sorted() -> None:
    manifest = _manifest(
        {"dependencies": {"c": "1", "a": "1", "b": "1"}}
    )
    parse = _parse_ok(manifest, _lockfile({"packages": {}}))
    names = _names(parse)
    assert names == sorted(names)
    assert len(names) == len(set(names))
    assert names == ["a", "b", "c"]


def test_scoped_package_path_resolves() -> None:
    manifest = _manifest({"dependencies": {"@scope/pkg": "^1.0.0"}})
    lockfile = _lockfile(
        {"packages": {"node_modules/@scope/pkg": {"version": "3.1.4"}}}
    )
    parse = _parse_ok(manifest, lockfile)
    assert _entry(parse, "@scope/pkg").resolved == "3.1.4"


def test_dependency_absent_from_lockfile_has_null_resolved() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0", "lodash": "^4.0.0"}})
    lockfile = _lockfile({"packages": {"node_modules/axios": {"version": "1.2.3"}}})
    parse = _parse_ok(manifest, lockfile)
    assert _entry(parse, "axios").resolved == "1.2.3"
    assert _entry(parse, "lodash").resolved is None
    assert parse.warnings == ()


def test_version_spec_preserved_verbatim() -> None:
    specs = {
        "exact": "1.0.0",
        "caret": "^2.3.4",
        "tilde": "~5.6.7",
        "star": "*",
        "tag": "latest",
        "range": ">=1.0.0 <2.0.0",
    }
    manifest = _manifest({"dependencies": specs})
    parse = _parse_ok(manifest, None)
    for name, spec in specs.items():
        assert _entry(parse, name).version_spec == spec


# --- Case 2: missing lockfile ---


def test_missing_lockfile_yields_ok_with_warning_and_null_resolved() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    parse = _parse_ok(manifest, None)

    assert _shapes(parse) == ["missing_lockfile"]
    assert parse.warnings[0].reason != ""
    assert parse.dependencies == (
        DependencyEntry(name="axios", version_spec="^1.0.0", resolved=None),
    )


# --- Case 3: hard errors (structurally invalid manifest) ---


def test_manifest_not_valid_json_is_err() -> None:
    error = _parse_err(_manifest_raw("{ not json"), None)
    assert isinstance(error, LockfileParseError)


def test_manifest_empty_string_is_err() -> None:
    error = _parse_err(_manifest_raw(""), None)
    assert isinstance(error, LockfileParseError)


def test_manifest_whitespace_only_is_err() -> None:
    error = _parse_err(_manifest_raw("   \n\t "), None)
    assert isinstance(error, LockfileParseError)


@pytest.mark.parametrize("payload", [[1, 2, 3], 42, 3.14, True, "a string", None])
def test_manifest_valid_json_but_not_object_is_err(payload: object) -> None:
    error = _parse_err(_manifest(payload), None)
    assert isinstance(error, LockfileParseError)


def test_manifest_non_string_version_number_is_err() -> None:
    error = _parse_err(_manifest({"dependencies": {"axios": 123}}), None)
    assert isinstance(error, LockfileParseError)


@pytest.mark.parametrize("bad_version", [123, 1.5, True, ["1.0.0"], {"v": "1"}, None])
def test_manifest_non_string_version_variants_are_err(bad_version: object) -> None:
    error = _parse_err(_manifest({"dependencies": {"axios": bad_version}}), None)
    assert isinstance(error, LockfileParseError)


@pytest.mark.parametrize("section", ["dependencies", "devDependencies"])
@pytest.mark.parametrize("bad_section", [[1, 2], "axios", 7, True])
def test_manifest_non_object_dependency_section_is_err(
    section: str, bad_section: object
) -> None:
    error = _parse_err(_manifest({section: bad_section}), None)
    assert isinstance(error, LockfileParseError)


# --- Case 4: empty dependencies object ---


def test_empty_dependencies_object_yields_empty_tuple() -> None:
    parse = _parse_ok(_manifest({"dependencies": {}}), _lockfile({"packages": {}}))
    assert parse.dependencies == ()
    assert parse.warnings == ()


def test_manifest_without_any_dependency_sections_is_ok_empty() -> None:
    parse = _parse_ok(_manifest({"name": "root", "version": "1.0.0"}), None)
    assert parse.dependencies == ()
    assert _shapes(parse) == ["missing_lockfile"]


def test_null_dependency_section_treated_as_absent() -> None:
    parse = _parse_ok(_manifest({"dependencies": None}), _lockfile({"packages": {}}))
    assert parse.dependencies == ()


# --- Case 5: lockfile weirdness -> a warning, no crash, deps still returned ---


def test_duplicate_versions_warn_and_null_resolved() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    lockfile = _lockfile(
        {
            "packages": {
                "node_modules/axios": {"version": "1.2.3"},
                "node_modules/foo/node_modules/axios": {"version": "0.9.0"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert "duplicate_version" in _shapes(parse)
    assert _entry(parse, "axios").resolved is None


def test_workspaces_declaration_warns() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    lockfile = _lockfile(
        {
            "packages": {
                "": {"name": "root", "workspaces": ["packages/*"]},
                "node_modules/axios": {"version": "1.2.3"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert "workspace" in _shapes(parse)
    assert _entry(parse, "axios").resolved == "1.2.3"


def test_workspace_member_path_warns() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    lockfile = _lockfile(
        {
            "packages": {
                "packages/foo": {"version": "1.0.0"},
                "node_modules/axios": {"version": "1.2.3"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert "workspace" in _shapes(parse)


def test_workspace_link_entry_warns_and_null_resolved() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0", "mylib": "*"}})
    lockfile = _lockfile(
        {
            "packages": {
                "node_modules/mylib": {"link": True},
                "node_modules/axios": {"version": "1.2.3"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert "workspace" in _shapes(parse)
    assert _entry(parse, "mylib").resolved is None


def test_git_url_dep_via_resolved_warns_and_null_resolved() -> None:
    manifest = _manifest({"dependencies": {"gitdep": "github:foo/bar", "axios": "^1.0.0"}})
    lockfile = _lockfile(
        {
            "packages": {
                "node_modules/gitdep": {
                    "version": "1.0.0",
                    "resolved": "git+https://github.com/foo/bar.git",
                },
                "node_modules/axios": {"version": "1.2.3"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert "git_url" in _shapes(parse)
    assert _entry(parse, "gitdep").resolved is None
    assert _entry(parse, "axios").resolved == "1.2.3"


def test_git_url_dep_via_version_field_warns() -> None:
    manifest = _manifest({"dependencies": {"gitdep": "*"}})
    lockfile = _lockfile(
        {"packages": {"node_modules/gitdep": {"version": "git+ssh://git@host/x.git"}}}
    )
    parse = _parse_ok(manifest, lockfile)
    assert "git_url" in _shapes(parse)
    assert _entry(parse, "gitdep").resolved is None


def test_git_prefix_is_case_insensitive() -> None:
    manifest = _manifest({"dependencies": {"gitdep": "*"}})
    lockfile = _lockfile(
        {
            "packages": {
                "node_modules/gitdep": {
                    "version": "1.0.0",
                    "resolved": "GIT+HTTPS://github.com/foo/bar.git",
                }
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert "git_url" in _shapes(parse)


def test_malformed_lockfile_json_warns_and_returns_deps() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    parse = _parse_ok(manifest, _lockfile_raw("{ not valid json"))
    assert _shapes(parse) == ["malformed_lockfile"]
    assert _entry(parse, "axios").resolved is None


@pytest.mark.parametrize("payload", [{"lockfileVersion": 3}, [1, 2, 3], 42, "text", None])
def test_unknown_lockfile_shape_warns_and_returns_deps(payload: object) -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    parse = _parse_ok(manifest, _lockfile(payload))
    assert _shapes(parse) == ["unknown_lockfile_shape"]
    assert _entry(parse, "axios").resolved is None


def test_empty_packages_object_is_known_shape_without_warning() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    parse = _parse_ok(manifest, _lockfile({"packages": {}}))
    assert parse.warnings == ()
    assert _entry(parse, "axios").resolved is None


def test_every_warning_has_nonempty_reason() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0", "mylib": "*"}})
    lockfile = _lockfile(
        {
            "packages": {
                "": {"name": "root", "workspaces": ["packages/*"]},
                "node_modules/mylib": {"link": True},
                "node_modules/axios": {"version": "1.2.3"},
                "node_modules/foo/node_modules/axios": {"version": "0.9.0"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert parse.warnings != ()
    for warning in parse.warnings:
        assert isinstance(warning, LockfileWarning)
        assert warning.reason != ""
        assert warning.shape != ""


# --- Case 6: merge dependencies + devDependencies ---


def test_merge_dedup_runtime_version_wins_and_sorted() -> None:
    manifest = _manifest(
        {
            "dependencies": {"axios": "^1.0.0", "shared": "^2.0.0"},
            "devDependencies": {"jest": "^29.0.0", "shared": "^9.9.9"},
        }
    )
    parse = _parse_ok(manifest, None)
    assert _names(parse) == ["axios", "jest", "shared"]
    assert _entry(parse, "shared").version_spec == "^2.0.0"


def test_dev_only_dependency_included() -> None:
    parse = _parse_ok(_manifest({"devDependencies": {"jest": "^29.0.0"}}), None)
    assert _names(parse) == ["jest"]
    assert _entry(parse, "jest").version_spec == "^29.0.0"


# --- Case 7: determinism ---


def test_parsing_identical_inputs_twice_is_identical() -> None:
    manifest = _manifest(
        {
            "dependencies": {"axios": "^1.0.0", "mylib": "*", "gitdep": "*"},
            "devDependencies": {"jest": "^29.0.0"},
        }
    )
    lockfile = _lockfile(
        {
            "packages": {
                "": {"name": "root", "workspaces": ["packages/*"]},
                "node_modules/mylib": {"link": True},
                "node_modules/gitdep": {
                    "version": "1.0.0",
                    "resolved": "git+https://github.com/foo/bar.git",
                },
                "node_modules/axios": {"version": "1.2.3"},
                "node_modules/foo/node_modules/axios": {"version": "0.9.0"},
            }
        }
    )
    first = _parse_ok(manifest, lockfile)
    second = _parse_ok(manifest, lockfile)
    assert first == second
    assert first.dependencies == second.dependencies
    assert first.warnings == second.warnings


def test_warnings_are_sorted_by_shape_then_reason() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0", "gitdep": "*"}})
    lockfile = _lockfile(
        {
            "packages": {
                "node_modules/gitdep": {
                    "version": "1.0.0",
                    "resolved": "git+https://github.com/foo/bar.git",
                },
                "node_modules/axios": {"version": "1.2.3"},
                "node_modules/foo/node_modules/axios": {"version": "0.9.0"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    keys = [(w.shape, w.reason) for w in parse.warnings]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    assert "duplicate_version" in _shapes(parse)
    assert "git_url" in _shapes(parse)


# --- Case 8: frozen dataclasses ---


def test_dependency_entry_is_frozen() -> None:
    entry = DependencyEntry(name="axios", version_spec="^1.0.0", resolved=None)
    with pytest.raises(FrozenInstanceError):
        setattr(entry, "name", "changed")


def test_manifest_parse_is_frozen() -> None:
    parse = ManifestParse(dependencies=(), warnings=())
    with pytest.raises(FrozenInstanceError):
        setattr(parse, "dependencies", ())


def test_dependency_entry_equality_by_value() -> None:
    left = DependencyEntry(name="a", version_spec="^1", resolved="1.0.0")
    right = DependencyEntry(name="a", version_spec="^1", resolved="1.0.0")
    assert left == right


# --- Adversarial lockfile robustness ---


def test_packages_non_object_falls_back_to_nested_dependencies() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    lockfile = _lockfile(
        {
            "packages": [1, 2, 3],
            "dependencies": {"axios": {"version": "1.2.3"}},
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert _entry(parse, "axios").resolved == "1.2.3"


def test_empty_json_object_lockfile_is_unknown_shape() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    parse = _parse_ok(manifest, _lockfile({}))
    assert _shapes(parse) == ["unknown_lockfile_shape"]


def test_lockfile_entries_that_are_not_objects_are_skipped() -> None:
    manifest = _manifest({"dependencies": {"axios": "^1.0.0"}})
    lockfile = _lockfile(
        {
            "packages": {
                "node_modules/axios": "not-an-object",
                "node_modules/lodash": {"version": "4.17.21"},
            }
        }
    )
    parse = _parse_ok(manifest, lockfile)
    assert _entry(parse, "axios").resolved is None
