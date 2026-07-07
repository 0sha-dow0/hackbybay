"""Tests for Unit 6: RepoContentProvider port + FakeRepoContentProvider.

Binds to the coder's actual API:

* ``FakeRepoContentProvider(repos: Mapping[str, tuple[FileContent, ...]])``.
* ``fetch`` returns files with normalized (posix, repo-relative, no ``..``),
  deduped paths, sorted ascending by path.
* Unknown ``repo_url`` -> ``Err`` on all three methods.
* Registered empty repo -> ``fetch`` yields ``Ok(())``.
* Manifest is the file whose *normalized* path is exactly ``package.json``.
* Lockfile is the first present of package-lock.json, npm-shrinkwrap.json,
  yarn.lock, pnpm-lock.yaml; none present -> ``Ok(None)``.
* Absolute / ``..`` / empty paths -> ``Err`` at fetch/read time.
* Exact-duplicate raw path within one repo -> ``ValueError`` at construction.
* Two distinct raw paths normalizing to the same path -> ``Err`` at fetch.
* Constructor defensively copies the passed mapping.

Structural conformance of ``FakeRepoContentProvider`` to the
``RepoContentProvider`` Protocol is proved by the scoped mypy run through the
annotated binding in ``test_structural_conformance_typechecks``.
"""

from __future__ import annotations

import pytest

from backend.adapters.fake.fake_repo_content import FakeRepoContentProvider
from backend.domain.errors import Err, IngestError, Ok
from backend.domain.models import FileContent
from backend.ports.repo_content import RepoContentProvider

_REPO = "https://example.com/acme/widget.git"


def _fc(path: str, text: str = "") -> FileContent:
    return FileContent(path=path, text=text)


# ---------------------------------------------------------------------------
# Case 1: fetch normalizes, dedupes, and sorts ascending by path.
# ---------------------------------------------------------------------------


def test_fetch_normalizes_and_sorts_paths() -> None:
    provider = FakeRepoContentProvider(
        {
            _REPO: (
                _fc("./b.js", "b-text"),
                _fc("a.js", "a-text"),
                _fc("src\\c.js", "c-text"),
                _fc("src/./d.js", "d-text"),
            )
        }
    )
    result = provider.fetch(_REPO)
    assert isinstance(result, Ok)
    paths = [file.path for file in result.value]
    assert paths == ["a.js", "b.js", "src/c.js", "src/d.js"]
    # Normalization must preserve the associated text unchanged.
    by_path = {file.path: file.text for file in result.value}
    assert by_path == {
        "a.js": "a-text",
        "b.js": "b-text",
        "src/c.js": "c-text",
        "src/d.js": "d-text",
    }


def test_fetch_paths_are_all_normalized() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("./nested/./x.js"),)})
    result = provider.fetch(_REPO)
    assert isinstance(result, Ok)
    assert [file.path for file in result.value] == ["nested/x.js"]


# ---------------------------------------------------------------------------
# Case 2: unknown repo -> Err on every method (never empty success).
# ---------------------------------------------------------------------------


def test_unknown_repo_fetch_is_err() -> None:
    provider = FakeRepoContentProvider({})
    result = provider.fetch("https://unknown/repo.git")
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_unknown_repo_read_manifest_is_err() -> None:
    provider = FakeRepoContentProvider({})
    result = provider.read_manifest("https://unknown/repo.git")
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_unknown_repo_read_lockfile_is_err() -> None:
    provider = FakeRepoContentProvider({})
    result = provider.read_lockfile("https://unknown/repo.git")
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_registered_repo_distinguished_from_unknown_repo() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("a.js"),)})
    other = provider.fetch("https://example.com/other.git")
    assert isinstance(other, Err)


# ---------------------------------------------------------------------------
# Case 3: registered EMPTY repo -> Ok(()) (not Err).
# ---------------------------------------------------------------------------


def test_registered_empty_repo_fetch_is_ok_empty_tuple() -> None:
    provider = FakeRepoContentProvider({_REPO: ()})
    result = provider.fetch(_REPO)
    assert isinstance(result, Ok)
    assert result.value == ()


def test_registered_empty_repo_read_manifest_is_err() -> None:
    provider = FakeRepoContentProvider({_REPO: ()})
    result = provider.read_manifest(_REPO)
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_registered_empty_repo_read_lockfile_is_ok_none() -> None:
    provider = FakeRepoContentProvider({_REPO: ()})
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is None


# ---------------------------------------------------------------------------
# Case 4: read_manifest returns the package.json FileContent; missing -> Err.
# ---------------------------------------------------------------------------


def test_read_manifest_returns_package_json() -> None:
    manifest = _fc("package.json", '{"name":"widget"}')
    provider = FakeRepoContentProvider({_REPO: (_fc("index.js"), manifest)})
    result = provider.read_manifest(_REPO)
    assert isinstance(result, Ok)
    assert result.value.path == "package.json"
    assert result.value.text == '{"name":"widget"}'


def test_read_manifest_matches_normalized_path() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("./package.json", "m"),)})
    result = provider.read_manifest(_REPO)
    assert isinstance(result, Ok)
    assert result.value.path == "package.json"
    assert result.value.text == "m"


def test_read_manifest_missing_is_err() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("index.js"), _fc("readme.md"))})
    result = provider.read_manifest(_REPO)
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_read_manifest_nested_package_json_is_not_manifest() -> None:
    provider = FakeRepoContentProvider(
        {_REPO: (_fc("packages/foo/package.json"), _fc("index.js"))}
    )
    result = provider.read_manifest(_REPO)
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


# ---------------------------------------------------------------------------
# Case 5: read_lockfile -> Ok(FileContent) / Ok(None) / priority ordering.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lockfile_name",
    ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"],
)
def test_read_lockfile_each_supported_name(lockfile_name: str) -> None:
    provider = FakeRepoContentProvider(
        {_REPO: (_fc("index.js"), _fc(lockfile_name, "lock-body"))}
    )
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is not None
    assert result.value.path == lockfile_name
    assert result.value.text == "lock-body"


def test_read_lockfile_none_present_is_ok_none() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("index.js"), _fc("package.json"))})
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is None


def test_read_lockfile_priority_prefers_package_lock() -> None:
    # yarn.lock precedes package-lock.json in the fixture tuple, but priority,
    # not fixture order, must decide the winner.
    provider = FakeRepoContentProvider(
        {
            _REPO: (
                _fc("yarn.lock", "yarn"),
                _fc("pnpm-lock.yaml", "pnpm"),
                _fc("package-lock.json", "npm"),
            )
        }
    )
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is not None
    assert result.value.path == "package-lock.json"


def test_read_lockfile_priority_shrinkwrap_over_yarn_and_pnpm() -> None:
    provider = FakeRepoContentProvider(
        {
            _REPO: (
                _fc("pnpm-lock.yaml", "pnpm"),
                _fc("yarn.lock", "yarn"),
                _fc("npm-shrinkwrap.json", "shrinkwrap"),
            )
        }
    )
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is not None
    assert result.value.path == "npm-shrinkwrap.json"


def test_read_lockfile_priority_yarn_over_pnpm() -> None:
    provider = FakeRepoContentProvider(
        {_REPO: (_fc("pnpm-lock.yaml", "pnpm"), _fc("yarn.lock", "yarn"))}
    )
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is not None
    assert result.value.path == "yarn.lock"


def test_read_lockfile_nested_lockfile_is_not_root_lockfile() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("sub/yarn.lock"),)})
    result = provider.read_lockfile(_REPO)
    assert isinstance(result, Ok)
    assert result.value is None


# ---------------------------------------------------------------------------
# Case 6: path traversal / absolute / empty paths -> Err at fetch and reads.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "/absolute/file.js",
        "../x.js",
        "..",
        "a/../b.js",
        "src/../../y.js",
        "..\\windows.js",
        "",
    ],
)
def test_bad_path_is_err_on_fetch(bad_path: str) -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc(bad_path),)})
    result = provider.fetch(_REPO)
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_bad_path_propagates_to_read_manifest_and_read_lockfile() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("../escape.js"),)})
    manifest = provider.read_manifest(_REPO)
    lockfile = provider.read_lockfile(_REPO)
    assert isinstance(manifest, Err)
    assert isinstance(manifest.error, IngestError)
    assert isinstance(lockfile, Err)
    assert isinstance(lockfile.error, IngestError)


def test_valid_paths_alongside_bad_path_still_err() -> None:
    provider = FakeRepoContentProvider({_REPO: (_fc("ok.js"), _fc("/etc/x"))})
    result = provider.fetch(_REPO)
    assert isinstance(result, Err)


# ---------------------------------------------------------------------------
# Case 7: duplicate raw path -> ValueError at construction;
#         normalize-collision -> Err at fetch.
# ---------------------------------------------------------------------------


def test_exact_duplicate_raw_path_raises_at_construction() -> None:
    with pytest.raises(ValueError):
        FakeRepoContentProvider({_REPO: (_fc("a.js", "1"), _fc("a.js", "2"))})


def test_normalize_collision_slash_prefix_is_err_at_fetch() -> None:
    # Distinct raw paths ("a.js" vs "./a.js") pass construction, then collide
    # after normalization -> Err at fetch (not a construction-time ValueError).
    provider = FakeRepoContentProvider({_REPO: (_fc("a.js", "1"), _fc("./a.js", "2"))})
    result = provider.fetch(_REPO)
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_normalize_collision_windows_separator_is_err_at_fetch() -> None:
    provider = FakeRepoContentProvider(
        {_REPO: (_fc("src/x.js", "1"), _fc("src\\x.js", "2"))}
    )
    result = provider.fetch(_REPO)
    assert isinstance(result, Err)
    assert isinstance(result.error, IngestError)


def test_duplicate_in_one_repo_does_not_affect_other_repo() -> None:
    with pytest.raises(ValueError):
        FakeRepoContentProvider(
            {
                _REPO: (_fc("clean.js"),),
                "https://example.com/dup.git": (_fc("d.js"), _fc("d.js")),
            }
        )


# ---------------------------------------------------------------------------
# Case 8: determinism + defensive copy of the constructor argument.
# ---------------------------------------------------------------------------


def test_fetch_is_deterministic_across_calls() -> None:
    provider = FakeRepoContentProvider(
        {_REPO: (_fc("b.js", "b"), _fc("a.js", "a"), _fc("src/z.js", "z"))}
    )
    first = provider.fetch(_REPO)
    second = provider.fetch(_REPO)
    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert first.value == second.value
    assert [file.path for file in first.value] == ["a.js", "b.js", "src/z.js"]


def test_constructor_defensively_copies_mapping() -> None:
    mapping: dict[str, tuple[FileContent, ...]] = {_REPO: (_fc("a.js"),)}
    provider = FakeRepoContentProvider(mapping)

    # Mutate the caller's mapping after construction in three ways.
    mapping["https://example.com/added.git"] = (_fc("added.js"),)
    mapping[_REPO] = (_fc("replaced.js"),)
    mapping.clear()

    result = provider.fetch(_REPO)
    assert isinstance(result, Ok)
    assert [file.path for file in result.value] == ["a.js"]
    # A repo added to the caller's mapping post-construction stays unknown.
    added = provider.fetch("https://example.com/added.git")
    assert isinstance(added, Err)


# ---------------------------------------------------------------------------
# Case 9: structural conformance to the Protocol (checked statically by mypy).
# ---------------------------------------------------------------------------


def test_structural_conformance_typechecks() -> None:
    provider: RepoContentProvider = FakeRepoContentProvider({_REPO: ()})
    result = provider.fetch(_REPO)
    assert isinstance(result, Ok)
    assert result.value == ()
