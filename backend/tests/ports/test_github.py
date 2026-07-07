"""Tests for the GitHubClient port and its in-memory FakeGitHubClient (Unit 12).

Covers every Unit 12 acceptance criterion plus adversarial inputs:

* open_pr / post_comment idempotency by key (and scoping per repo / per PR);
* empty idempotency_key surfaces Err(GitHubError) on both mutating methods;
* rate limiting surfaces Err(RateLimitError) on all four methods;
* list_open_prs / get_pr_files projection, ordering and path normalization;
* unknown repo / unknown PR error channels;
* invalid-fixture rejection at construction (ValueError);
* determinism of allocated numbers, urls and head_sha;
* structural conformance to the GitHubClient Protocol (scoped mypy).
"""

from __future__ import annotations

from typing import TypeVar

import pytest

from backend.adapters.fake.fake_github import (
    FakeGitHubClient,
    RecordedComment,
    SeededPr,
)
from backend.domain.errors import Err, GitHubError, Ok, RateLimitError, Result
from backend.domain.models import FileContent, PullRequestRef, RewrittenFile
from backend.ports.github import GitHubClient, NewPr, PrSummary

_T = TypeVar("_T")

REPO = "https://github.com/acme/widgets"
OTHER_REPO = "https://github.com/acme/gadgets"
REPO_TRAILING_SLASH = "https://github.com/acme/widgets/"


def _ok(result: Result[_T, GitHubError]) -> _T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _err(result: Result[_T, GitHubError]) -> GitHubError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


def _new_pr(
    *,
    title: str = "Transplant axios -> fetch",
    body: str = "Automated transplant.",
    head_branch: str = "depcover/transplant",
    base_branch: str = "main",
    files: tuple[RewrittenFile, ...] = (),
) -> NewPr:
    return NewPr(
        title=title,
        body=body,
        head_branch=head_branch,
        base_branch=base_branch,
        files=files,
    )


# --- Case 1: open_pr idempotency -------------------------------------------


def test_open_pr_same_key_returns_single_pr_and_identical_ref() -> None:
    client = FakeGitHubClient({REPO: ()})
    first = _ok(client.open_pr(REPO, _new_pr(), "key-1"))
    second = _ok(client.open_pr(REPO, _new_pr(), "key-1"))

    assert isinstance(first, PullRequestRef)
    assert first == second

    summaries = _ok(client.list_open_prs(REPO))
    assert len(summaries) == 1
    assert summaries[0].number == first.number


def test_open_pr_different_key_returns_distinct_pr() -> None:
    client = FakeGitHubClient({REPO: ()})
    first = _ok(client.open_pr(REPO, _new_pr(), "key-1"))
    second = _ok(client.open_pr(REPO, _new_pr(), "key-2"))

    assert first.number != second.number
    assert first.url != second.url
    assert len(_ok(client.list_open_prs(REPO))) == 2


def test_open_pr_idempotent_key_ignores_second_payload() -> None:
    client = FakeGitHubClient({REPO: ()})
    files_a = (RewrittenFile(path="src/a.js", text="A"),)
    files_b = (RewrittenFile(path="src/b.js", text="B"),)
    first = _ok(client.open_pr(REPO, _new_pr(files=files_a), "key-1"))
    second = _ok(client.open_pr(REPO, _new_pr(title="different", files=files_b), "key-1"))

    assert first == second
    stored = _ok(client.get_pr_files(REPO, first.number))
    assert tuple(f.path for f in stored) == ("src/a.js",)


def test_open_pr_key_is_scoped_per_repo() -> None:
    client = FakeGitHubClient({REPO: (), OTHER_REPO: ()})
    here = _ok(client.open_pr(REPO, _new_pr(), "shared-key"))
    there = _ok(client.open_pr(OTHER_REPO, _new_pr(), "shared-key"))

    assert here.url.startswith(REPO)
    assert there.url.startswith(OTHER_REPO)
    assert len(_ok(client.list_open_prs(REPO))) == 1
    assert len(_ok(client.list_open_prs(OTHER_REPO))) == 1


def test_open_pr_numbers_start_after_max_seeded() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(2, "sha-2", ()), SeededPr(5, "sha-5", ()))})
    first = _ok(client.open_pr(REPO, _new_pr(), "key-1"))
    second = _ok(client.open_pr(REPO, _new_pr(), "key-2"))

    assert first.number == 6
    assert second.number == 7


def test_open_pr_first_number_is_one_for_empty_repo() -> None:
    client = FakeGitHubClient({REPO: ()})
    ref = _ok(client.open_pr(REPO, _new_pr(), "key-1"))
    assert ref.number == 1
    assert ref.url == f"{REPO}/pull/1"


def test_open_pr_url_strips_trailing_slash() -> None:
    client = FakeGitHubClient({REPO_TRAILING_SLASH: ()})
    ref = _ok(client.open_pr(REPO_TRAILING_SLASH, _new_pr(), "key-1"))
    assert ref.url == "https://github.com/acme/widgets/pull/1"


def test_open_pr_normalizes_file_paths() -> None:
    client = FakeGitHubClient({REPO: ()})
    files = (
        RewrittenFile(path="src/./client.js", text="x"),
        RewrittenFile(path="lib\\util.js", text="y"),
    )
    ref = _ok(client.open_pr(REPO, _new_pr(files=files), "key-1"))
    stored = _ok(client.get_pr_files(REPO, ref.number))
    assert tuple(f.path for f in stored) == ("lib/util.js", "src/client.js")


def test_open_pr_with_duplicate_file_paths_is_err() -> None:
    client = FakeGitHubClient({REPO: ()})
    files = (
        RewrittenFile(path="a.js", text="1"),
        RewrittenFile(path="./a.js", text="2"),
    )
    error = _err(client.open_pr(REPO, _new_pr(files=files), "key-1"))
    assert isinstance(error, GitHubError)
    assert not isinstance(error, RateLimitError)


def test_open_pr_with_traversal_file_path_is_err() -> None:
    client = FakeGitHubClient({REPO: ()})
    files = (RewrittenFile(path="../escape.js", text="x"),)
    error = _err(client.open_pr(REPO, _new_pr(files=files), "key-1"))
    assert isinstance(error, GitHubError)


def test_open_pr_unknown_repo_is_err() -> None:
    client = FakeGitHubClient({REPO: ()})
    error = _err(client.open_pr("https://github.com/nope/nope", _new_pr(), "key-1"))
    assert isinstance(error, GitHubError)
    assert not isinstance(error, RateLimitError)


# --- Case 2: post_comment idempotency --------------------------------------


def test_post_comment_same_key_records_once() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    assert isinstance(client.post_comment(REPO, 1, "hello", "poll-key"), Ok)
    assert isinstance(client.post_comment(REPO, 1, "hello", "poll-key"), Ok)

    comments = client.recorded_comments(REPO)
    assert comments == (RecordedComment(pr_number=1, body="hello", idempotency_key="poll-key"),)
    assert len(comments) == 1


def test_post_comment_different_key_records_second() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    _ok(client.post_comment(REPO, 1, "first", "key-a"))
    _ok(client.post_comment(REPO, 1, "second", "key-b"))

    comments = client.recorded_comments(REPO)
    assert len(comments) == 2
    assert [c.body for c in comments] == ["first", "second"]


def test_post_comment_same_key_scoped_per_pr_number() -> None:
    client = FakeGitHubClient(
        {REPO: (SeededPr(1, "sha-1", ()), SeededPr(2, "sha-2", ()))}
    )
    _ok(client.post_comment(REPO, 1, "on pr 1", "same-key"))
    _ok(client.post_comment(REPO, 2, "on pr 2", "same-key"))

    comments = client.recorded_comments(REPO)
    assert len(comments) == 2
    assert {c.pr_number for c in comments} == {1, 2}


def test_post_comment_unknown_pr_is_err() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    error = _err(client.post_comment(REPO, 999, "body", "key"))
    assert isinstance(error, GitHubError)
    assert client.recorded_comments(REPO) == ()


def test_post_comment_unknown_repo_is_err() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    error = _err(client.post_comment("https://github.com/x/y", 1, "body", "key"))
    assert isinstance(error, GitHubError)


# --- Case 3: empty idempotency_key -----------------------------------------


def test_open_pr_empty_key_is_github_error() -> None:
    client = FakeGitHubClient({REPO: ()})
    error = _err(client.open_pr(REPO, _new_pr(), ""))
    assert isinstance(error, GitHubError)
    assert not isinstance(error, RateLimitError)
    assert _ok(client.list_open_prs(REPO)) == ()


def test_post_comment_empty_key_is_github_error() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    error = _err(client.post_comment(REPO, 1, "body", ""))
    assert isinstance(error, GitHubError)
    assert not isinstance(error, RateLimitError)
    assert client.recorded_comments(REPO) == ()


# --- Case 4: rate limiting -------------------------------------------------


def test_rate_limited_open_pr() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)}, rate_limited=True)
    assert isinstance(_err(client.open_pr(REPO, _new_pr(), "key")), RateLimitError)


def test_rate_limited_list_open_prs() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)}, rate_limited=True)
    assert isinstance(_err(client.list_open_prs(REPO)), RateLimitError)


def test_rate_limited_get_pr_files() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)}, rate_limited=True)
    assert isinstance(_err(client.get_pr_files(REPO, 1)), RateLimitError)


def test_rate_limited_post_comment() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)}, rate_limited=True)
    assert isinstance(_err(client.post_comment(REPO, 1, "body", "key")), RateLimitError)


def test_rate_limited_records_no_comment() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)}, rate_limited=True)
    client.post_comment(REPO, 1, "body", "key")
    assert client.recorded_comments(REPO) == ()


# --- Case 5: list_open_prs / get_pr_files projection -----------------------


def test_list_open_prs_projects_seeded_prs_sorted_by_number() -> None:
    seeded = (
        SeededPr(5, "sha-5", (FileContent(path="e.js", text="e"),)),
        SeededPr(1, "sha-1", (FileContent(path="a.js", text="a"),)),
        SeededPr(3, "sha-3", (FileContent(path="c.js", text="c"),)),
    )
    client = FakeGitHubClient({REPO: seeded})
    summaries = _ok(client.list_open_prs(REPO))

    assert [s.number for s in summaries] == [1, 3, 5]
    assert summaries[0] == PrSummary(number=1, head_sha="sha-1", changed_files=("a.js",))
    assert all(isinstance(s, PrSummary) for s in summaries)


def test_list_open_prs_empty_repo_is_ok_empty() -> None:
    client = FakeGitHubClient({REPO: ()})
    assert _ok(client.list_open_prs(REPO)) == ()


def test_list_open_prs_unknown_repo_is_err() -> None:
    client = FakeGitHubClient({})
    error = _err(client.list_open_prs("https://github.com/unknown/repo"))
    assert isinstance(error, GitHubError)


def test_get_pr_files_returns_only_that_prs_files_normalized() -> None:
    seeded = (
        SeededPr(1, "sha-1", (FileContent(path="src/./one.js", text="1"),)),
        SeededPr(2, "sha-2", (FileContent(path="two\\two.js", text="2"),)),
    )
    client = FakeGitHubClient({REPO: seeded})

    files_one = _ok(client.get_pr_files(REPO, 1))
    files_two = _ok(client.get_pr_files(REPO, 2))

    assert tuple(f.path for f in files_one) == ("src/one.js",)
    assert tuple(f.path for f in files_two) == ("two/two.js",)
    assert all(isinstance(f, FileContent) for f in files_one)


def test_get_pr_files_unknown_number_is_err() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    error = _err(client.get_pr_files(REPO, 42))
    assert isinstance(error, GitHubError)


def test_get_pr_files_unknown_repo_is_err() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    error = _err(client.get_pr_files("https://github.com/x/y", 1))
    assert isinstance(error, GitHubError)


# --- Case 6: invalid fixtures reject at construction -----------------------


def test_construction_rejects_empty_repo_url() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({"": ()})


def test_construction_rejects_zero_pr_number() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(0, "sha", ()),)})


def test_construction_rejects_negative_pr_number() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(-3, "sha", ()),)})


def test_construction_accepts_pr_number_one_boundary() -> None:
    client = FakeGitHubClient({REPO: (SeededPr(1, "sha-1", ()),)})
    assert [s.number for s in _ok(client.list_open_prs(REPO))] == [1]


def test_construction_rejects_duplicate_pr_number() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "sha-a", ()), SeededPr(1, "sha-b", ()))})


def test_construction_rejects_empty_head_sha() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "", ()),)})


def test_construction_rejects_absolute_file_path() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "sha", (FileContent(path="/etc/passwd", text="x"),)),)})


def test_construction_rejects_parent_traversal_file_path() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "sha", (FileContent(path="../x.js", text="x"),)),)})


def test_construction_rejects_dotdot_file_path() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "sha", (FileContent(path="..", text="x"),)),)})


def test_construction_rejects_empty_file_path() -> None:
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "sha", (FileContent(path="", text="x"),)),)})


def test_construction_rejects_duplicate_file_paths() -> None:
    files = (FileContent(path="a.js", text="1"), FileContent(path="./a.js", text="2"))
    with pytest.raises(ValueError):
        FakeGitHubClient({REPO: (SeededPr(1, "sha", files),)})


# --- Case 7: determinism ----------------------------------------------------


def test_determinism_identical_construction_and_calls() -> None:
    seeded = {REPO: (SeededPr(1, "sha-1", (FileContent(path="a.js", text="a"),)),)}
    payload = _new_pr(files=(RewrittenFile(path="src/new.js", text="body"),))

    client_a = FakeGitHubClient(seeded)
    client_b = FakeGitHubClient(seeded)

    ref_a = _ok(client_a.open_pr(REPO, payload, "key-1"))
    ref_b = _ok(client_b.open_pr(REPO, payload, "key-1"))
    assert ref_a == ref_b

    summaries_a = _ok(client_a.list_open_prs(REPO))
    summaries_b = _ok(client_b.list_open_prs(REPO))
    assert summaries_a == summaries_b


def test_determinism_head_sha_stable_for_identical_payload() -> None:
    payload = _new_pr(files=(RewrittenFile(path="x.js", text="content"),))
    client_a = FakeGitHubClient({REPO: ()})
    client_b = FakeGitHubClient({REPO: ()})
    num_a = _ok(client_a.open_pr(REPO, payload, "k")).number
    num_b = _ok(client_b.open_pr(REPO, payload, "k")).number

    sha_a = next(s.head_sha for s in _ok(client_a.list_open_prs(REPO)) if s.number == num_a)
    sha_b = next(s.head_sha for s in _ok(client_b.list_open_prs(REPO)) if s.number == num_b)
    assert sha_a == sha_b


def test_head_sha_differs_for_different_payload() -> None:
    client = FakeGitHubClient({REPO: ()})
    ref_1 = _ok(client.open_pr(REPO, _new_pr(body="first"), "k1"))
    ref_2 = _ok(client.open_pr(REPO, _new_pr(body="second"), "k2"))

    by_number = {s.number: s.head_sha for s in _ok(client.list_open_prs(REPO))}
    assert by_number[ref_1.number] != by_number[ref_2.number]


# --- Case 8: structural conformance (scoped mypy) --------------------------


def _accepts_port(client: GitHubClient) -> GitHubClient:
    return client


def test_structural_conformance_to_port() -> None:
    client: GitHubClient = FakeGitHubClient({})
    assert _accepts_port(client) is client


# --- Misc introspection ----------------------------------------------------


def test_recorded_comments_unknown_repo_is_empty_tuple() -> None:
    client = FakeGitHubClient({REPO: ()})
    assert client.recorded_comments("https://github.com/unknown/repo") == ()


def test_open_pr_with_empty_files_is_ok() -> None:
    client = FakeGitHubClient({REPO: ()})
    ref = _ok(client.open_pr(REPO, _new_pr(files=()), "key-1"))
    assert _ok(client.get_pr_files(REPO, ref.number)) == ()
