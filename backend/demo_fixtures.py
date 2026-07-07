from collections.abc import Mapping

from backend.domain.enums import SandboxOutcome
from backend.domain.models import FileContent
from backend.ports.auth import AuthenticatedUser
from backend.adapters.fake.fake_github import SeededPr
from backend.ports.sandbox import SandboxResult
from backend.services.battery import input_battery

_VICTIM_URL = "https://github.com/depcover/victim-axios"
_DEMO_TOKEN = "demo-token"
_DEMO_USER = AuthenticatedUser(id="user-demo", email="demo@depcover.dev")

_PACKAGE_JSON = FileContent(
    path="package.json",
    text='{\n  "name": "victim-axios",\n  "version": "1.0.0",\n'
    '  "dependencies": { "axios": "^1.6.0" },\n'
    '  "scripts": { "test": "node test.js", "build": "node build.js" }\n}\n',
)
_API_JS = FileContent(
    path="src/api.js",
    text="const axios = require('axios');\n"
    "async function getUser(id) {\n"
    "  const res = await axios.get(`/users/${id}`);\n"
    "  return res.data;\n"
    "}\n"
    "module.exports = { getUser };\n",
)
_CLIENT_JS = FileContent(
    path="src/userClient.js",
    text="const http = require('axios');\n"
    "async function listUsers() {\n"
    "  const res = await http.get('/users');\n"
    "  return res.data;\n"
    "}\n"
    "module.exports = { listUsers };\n",
)

_VICTIM_FILES: tuple[FileContent, ...] = (_PACKAGE_JSON, _API_JS, _CLIENT_JS)

_PATCHED_PATHS: tuple[str, ...] = ("src/api.js", "src/userClient.js")

_TAP_ALL_OK = "TAP version 13\n1..2\nok 1 - getUser\nok 2 - listUsers\n"


def victim_repo_url() -> str:
    return _VICTIM_URL


def victim_repo_files() -> tuple[FileContent, ...]:
    return _VICTIM_FILES


def victim_repos_seed() -> dict[str, tuple[FileContent, ...]]:
    return {_VICTIM_URL: _VICTIM_FILES}


def demo_token() -> str:
    return _DEMO_TOKEN


def demo_user() -> AuthenticatedUser:
    return _DEMO_USER


def auth_tokens_seed() -> dict[str, AuthenticatedUser]:
    return {_DEMO_TOKEN: _DEMO_USER}


def github_seed() -> dict[str, tuple[SeededPr, ...]]:
    return {_VICTIM_URL: ()}


def golden_raw() -> dict[str, str]:
    outputs: dict[str, str] = {}
    for index, case in enumerate(input_battery()):
        outputs[case.id] = '{"case":"' + case.id + '","ok":true,"seq":' + str(index) + "}"
    return outputs


def _passed(stdout: str) -> SandboxResult:
    return SandboxResult(
        outcome=SandboxOutcome.PASSED,
        exit_code=0,
        stdout=stdout,
        stderr="",
        duration_s=0.01,
    )


def scripted_sandbox() -> Mapping[tuple[str, ...], SandboxResult]:
    scripted: dict[tuple[str, ...], SandboxResult] = {
        ("rm", "-rf", "node_modules/axios"): _passed(""),
        ("npm", "test"): _passed(_TAP_ALL_OK),
        ("npm", "run", "build"): _passed("build ok\n"),
    }
    for path in _PATCHED_PATHS:
        scripted[("node", "--check", path)] = _passed("")
    for case_id, raw in golden_raw().items():
        scripted[("node", "harness.js", case_id)] = _passed(raw)
    return scripted
