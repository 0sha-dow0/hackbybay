"""Tests for backend.services.underwriter.Underwriter (Unit 18, kill-test
underwriter, AFTER its error-channel amendment).

Bound to the amended contract:

* ``run(repo, surgery_plan, centrality, layout, warnings)`` returns
  ``Result[UnderwritingReport, DepCoverError]`` and NEVER raises;
* the sandbox is driven with the exact argv sequence
  ``("rm", "-rf", "node_modules/<target>")`` then ``("npm", "test")``, each at
  ``settings.sandbox_exec_timeout_s``;
* failing tests are parsed defensively from TAP ``not ok`` lines;
* ``affected_file_count == len(surgery_plan.affected_files)``;
* an acquire failure -> ``Err(SandboxUnavailableError)`` and nothing persisted;
* a save conflict -> ``Err(RecordStoreError)`` returned (not raised);
* a TIMEOUT outcome yields a report with a timeout-derived note, no hang;
* the sandbox is ALWAYS released (capacity-1 reacquire succeeds afterwards);
* the report is persisted before ``Ok`` is returned and is retrievable;
* identical inputs + fixed clock/ids/script -> identical report.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timezone

import pytest

from backend.adapters.fake.fake_record_store import FakeRecordStore
from backend.adapters.fake.fake_sandbox import FakeSandbox
from backend.config import Settings, load_settings
from backend.domain.determinism import FixedClock, SequentialIdGenerator
from backend.domain.enums import GraphEdgeKind, GraphNodeKind, SandboxOutcome
from backend.domain.errors import (
    DepCoverError,
    Err,
    Ok,
    RecordStoreError,
    Result,
    SandboxError,
    SandboxUnavailableError,
)
from backend.domain.models import (
    CallSite,
    CentralityScore,
    GraphEdge,
    GraphLayout,
    GraphLayoutNode,
    LockfileWarning,
    Repo,
    SurgeryPlan,
    UnderwritingReport,
)
from backend.ports.sandbox import SandboxResult
from backend.services.underwriter import Underwriter

_START = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
_TARGET = "left-pad"
_TEST_ARGV: tuple[str, ...] = ("npm", "test")


# --------------------------------------------------------------------------- #
# Builders bound to the real schema / argv contract.
# --------------------------------------------------------------------------- #
def _repo(repo_id: str = "repo-1") -> Repo:
    return Repo(
        id=repo_id,
        url="https://github.com/acme/app",
        owner="acme",
        registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _call_site(path: str) -> CallSite:
    return CallSite(
        file_path=path,
        line=1,
        symbol="require",
        is_aliased=False,
        alias=None,
        snippet="require('left-pad')",
    )


def _surgery_plan(target: str, files: tuple[str, ...]) -> SurgeryPlan:
    call_sites = tuple(_call_site(path) for path in files)
    affected = tuple(sorted({cs.file_path for cs in call_sites}))
    return SurgeryPlan(
        target_package=target, call_sites=call_sites, affected_files=affected
    )


def _layout() -> GraphLayout:
    return GraphLayout(
        nodes=(
            GraphLayoutNode(
                id="n1", x=1.0, y=2.0, kind=GraphNodeKind.PACKAGE, label=_TARGET
            ),
        ),
        edges=(GraphEdge(src="n1", dst="n2", kind=GraphEdgeKind.IMPORTS),),
    )


def _removal_argv(target: str) -> tuple[str, ...]:
    return ("rm", "-rf", f"node_modules/{target}")


def _passed(stdout: str = "") -> SandboxResult:
    return SandboxResult(
        outcome=SandboxOutcome.PASSED,
        exit_code=0,
        stdout=stdout,
        stderr="",
        duration_s=0.01,
    )


def _script(
    target: str, test_result: SandboxResult
) -> dict[tuple[str, ...], SandboxResult]:
    return {_removal_argv(target): _passed(), _TEST_ARGV: test_result}


def _make(
    sandbox: FakeSandbox,
    store: FakeRecordStore,
    settings: Settings,
    *,
    step_s: float = 0.0,
    seed: int = 0,
) -> Underwriter:
    return Underwriter(
        sandbox,
        store,
        settings,
        FixedClock(_START, step_s),
        SequentialIdGenerator(seed),
    )


def _run_no_raise(
    uw: Underwriter,
    repo: Repo,
    plan: SurgeryPlan,
    centrality: Sequence[CentralityScore],
    layout: GraphLayout,
    warnings: Sequence[LockfileWarning],
) -> Result[UnderwritingReport, DepCoverError]:
    """Invoke run and fail loudly if the never-raise contract is violated."""
    try:
        return uw.run(repo, plan, centrality, layout, warnings)
    except Exception as exc:  # verifying the no-raise contract, so catch broadly
        pytest.fail(f"run() raised {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Fixture: fake-mode Settings over a fully cleared DEPCOVER_* env.
# --------------------------------------------------------------------------- #
@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in list(os.environ):
        if key.startswith("DEPCOVER_"):
            monkeypatch.delenv(key, raising=False)
    result = load_settings()
    assert isinstance(result, Ok), repr(result)
    value = result.value
    assert value.sandbox_exec_timeout_s > 0.0
    assert value.daytona_snapshot_id != ""
    return value


# --------------------------------------------------------------------------- #
# Case 1: happy path.
# --------------------------------------------------------------------------- #
def test_happy_path_parses_persists_and_carries_context(settings: Settings) -> None:
    repo = _repo()
    plan = _surgery_plan(_TARGET, ("src/a.js", "src/b.js"))
    centrality = [CentralityScore(package=_TARGET, score=0.5)]
    layout = _layout()
    warnings: tuple[LockfileWarning, ...] = (
        LockfileWarning(shape="lockfile_drift", reason="resolved version drifted"),
    )
    stdout = (
        "TAP version 13\n"
        "1..3\n"
        "ok 1 - passes cleanly\n"
        "not ok 2 - alpha explodes\n"
        "not ok 3 - beta explodes\n"
    )
    test_result = SandboxResult(
        outcome=SandboxOutcome.FAILED,
        exit_code=1,
        stdout=stdout,
        stderr="",
        duration_s=1.5,
    )
    sandbox = FakeSandbox(_script(_TARGET, test_result))
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, repo, plan, centrality, layout, warnings)

    assert isinstance(result, Ok), repr(result)
    report = result.value
    assert report.failing_tests == ("alpha explodes", "beta explodes")
    assert report.affected_file_count == len(plan.affected_files) == 2
    assert report.centrality == tuple(centrality)
    assert report.graph_layout == layout
    assert report.warnings == warnings  # FAILED-with-parsed adds no extra note
    assert report.created_at == _START
    assert report.repo_id == repo.id
    assert report.target_package == _TARGET
    assert report.id == "underwriting-00000000"

    fetched = store.get_underwriting(repo.id)
    assert isinstance(fetched, Ok), repr(fetched)
    assert fetched.value == report


# --------------------------------------------------------------------------- #
# Case 2: TIMEOUT -> report with timeout note, no hang, sandbox released.
# --------------------------------------------------------------------------- #
def test_timeout_yields_timeout_note_without_hanging(settings: Settings) -> None:
    repo = _repo()
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    test_result = SandboxResult(
        outcome=SandboxOutcome.TIMEOUT,
        exit_code=None,
        stdout="",
        stderr="killed after timeout",
        duration_s=settings.sandbox_exec_timeout_s,
    )
    sandbox = FakeSandbox(_script(_TARGET, test_result))
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, repo, plan, [], _layout(), ())

    assert isinstance(result, Ok), repr(result)
    report = result.value
    assert report.failing_tests == ()
    shapes = tuple(w.shape for w in report.warnings)
    assert "test_suite_timeout" in shapes
    assert isinstance(store.get_underwriting(repo.id), Ok)
    # Sandbox released: a capacity-1 reacquire succeeds.
    assert isinstance(sandbox.acquire(settings.daytona_snapshot_id), Ok)


# --------------------------------------------------------------------------- #
# Case 3: acquire failure -> Err(SandboxUnavailableError), nothing persisted.
# --------------------------------------------------------------------------- #
def test_acquire_failure_returns_unavailable_and_persists_nothing(
    settings: Settings,
) -> None:
    repo = _repo()
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    sandbox = FakeSandbox(_script(_TARGET, _passed()), capacity=0)
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, repo, plan, [], _layout(), ())

    assert isinstance(result, Err), repr(result)
    assert isinstance(result.error, SandboxUnavailableError)
    assert isinstance(store.get_underwriting(repo.id), Err)


# --------------------------------------------------------------------------- #
# Case 4: persistence conflict -> Err(RecordStoreError) RETURNED, not raised.
# --------------------------------------------------------------------------- #
def test_persistence_conflict_returns_recordstore_err_not_raised(
    settings: Settings,
) -> None:
    repo = _repo()
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    # Pre-seed a DIFFERENT report keyed by the same repo_id -> save conflicts.
    existing = UnderwritingReport(
        id="underwriting-preexisting",
        repo_id=repo.id,
        target_package="other-package",
        failing_tests=("pre-existing failure",),
        affected_file_count=0,
        centrality=(),
        graph_layout=_layout(),
        warnings=(),
        created_at=_START,
    )
    store = FakeRecordStore(FixedClock(_START, 0.0))
    assert isinstance(store.save_underwriting(existing), Ok)
    sandbox = FakeSandbox(_script(_TARGET, _passed("ok 1 - fine\n")))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, repo, plan, [], _layout(), ())

    assert isinstance(result, Err), repr(result)
    assert isinstance(result.error, RecordStoreError)
    # The store is unchanged: still the pre-existing report.
    fetched = store.get_underwriting(repo.id)
    assert isinstance(fetched, Ok), repr(fetched)
    assert fetched.value == existing
    # Sandbox released even though persistence failed.
    assert isinstance(sandbox.acquire(settings.daytona_snapshot_id), Ok)


# --------------------------------------------------------------------------- #
# Case 5: sandbox always released after a successful run.
# --------------------------------------------------------------------------- #
def test_sandbox_released_after_success_allows_reacquire(settings: Settings) -> None:
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    test_result = SandboxResult(
        outcome=SandboxOutcome.FAILED,
        exit_code=1,
        stdout="not ok 1 - x\n",
        stderr="",
        duration_s=0.1,
    )
    sandbox = FakeSandbox(_script(_TARGET, test_result), capacity=1)
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, _repo(), plan, [], _layout(), ())

    assert isinstance(result, Ok), repr(result)
    # Capacity is 1: reacquire only succeeds if run released its handle.
    assert isinstance(sandbox.acquire(settings.daytona_snapshot_id), Ok)


def test_removal_exec_failure_releases_and_persists_nothing(
    settings: Settings,
) -> None:
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    # Only the test command is scripted; the removal command is unscripted,
    # so the first exec returns Err on the DepCoverError channel.
    sandbox = FakeSandbox({_TEST_ARGV: _passed()}, capacity=1)
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, _repo(), plan, [], _layout(), ())

    assert isinstance(result, Err), repr(result)
    assert isinstance(result.error, SandboxError)
    assert isinstance(store.get_underwriting(_repo().id), Err)
    # Sandbox still released despite the exec failure.
    assert isinstance(sandbox.acquire(settings.daytona_snapshot_id), Ok)


# --------------------------------------------------------------------------- #
# Case 6: affected_file_count for a multi-file plan.
# --------------------------------------------------------------------------- #
def test_affected_file_count_matches_multifile_plan(settings: Settings) -> None:
    files = ("src/a.js", "src/b.js", "src/c.js", "src/d.js")
    plan = _surgery_plan(_TARGET, files)
    assert len(plan.affected_files) == 4
    sandbox = FakeSandbox(_script(_TARGET, _passed()))
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, _repo(), plan, [], _layout(), ())

    assert isinstance(result, Ok), repr(result)
    assert result.value.affected_file_count == 4


# --------------------------------------------------------------------------- #
# Case 7: malformed / empty stdout parsed defensively, never crashing.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("", ()),
        ("garbage\nnothing to see here", ()),
        ("not okay 1 - not a real fail\n", ()),  # 'not okay' != 'not ok '
        ("not ok\n", ("not ok",)),  # bare marker -> best-effort echo
        ("  not ok 7 - indented name  \n", ("indented name",)),
        ("not ok - dashed only\n", ("dashed only",)),
        ("not ok plain words\n", ("plain words",)),
    ],
)
def test_malformed_stdout_parsed_defensively(
    stdout: str, expected: tuple[str, ...], settings: Settings
) -> None:
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    sandbox = FakeSandbox(_script(_TARGET, _passed(stdout)))
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, _repo(), plan, [], _layout(), ())

    assert isinstance(result, Ok), repr(result)
    assert result.value.failing_tests == expected


def test_failed_without_parseable_tests_emits_unparsed_note(
    settings: Settings,
) -> None:
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    test_result = SandboxResult(
        outcome=SandboxOutcome.FAILED,
        exit_code=1,
        stdout="the suite failed but printed no TAP lines\n",
        stderr="",
        duration_s=0.2,
    )
    sandbox = FakeSandbox(_script(_TARGET, test_result))
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, _repo(), plan, [], _layout(), ())

    assert isinstance(result, Ok), repr(result)
    assert result.value.failing_tests == ()
    shapes = tuple(w.shape for w in result.value.warnings)
    assert "test_output_unrecognized" in shapes


def test_error_outcome_emits_error_note(settings: Settings) -> None:
    plan = _surgery_plan(_TARGET, ("src/a.js",))
    test_result = SandboxResult(
        outcome=SandboxOutcome.ERROR,
        exit_code=127,
        stdout="",
        stderr="runner crashed",
        duration_s=0.2,
    )
    sandbox = FakeSandbox(_script(_TARGET, test_result))
    store = FakeRecordStore(FixedClock(_START, 0.0))
    uw = _make(sandbox, store, settings)

    result = _run_no_raise(uw, _repo(), plan, [], _layout(), ())

    assert isinstance(result, Ok), repr(result)
    shapes = tuple(w.shape for w in result.value.warnings)
    assert "test_suite_error" in shapes


# --------------------------------------------------------------------------- #
# Case 8: determinism.
# --------------------------------------------------------------------------- #
def test_determinism_identical_inputs_yield_identical_report(
    settings: Settings,
) -> None:
    def _one() -> UnderwritingReport:
        repo = _repo()
        plan = _surgery_plan(_TARGET, ("src/a.js", "src/b.js"))
        stdout = "not ok 1 - a\nnot ok 2 - b\n"
        test_result = SandboxResult(
            outcome=SandboxOutcome.FAILED,
            exit_code=1,
            stdout=stdout,
            stderr="",
            duration_s=2.0,
        )
        sandbox = FakeSandbox(_script(_TARGET, test_result))
        store = FakeRecordStore(FixedClock(_START, 0.0))
        uw = _make(sandbox, store, settings, step_s=5.0)
        report_result = uw.run(
            repo,
            plan,
            [CentralityScore(package=_TARGET, score=0.5)],
            _layout(),
            (LockfileWarning(shape="lockfile_drift", reason="drifted"),),
        )
        assert isinstance(report_result, Ok), repr(report_result)
        return report_result.value

    first = _one()
    second = _one()

    assert first == second
    assert first.id == second.id == "underwriting-00000000"
    assert first.created_at == second.created_at == _START
    assert first.failing_tests == second.failing_tests == ("a", "b")
