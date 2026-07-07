"""Tests for backend.config (Unit 4: Config and per-role settings).

Every case fully controls the process environment: each test first clears the
entire ``DEPCOVER_*`` namespace via ``monkeypatch`` so results never depend on
ambient env, then sets only the variables that case requires. ``load_settings``
reads ``os.environ`` directly, so this control is load-bearing for determinism.

Coverage maps to the acceptance criteria:

* FAKE mode over a fully-cleared env and the unset-default; every ``LlmRole`` is
  present and ``role()`` is total (criteria 1, 2).
* Malformed mode/degraded booleans return ``Err(ConfigError)`` rather than
  raising (criterion 3).
* LIVE happy path with all seven roles, plus ``api_key_env`` carrying the NAME
  (criterion 4).
* LIVE missing / empty / whitespace required vars return ``Err`` (criterion 5).
* Validation of non-positive ``max_tokens``/``timeout_s``, malformed numerics,
  and the ``GITHUB_POLL_INTERVAL`` floor with an exact-boundary Ok (criterion 6).
* The no-secret invariant: only ``*_env`` names are stored, never the pointed-to
  secret value (criterion 7).
* Determinism across two loads under an identical env (criterion 8).
"""

from __future__ import annotations

import os

import pytest

from backend.config import (
    MIN_GITHUB_POLL_INTERVAL_S,
    LlmRoleConfig,
    Settings,
    load_settings,
)
from backend.domain.enums import LlmRole
from backend.domain.errors import ConfigError, Err, Ok, Result

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

_ROLE_SUFFIXES: tuple[str, str, str] = ("BASE_URL", "MODEL", "API_KEY_ENV")


def _clear_depcover_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every ``DEPCOVER_*`` var so the case starts from a clean slate."""
    for key in list(os.environ):
        if key.startswith("DEPCOVER_"):
            monkeypatch.delenv(key, raising=False)


def _role_key(role: LlmRole, suffix: str) -> str:
    return f"DEPCOVER_LLM_{role.name}_{suffix}"


def _set_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate a minimal, fully-valid LIVE environment for all seven roles."""
    monkeypatch.setenv("DEPCOVER_USE_FAKES", "false")
    monkeypatch.setenv("DEPCOVER_DAYTONA_SNAPSHOT_ID", "snap-abc-123")
    for role in LlmRole:
        monkeypatch.setenv(_role_key(role, "BASE_URL"), f"https://api.example/{role.value}")
        monkeypatch.setenv(_role_key(role, "MODEL"), f"model-{role.value}")
        monkeypatch.setenv(_role_key(role, "API_KEY_ENV"), f"KEY_ENV_{role.name}")


def _load_no_raise() -> Result[Settings, ConfigError]:
    """Call ``load_settings`` and fail loudly if it violates the never-raise contract."""
    try:
        return load_settings()
    except Exception as exc:  # deliberately broad: verifying the no-raise contract
        pytest.fail(f"load_settings() raised {type(exc).__name__}: {exc}")


def _expect_ok(result: Result[Settings, ConfigError]) -> Settings:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _expect_err(result: Result[Settings, ConfigError]) -> ConfigError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    assert isinstance(result.error, ConfigError), f"error is not ConfigError: {result.error!r}"
    return result.error


def _collect_strings(settings: Settings) -> list[str]:
    """Every string leaf actually stored on a Settings instance."""
    result: list[str] = []
    optionals: tuple[str | None, ...] = (
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password_env,
        settings.butterbase_base_url,
        settings.butterbase_key_env,
        settings.github_token_env,
    )
    result.extend(value for value in optionals if value is not None)
    result.append(settings.daytona_snapshot_id)
    for config in settings.llm_roles.values():
        result.extend((config.base_url, config.model, config.api_key_env))
    return result


def _assert_role_map_complete(settings: Settings) -> None:
    assert frozenset(settings.llm_roles) == frozenset(LlmRole)
    assert len(settings.llm_roles) == 7
    for role in LlmRole:
        config = settings.role(role)
        assert isinstance(config, LlmRoleConfig)
        assert config.base_url != ""
        assert config.model != ""
        assert config.api_key_env != ""
        assert config.max_tokens > 0
        assert config.timeout_s > 0.0


# --------------------------------------------------------------------------- #
# Criterion 1 & 2: FAKE mode                                                   #
# --------------------------------------------------------------------------- #


def test_fake_mode_cleared_env_returns_ok_with_all_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_depcover_env(monkeypatch)
    settings = _expect_ok(_load_no_raise())
    assert settings.use_fakes is True
    _assert_role_map_complete(settings)


def test_fake_mode_leaves_live_fields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    settings = _expect_ok(_load_no_raise())
    assert settings.neo4j_uri is None
    assert settings.neo4j_user is None
    assert settings.neo4j_password_env is None
    assert settings.butterbase_base_url is None
    assert settings.butterbase_key_env is None
    assert settings.github_token_env is None


def test_default_mode_unset_use_fakes_defaults_to_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_depcover_env(monkeypatch)
    # DEPCOVER_USE_FAKES intentionally left unset entirely.
    settings = _expect_ok(_load_no_raise())
    assert settings.use_fakes is True
    _assert_role_map_complete(settings)


def test_fake_mode_ignores_stray_live_role_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fake mode must not require or fail on partial live config left in the env.
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_USE_FAKES", "true")
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "BASE_URL"), "")
    settings = _expect_ok(_load_no_raise())
    assert settings.use_fakes is True
    _assert_role_map_complete(settings)


# --------------------------------------------------------------------------- #
# Criterion 3: malformed boolean env vars -> Err, never raises                 #
# --------------------------------------------------------------------------- #


def test_malformed_use_fakes_yes_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_USE_FAKES", "yes")
    _expect_err(_load_no_raise())


def test_malformed_use_fakes_maybe_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_USE_FAKES", "maybe")
    _expect_err(_load_no_raise())


def test_malformed_use_fakes_empty_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_USE_FAKES", "")
    _expect_err(_load_no_raise())


def test_malformed_use_fakes_whitespace_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_USE_FAKES", "   ")
    _expect_err(_load_no_raise())


def test_use_fakes_accepts_case_insensitive_true_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for token in ("1", "true", "TRUE", "True", "  true  "):
        _clear_depcover_env(monkeypatch)
        monkeypatch.setenv("DEPCOVER_USE_FAKES", token)
        settings = _expect_ok(_load_no_raise())
        assert settings.use_fakes is True, f"token {token!r} should mean fake mode"


def test_malformed_judges_degraded_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    # Degraded flag is parsed before branching, so it errs even in fake mode.
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_JUDGES_DEGRADED", "maybe")
    _expect_err(_load_no_raise())


def test_judges_degraded_true_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_JUDGES_DEGRADED", "TRUE")
    settings = _expect_ok(_load_no_raise())
    assert settings.judges_degraded is True


# --------------------------------------------------------------------------- #
# Criterion 4: LIVE happy path                                                 #
# --------------------------------------------------------------------------- #


def test_live_happy_path_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    settings = _expect_ok(_load_no_raise())
    assert settings.use_fakes is False
    assert settings.daytona_snapshot_id == "snap-abc-123"
    _assert_role_map_complete(settings)


def test_live_role_carries_names_not_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    settings = _expect_ok(_load_no_raise())
    for role in LlmRole:
        config = settings.role(role)
        assert config.base_url == f"https://api.example/{role.value}"
        assert config.model == f"model-{role.value}"
        # api_key_env stores the VARIABLE NAME the operator supplied.
        assert config.api_key_env == f"KEY_ENV_{role.name}"


def test_live_required_var_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_DAYTONA_SNAPSHOT_ID", "   snap-trimmed   ")
    settings = _expect_ok(_load_no_raise())
    assert settings.daytona_snapshot_id == "snap-trimmed"


def test_live_optional_whitespace_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_NEO4J_URI", "   ")
    settings = _expect_ok(_load_no_raise())
    assert settings.neo4j_uri is None


# --------------------------------------------------------------------------- #
# Criterion 5: LIVE missing / empty / whitespace required vars -> Err          #
# --------------------------------------------------------------------------- #


def test_live_missing_snapshot_id_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.delenv("DEPCOVER_DAYTONA_SNAPSHOT_ID", raising=False)
    _expect_err(_load_no_raise())


def test_live_missing_one_role_base_url_returns_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.delenv(_role_key(LlmRole.JUDGE_SECURITY, "BASE_URL"), raising=False)
    _expect_err(_load_no_raise())


def test_live_missing_one_role_model_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.delenv(_role_key(LlmRole.PR_SCREEN, "MODEL"), raising=False)
    _expect_err(_load_no_raise())


def test_live_missing_one_role_api_key_env_returns_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.delenv(_role_key(LlmRole.MITIGATION, "API_KEY_ENV"), raising=False)
    _expect_err(_load_no_raise())


def test_live_empty_snapshot_id_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_DAYTONA_SNAPSHOT_ID", "")
    _expect_err(_load_no_raise())


def test_live_whitespace_role_model_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.JUDGE_RECIPE, "MODEL"), "   ")
    _expect_err(_load_no_raise())


def test_live_every_required_role_field_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Adversarial completeness: dropping ANY single required field of ANY role errs.
    for role in LlmRole:
        for suffix in _ROLE_SUFFIXES:
            _clear_depcover_env(monkeypatch)
            _set_live_env(monkeypatch)
            monkeypatch.delenv(_role_key(role, suffix), raising=False)
            result = _load_no_raise()
            assert isinstance(result, Err), (
                f"missing {role.name}/{suffix} should be Err, got {result!r}"
            )


# --------------------------------------------------------------------------- #
# Criterion 6: numeric validation and poll-interval floor                      #
# --------------------------------------------------------------------------- #


def test_live_zero_timeout_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "TIMEOUT_S"), "0")
    _expect_err(_load_no_raise())


def test_live_negative_timeout_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "TIMEOUT_S"), "-1")
    _expect_err(_load_no_raise())


def test_live_zero_max_tokens_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "MAX_TOKENS"), "0")
    _expect_err(_load_no_raise())


def test_live_negative_max_tokens_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "MAX_TOKENS"), "-5")
    _expect_err(_load_no_raise())


def test_live_malformed_max_tokens_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "MAX_TOKENS"), "abc")
    _expect_err(_load_no_raise())


def test_live_float_max_tokens_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    # "1.5" is not a valid integer; must not silently truncate.
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "MAX_TOKENS"), "1.5")
    _expect_err(_load_no_raise())


def test_live_malformed_timeout_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "TIMEOUT_S"), "notafloat")
    _expect_err(_load_no_raise())


def test_poll_interval_below_floor_returns_err(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(
        "DEPCOVER_GITHUB_POLL_INTERVAL_S", str(MIN_GITHUB_POLL_INTERVAL_S - 1.0)
    )
    _expect_err(_load_no_raise())


def test_poll_interval_just_below_floor_returns_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(
        "DEPCOVER_GITHUB_POLL_INTERVAL_S", str(MIN_GITHUB_POLL_INTERVAL_S - 0.000001)
    )
    _expect_err(_load_no_raise())


def test_poll_interval_exact_floor_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv(
        "DEPCOVER_GITHUB_POLL_INTERVAL_S", str(MIN_GITHUB_POLL_INTERVAL_S)
    )
    settings = _expect_ok(_load_no_raise())
    assert settings.github_poll_interval_s == MIN_GITHUB_POLL_INTERVAL_S


def test_poll_interval_above_floor_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_GITHUB_POLL_INTERVAL_S", "30")
    settings = _expect_ok(_load_no_raise())
    assert settings.github_poll_interval_s == 30.0


def test_live_zero_sandbox_exec_timeout_returns_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    monkeypatch.setenv("DEPCOVER_SANDBOX_EXEC_TIMEOUT_S", "0")
    _expect_err(_load_no_raise())


# --------------------------------------------------------------------------- #
# Criterion 7: no-secret invariant                                            #
# --------------------------------------------------------------------------- #


def test_no_secret_value_is_read_or_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    secret = "sk-super-secret-DO-NOT-STORE-abc123"
    # Point a role's api_key_env at a REAL env var that holds the secret value.
    monkeypatch.setenv(_role_key(LlmRole.TRANSPLANT, "API_KEY_ENV"), "TRANSPLANT_SECRET_KEY")
    monkeypatch.setenv("TRANSPLANT_SECRET_KEY", secret)
    settings = _expect_ok(_load_no_raise())
    # The stored value is the NAME, not the secret it points to.
    assert settings.role(LlmRole.TRANSPLANT).api_key_env == "TRANSPLANT_SECRET_KEY"
    # No string field anywhere on Settings carries the secret value.
    for value in _collect_strings(settings):
        assert value != secret, f"secret value leaked into Settings field: {value!r}"


def test_settings_only_carries_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    # Provide neo4j password + butterbase key as NAMES pointing at secret values.
    monkeypatch.setenv("DEPCOVER_NEO4J_PASSWORD_ENV", "NEO4J_PW_VAR")
    monkeypatch.setenv("NEO4J_PW_VAR", "neo4j-plaintext-secret")
    monkeypatch.setenv("DEPCOVER_BUTTERBASE_KEY_ENV", "BUTTERBASE_KEY_VAR")
    monkeypatch.setenv("BUTTERBASE_KEY_VAR", "butterbase-plaintext-secret")
    settings = _expect_ok(_load_no_raise())
    assert settings.neo4j_password_env == "NEO4J_PW_VAR"
    assert settings.butterbase_key_env == "BUTTERBASE_KEY_VAR"
    stored = _collect_strings(settings)
    assert "neo4j-plaintext-secret" not in stored
    assert "butterbase-plaintext-secret" not in stored


# --------------------------------------------------------------------------- #
# Criterion 8: determinism                                                     #
# --------------------------------------------------------------------------- #


def test_determinism_live_identical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    _set_live_env(monkeypatch)
    first = _expect_ok(_load_no_raise())
    second = _expect_ok(_load_no_raise())
    assert first == second


def test_determinism_fake_identical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_depcover_env(monkeypatch)
    first = _expect_ok(_load_no_raise())
    second = _expect_ok(_load_no_raise())
    assert first == second
