import asyncio
from pathlib import Path

from dotenv import load_dotenv

from backend.adapters.live.live_llm import LiveLlmClientFactory
from backend.config import load_settings
from backend.domain.enums import SandboxOutcome
from backend.domain.errors import Err, Ok
from backend.domain.models import (
    BehavioralCaseResult,
    BehavioralDiffResult,
    BuildResult,
    EvidenceBundle,
    FileDiff,
    NormalizedOutput,
    TestResult,
)
from backend.services.judges import JudgePanel
from backend.services.sanitizer import sanitize_evidence

_DIFF = FileDiff(
    path="src/userClient.js",
    unified_diff=(
        "--- a/src/userClient.js\n"
        "+++ b/src/userClient.js\n"
        "@@\n"
        "-const axios = require('axios');\n"
        "+const http = require('./httpClient');\n"
        " async function getUser(id) {\n"
        "-  const res = await axios.get(`/users/${id}`);\n"
        "+  const res = await http.get(`/users/${id}`);\n"
        "   return res.data;\n"
        " }\n"
        "// httpClient: standard fetch wrapper; throws on non-2xx, returns { data: <parsed JSON> }\n"
    ),
    before="const axios = require('axios');\nconst res = await axios.get(`/users/${id}`);\nreturn res.data;\n",
    after="const http = require('./httpClient');\nconst res = await http.get(`/users/${id}`);\nreturn res.data;\n",
)


def _bundle(*, matched: bool, build_ok: bool) -> EvidenceBundle:
    build = BuildResult(
        outcome=SandboxOutcome.PASSED if build_ok else SandboxOutcome.FAILED,
        log="webpack compiled successfully" if build_ok else "SyntaxError: unexpected token",
    )
    test = TestResult(
        outcome=SandboxOutcome.PASSED,
        failing_tests=(),
        log="12 passing (240ms)",
    )
    ok_case = BehavioralCaseResult(
        case_id="get-success",
        golden=NormalizedOutput(case_id="get-success", normalized='{"id":1,"name":"ada"}'),
        candidate=NormalizedOutput(case_id="get-success", normalized='{"id":1,"name":"ada"}'),
        equal=True,
    )
    not_found = BehavioralCaseResult(
        case_id="get-not-found",
        golden=NormalizedOutput(case_id="get-not-found", normalized='{"thrown":true,"status":404}'),
        candidate=NormalizedOutput(
            case_id="get-not-found",
            normalized='{"thrown":false,"status":404}' if not matched else '{"thrown":true,"status":404}',
        ),
        equal=matched,
    )
    return EvidenceBundle(
        transplant_id="transplant-demo-0001",
        diff=(_DIFF,),
        build=build,
        test=test,
        behavioral=BehavioralDiffResult(matched=matched, per_case=(ok_case, not_found)),
    )


async def _run() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    settings_result = load_settings()
    if isinstance(settings_result, Err):
        print("CONFIG ERROR:", settings_result.error)
        return
    settings = settings_result.value
    print(f"use_fakes={settings.use_fakes}  judges_degraded={settings.judges_degraded}")
    for role, cfg in settings.llm_roles.items():
        print(f"  {role.value:20s} -> {cfg.model}  ({cfg.base_url})")
    factory = LiveLlmClientFactory(settings)
    panel = JudgePanel(factory, settings)

    for label, matched, build_ok in (("CLEAN transplant", True, True), ("PLANTED 404 regression", False, True)):
        bundle = _bundle(matched=matched, build_ok=build_ok)
        sanitized = sanitize_evidence(bundle)
        if isinstance(sanitized, Err):
            print("SANITIZE ERROR:", sanitized.error)
            return
        print(f"\n================ {label} (behavioral matched={matched}) ================")
        result = await panel.deliberate(bundle.transplant_id, sanitized.value, None)
        if isinstance(result, Err):
            print("PANEL ERROR:", result.error)
            continue
        consensus = result.value
        for verdict in consensus.verdicts:
            print(f"  [{verdict.judge_name.value:16s}] {verdict.verdict.value.upper():7s} :: {verdict.rationale[:160]}")
        print(
            f"  CONSENSUS -> approved={consensus.approved} contested={consensus.contested} "
            f"approvals={consensus.approvals}/{consensus.panel_size}"
        )


if __name__ == "__main__":
    asyncio.run(_run())
