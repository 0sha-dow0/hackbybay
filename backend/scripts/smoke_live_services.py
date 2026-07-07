from __future__ import annotations

import os
import sys
from typing import Final

import httpx
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from backend.config import Settings, load_settings
from backend.domain.errors import Err

_AUTO_API_SEGMENT: Final[str] = "auto-api"
_TABLE_REPOS: Final[str] = "repos"


def _log(message: str) -> None:
    print(message, flush=True)


def _secret(env_name: str | None, label: str) -> str | None:
    if env_name is None or env_name.strip() == "":
        _log(f"FAIL {label}: missing env pointer")
        return None
    value = os.environ.get(env_name)
    if value is None or value.strip() == "":
        _log(f"FAIL {label}: missing secret env {env_name}")
        return None
    return value


def _check_butterbase(settings: Settings) -> bool:
    if settings.butterbase_base_url is None:
        _log("FAIL butterbase: missing DEPCOVER_BUTTERBASE_BASE_URL")
        return False
    service_key = _secret(settings.butterbase_key_env, "butterbase")
    if service_key is None:
        return False
    url = f"{settings.butterbase_base_url.rstrip('/')}/{_AUTO_API_SEGMENT}/{_TABLE_REPOS}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
    }
    try:
        response = httpx.get(url, headers=headers, params={"limit": "1"}, timeout=10.0)
    except httpx.HTTPError as error:
        _log(f"FAIL butterbase: transport error {type(error).__name__}")
        return False
    if not response.is_success:
        _log(f"FAIL butterbase: HTTP {response.status_code}")
        return False
    try:
        parsed = response.json()
    except ValueError:
        _log("FAIL butterbase: response was not JSON")
        return False
    if not isinstance(parsed, list):
        _log("FAIL butterbase: expected JSON list")
        return False
    _log("OK butterbase")
    return True


def _check_neo4j(settings: Settings) -> bool:
    if settings.neo4j_uri is None or settings.neo4j_user is None:
        _log("FAIL neo4j: missing DEPCOVER_NEO4J_URI or DEPCOVER_NEO4J_USER")
        return False
    password = _secret(settings.neo4j_password_env, "neo4j")
    if password is None:
        return False
    try:
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, password)
        )
        with driver:
            with driver.session(database="neo4j") as session:
                record = session.run("RETURN 1 AS ok").single()
    except Neo4jError as error:
        _log(f"FAIL neo4j: {type(error).__name__}")
        return False
    if record is None or record.get("ok") != 1:
        _log("FAIL neo4j: unexpected query result")
        return False
    _log("OK neo4j")
    return True


def main() -> int:
    loaded = load_settings()
    if isinstance(loaded, Err):
        _log(f"FAIL settings: {loaded.error.message}")
        return 1
    settings = loaded.value
    if settings.use_fakes:
        _log("FAIL settings: DEPCOVER_USE_FAKES must be false for live smoke checks")
        return 1
    ok = _check_butterbase(settings)
    ok = _check_neo4j(settings) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
