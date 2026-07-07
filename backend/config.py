import os
from collections.abc import Mapping
from typing import Annotated, Final, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)

from backend.domain.enums import LlmRole
from backend.domain.errors import ConfigError, Err, Ok, Result

ENV_PREFIX: Final[str] = "DEPCOVER_"
ENV_LLM_PREFIX: Final[str] = f"{ENV_PREFIX}LLM_"

ENV_USE_FAKES: Final[str] = f"{ENV_PREFIX}USE_FAKES"
ENV_JUDGES_DEGRADED: Final[str] = f"{ENV_PREFIX}JUDGES_DEGRADED"
ENV_DAYTONA_SNAPSHOT_ID: Final[str] = f"{ENV_PREFIX}DAYTONA_SNAPSHOT_ID"
ENV_SANDBOX_EXEC_TIMEOUT_S: Final[str] = f"{ENV_PREFIX}SANDBOX_EXEC_TIMEOUT_S"
ENV_SANDBOX_ACQUIRE_TIMEOUT_S: Final[str] = f"{ENV_PREFIX}SANDBOX_ACQUIRE_TIMEOUT_S"
ENV_NEO4J_URI: Final[str] = f"{ENV_PREFIX}NEO4J_URI"
ENV_NEO4J_USER: Final[str] = f"{ENV_PREFIX}NEO4J_USER"
ENV_NEO4J_PASSWORD_ENV: Final[str] = f"{ENV_PREFIX}NEO4J_PASSWORD_ENV"
ENV_BUTTERBASE_BASE_URL: Final[str] = f"{ENV_PREFIX}BUTTERBASE_BASE_URL"
ENV_BUTTERBASE_KEY_ENV: Final[str] = f"{ENV_PREFIX}BUTTERBASE_KEY_ENV"
ENV_GITHUB_TOKEN_ENV: Final[str] = f"{ENV_PREFIX}GITHUB_TOKEN_ENV"
ENV_GITHUB_POLL_INTERVAL_S: Final[str] = f"{ENV_PREFIX}GITHUB_POLL_INTERVAL_S"

ENV_SUFFIX_BASE_URL: Final[str] = "BASE_URL"
ENV_SUFFIX_MODEL: Final[str] = "MODEL"
ENV_SUFFIX_API_KEY_ENV: Final[str] = "API_KEY_ENV"
ENV_SUFFIX_MAX_TOKENS: Final[str] = "MAX_TOKENS"
ENV_SUFFIX_TIMEOUT_S: Final[str] = "TIMEOUT_S"
ENV_SUFFIX_TEMPERATURE: Final[str] = "TEMPERATURE"

DEFAULT_USE_FAKES: Final[bool] = True
DEFAULT_JUDGES_DEGRADED: Final[bool] = False

DEFAULT_LLM_MAX_TOKENS: Final[int] = 4096
DEFAULT_LLM_TIMEOUT_S: Final[float] = 60.0
DEFAULT_LLM_TEMPERATURE: Final[float] = 0.0

DEFAULT_SANDBOX_EXEC_TIMEOUT_S: Final[float] = 300.0
DEFAULT_SANDBOX_ACQUIRE_TIMEOUT_S: Final[float] = 30.0

MIN_GITHUB_POLL_INTERVAL_S: Final[float] = 5.0
DEFAULT_GITHUB_POLL_INTERVAL_S: Final[float] = 5.0

FAKE_LLM_BASE_URL: Final[str] = "http://fake"
FAKE_LLM_MODEL_PREFIX: Final[str] = "fake-"
FAKE_API_KEY_ENV_PREFIX: Final[str] = "FAKE_LLM_API_KEY_"
FAKE_DAYTONA_SNAPSHOT_ID: Final[str] = "fake-snapshot"

_TRUE_TOKENS: Final[frozenset[str]] = frozenset({"1", "true"})
_FALSE_TOKENS: Final[frozenset[str]] = frozenset({"0", "false"})

NonEmptyStr: TypeAlias = Annotated[str, Field(min_length=1)]
GithubPollIntervalS: TypeAlias = Annotated[float, Field(ge=MIN_GITHUB_POLL_INTERVAL_S)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LlmRoleConfig(_FrozenModel):
    base_url: NonEmptyStr
    model: NonEmptyStr
    api_key_env: NonEmptyStr
    temperature: NonNegativeFloat = DEFAULT_LLM_TEMPERATURE
    max_tokens: PositiveInt
    timeout_s: PositiveFloat


class Settings(_FrozenModel):
    use_fakes: bool
    neo4j_uri: str | None
    neo4j_user: str | None
    neo4j_password_env: str | None
    daytona_snapshot_id: NonEmptyStr
    sandbox_exec_timeout_s: PositiveFloat
    sandbox_acquire_timeout_s: PositiveFloat
    butterbase_base_url: str | None
    butterbase_key_env: str | None
    github_token_env: str | None
    github_poll_interval_s: GithubPollIntervalS
    llm_roles: Mapping[LlmRole, LlmRoleConfig]
    judges_degraded: bool

    @model_validator(mode="after")
    def _validate_all_roles_present(self) -> Self:
        present = frozenset(self.llm_roles)
        expected = frozenset(LlmRole)
        if present != expected:
            missing = tuple(sorted(role.value for role in expected - present))
            raise ValueError(f"llm_roles is missing required roles: {missing!r}")
        return self

    def role(self, role: LlmRole) -> LlmRoleConfig:
        config = self.llm_roles.get(role)
        if config is None:
            raise ConfigError("llm role is not configured", {"role": role.value})
        return config


def _role_env_key(role: LlmRole, suffix: str) -> str:
    return f"{ENV_LLM_PREFIX}{role.name}_{suffix}"


def _require_str(env: Mapping[str, str], key: str) -> Result[str, ConfigError]:
    raw = env.get(key)
    if raw is None:
        return Err(ConfigError("required env var is missing", {"env_var": key}))
    stripped = raw.strip()
    if stripped == "":
        return Err(ConfigError("required env var is empty", {"env_var": key}))
    return Ok(stripped)


def _optional_str(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped == "":
        return None
    return stripped


def _parse_bool(
    env: Mapping[str, str], key: str, default: bool
) -> Result[bool, ConfigError]:
    raw = env.get(key)
    if raw is None:
        return Ok(default)
    normalized = raw.strip().lower()
    if normalized in _TRUE_TOKENS:
        return Ok(True)
    if normalized in _FALSE_TOKENS:
        return Ok(False)
    return Err(
        ConfigError("env var is not a valid boolean", {"env_var": key, "value": raw})
    )


def _parse_int(
    env: Mapping[str, str], key: str, default: int
) -> Result[int, ConfigError]:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return Ok(default)
    try:
        return Ok(int(raw.strip()))
    except ValueError:
        return Err(
            ConfigError(
                "env var is not a valid integer", {"env_var": key, "value": raw}
            )
        )


def _parse_float(
    env: Mapping[str, str], key: str, default: float
) -> Result[float, ConfigError]:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return Ok(default)
    try:
        return Ok(float(raw.strip()))
    except ValueError:
        return Err(
            ConfigError("env var is not a valid float", {"env_var": key, "value": raw})
        )


def _construct_role_config(
    *,
    base_url: str,
    model: str,
    api_key_env: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
) -> Result[LlmRoleConfig, ConfigError]:
    try:
        config = LlmRoleConfig(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
    except ValidationError as error:
        return Err(ConfigError("llm role config is invalid", {"error": str(error)}))
    return Ok(config)


def _construct_settings(
    *,
    use_fakes: bool,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password_env: str | None,
    daytona_snapshot_id: str,
    sandbox_exec_timeout_s: float,
    sandbox_acquire_timeout_s: float,
    butterbase_base_url: str | None,
    butterbase_key_env: str | None,
    github_token_env: str | None,
    github_poll_interval_s: float,
    llm_roles: Mapping[LlmRole, LlmRoleConfig],
    judges_degraded: bool,
) -> Result[Settings, ConfigError]:
    try:
        settings = Settings(
            use_fakes=use_fakes,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_password_env=neo4j_password_env,
            daytona_snapshot_id=daytona_snapshot_id,
            sandbox_exec_timeout_s=sandbox_exec_timeout_s,
            sandbox_acquire_timeout_s=sandbox_acquire_timeout_s,
            butterbase_base_url=butterbase_base_url,
            butterbase_key_env=butterbase_key_env,
            github_token_env=github_token_env,
            github_poll_interval_s=github_poll_interval_s,
            llm_roles=llm_roles,
            judges_degraded=judges_degraded,
        )
    except ValidationError as error:
        return Err(ConfigError("settings are invalid", {"error": str(error)}))
    return Ok(settings)


def _fake_role_configs() -> Result[dict[LlmRole, LlmRoleConfig], ConfigError]:
    configs: dict[LlmRole, LlmRoleConfig] = {}
    for role in LlmRole:
        config_result = _construct_role_config(
            base_url=FAKE_LLM_BASE_URL,
            model=f"{FAKE_LLM_MODEL_PREFIX}{role.value}",
            api_key_env=f"{FAKE_API_KEY_ENV_PREFIX}{role.name}",
            temperature=DEFAULT_LLM_TEMPERATURE,
            max_tokens=DEFAULT_LLM_MAX_TOKENS,
            timeout_s=DEFAULT_LLM_TIMEOUT_S,
        )
        if isinstance(config_result, Err):
            return config_result
        configs[role] = config_result.value
    return Ok(configs)


def _live_role_config(
    env: Mapping[str, str], role: LlmRole
) -> Result[LlmRoleConfig, ConfigError]:
    base_url_result = _require_str(env, _role_env_key(role, ENV_SUFFIX_BASE_URL))
    if isinstance(base_url_result, Err):
        return base_url_result
    model_result = _require_str(env, _role_env_key(role, ENV_SUFFIX_MODEL))
    if isinstance(model_result, Err):
        return model_result
    api_key_env_result = _require_str(env, _role_env_key(role, ENV_SUFFIX_API_KEY_ENV))
    if isinstance(api_key_env_result, Err):
        return api_key_env_result
    max_tokens_result = _parse_int(
        env, _role_env_key(role, ENV_SUFFIX_MAX_TOKENS), DEFAULT_LLM_MAX_TOKENS
    )
    if isinstance(max_tokens_result, Err):
        return max_tokens_result
    timeout_result = _parse_float(
        env, _role_env_key(role, ENV_SUFFIX_TIMEOUT_S), DEFAULT_LLM_TIMEOUT_S
    )
    if isinstance(timeout_result, Err):
        return timeout_result
    temperature_result = _parse_float(
        env, _role_env_key(role, ENV_SUFFIX_TEMPERATURE), DEFAULT_LLM_TEMPERATURE
    )
    if isinstance(temperature_result, Err):
        return temperature_result
    return _construct_role_config(
        base_url=base_url_result.value,
        model=model_result.value,
        api_key_env=api_key_env_result.value,
        temperature=temperature_result.value,
        max_tokens=max_tokens_result.value,
        timeout_s=timeout_result.value,
    )


def _live_role_configs(
    env: Mapping[str, str],
) -> Result[dict[LlmRole, LlmRoleConfig], ConfigError]:
    configs: dict[LlmRole, LlmRoleConfig] = {}
    for role in LlmRole:
        config_result = _live_role_config(env, role)
        if isinstance(config_result, Err):
            return config_result
        configs[role] = config_result.value
    return Ok(configs)


def _fake_settings(judges_degraded: bool) -> Result[Settings, ConfigError]:
    roles_result = _fake_role_configs()
    if isinstance(roles_result, Err):
        return roles_result
    return _construct_settings(
        use_fakes=True,
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password_env=None,
        daytona_snapshot_id=FAKE_DAYTONA_SNAPSHOT_ID,
        sandbox_exec_timeout_s=DEFAULT_SANDBOX_EXEC_TIMEOUT_S,
        sandbox_acquire_timeout_s=DEFAULT_SANDBOX_ACQUIRE_TIMEOUT_S,
        butterbase_base_url=None,
        butterbase_key_env=None,
        github_token_env=None,
        github_poll_interval_s=DEFAULT_GITHUB_POLL_INTERVAL_S,
        llm_roles=roles_result.value,
        judges_degraded=judges_degraded,
    )


def _live_settings(
    env: Mapping[str, str], judges_degraded: bool
) -> Result[Settings, ConfigError]:
    snapshot_result = _require_str(env, ENV_DAYTONA_SNAPSHOT_ID)
    if isinstance(snapshot_result, Err):
        return snapshot_result
    exec_timeout_result = _parse_float(
        env, ENV_SANDBOX_EXEC_TIMEOUT_S, DEFAULT_SANDBOX_EXEC_TIMEOUT_S
    )
    if isinstance(exec_timeout_result, Err):
        return exec_timeout_result
    acquire_timeout_result = _parse_float(
        env, ENV_SANDBOX_ACQUIRE_TIMEOUT_S, DEFAULT_SANDBOX_ACQUIRE_TIMEOUT_S
    )
    if isinstance(acquire_timeout_result, Err):
        return acquire_timeout_result
    poll_interval_result = _parse_float(
        env, ENV_GITHUB_POLL_INTERVAL_S, DEFAULT_GITHUB_POLL_INTERVAL_S
    )
    if isinstance(poll_interval_result, Err):
        return poll_interval_result
    roles_result = _live_role_configs(env)
    if isinstance(roles_result, Err):
        return roles_result
    return _construct_settings(
        use_fakes=False,
        neo4j_uri=_optional_str(env, ENV_NEO4J_URI),
        neo4j_user=_optional_str(env, ENV_NEO4J_USER),
        neo4j_password_env=_optional_str(env, ENV_NEO4J_PASSWORD_ENV),
        daytona_snapshot_id=snapshot_result.value,
        sandbox_exec_timeout_s=exec_timeout_result.value,
        sandbox_acquire_timeout_s=acquire_timeout_result.value,
        butterbase_base_url=_optional_str(env, ENV_BUTTERBASE_BASE_URL),
        butterbase_key_env=_optional_str(env, ENV_BUTTERBASE_KEY_ENV),
        github_token_env=_optional_str(env, ENV_GITHUB_TOKEN_ENV),
        github_poll_interval_s=poll_interval_result.value,
        llm_roles=roles_result.value,
        judges_degraded=judges_degraded,
    )


def _load_settings(env: Mapping[str, str]) -> Result[Settings, ConfigError]:
    use_fakes_result = _parse_bool(env, ENV_USE_FAKES, DEFAULT_USE_FAKES)
    if isinstance(use_fakes_result, Err):
        return use_fakes_result
    judges_degraded_result = _parse_bool(
        env, ENV_JUDGES_DEGRADED, DEFAULT_JUDGES_DEGRADED
    )
    if isinstance(judges_degraded_result, Err):
        return judges_degraded_result
    if use_fakes_result.value:
        return _fake_settings(judges_degraded_result.value)
    return _live_settings(env, judges_degraded_result.value)


def load_settings() -> Result[Settings, ConfigError]:
    return _load_settings(os.environ)


__all__ = (
    "DEFAULT_GITHUB_POLL_INTERVAL_S",
    "DEFAULT_LLM_MAX_TOKENS",
    "DEFAULT_LLM_TEMPERATURE",
    "DEFAULT_LLM_TIMEOUT_S",
    "DEFAULT_SANDBOX_ACQUIRE_TIMEOUT_S",
    "DEFAULT_SANDBOX_EXEC_TIMEOUT_S",
    "MIN_GITHUB_POLL_INTERVAL_S",
    "LlmRoleConfig",
    "Settings",
    "load_settings",
)
