# PRD: Dependency Transplant Engine
**Working name:** DepCover (rename before repo creation if desired)
**Event:** HackwithBay 3.0, July 7 2026, AWS Builder Loft SF
**Author:** Shadab | **Status:** Locked for build
**One-liner:** An impact-analysis agent for compromised dependencies. It maps every place a bad library touches your code, proves the blast radius by breaking things in a sandbox, recommends ranked mitigation strategies, and autonomously executes the hardest one: full library replacement with behavioral proof and a judged diff you accept or reject. Dependabot patches. We perform transplants.

---

## 1. Problem

When a dependency is vulnerable, abandoned, or untrusted, upgrading is a solved problem (Dependabot, Renovate). Full library replacement is not. Nobody automates it because nobody can prove an AI rewrite is safe. Teams either live with the risk or spend engineer-weeks on manual migration.

## 2. Solution

An autonomous pipeline: graph analysis finds every call site touching the target library, a sandbox kill-test demonstrates the blast radius, Claude rewrites the call sites to a replacement library, a behavioral diff proves the rewrite preserves observable behavior, a 4-judge consensus panel reviews the evidence, and the user gets a Claude Code-style diff to accept or reject. Accepted diffs become a PR on their repo.

**Demo scope:** exactly one transplant pair, axios -> fetch, on a pre-built victim repo.

## 3. Users

- **Demo persona:** engineer responsible for a Node service who just learned axios (hypothetically) has an unfixable CVE.
- **Real target (post-hackathon story):** platform/security teams at companies with large JS estates.

## 4. Core user flow

1. Sign in (Butterbase auth), register repo URL
2. **Scan:** system clones repo, builds dependency + call-site graph, shows graph view
3. **Underwrite:** kill-test in sandbox demonstrates blast radius; exposure report displayed
4. **Incident:** CVE event fires against axios (mock trigger for demo)
5. **Mitigation options:** agent presents 3-4 ranked strategies derived from underwriting evidence (upgrade / shim / transplant / accept risk), each with effort, blast radius, residual risk; user chooses
6. **Transplant:** on choosing transplant, pipeline runs live with visible stages (recall -> rewrite -> verify -> judge)
7. **Review:** user sees a per-file diff (Claude Code style) with the evidence bundle and judge verdicts alongside; Accept or Reject per file or whole transplant
8. **Deliver:** on Accept, PR is opened on the repo with evidence bundle linked

## 5. Functional requirements by component

### 5.1 Ingestion + Graph (Neo4j, ephemeral)
- Parse package.json + lockfile; AST/regex scan for axios imports and call sites, including aliased imports (`const http = require('axios')`)
- Rebuild graph per scan: `MATCH (n) DETACH DELETE n` then load. Nodes: Package, File, CallSite. Edges: DEPENDS_ON, IMPORTS, CALLS
- Queries: (a) centrality to rank dependency criticality, (b) traversal producing the full call-site list = the surgery plan
- Export analysis results as JSON to Butterbase; store graph layout JSON for UI rendering. Nothing reads Neo4j after analysis
- Unknown lockfile shapes are skipped with warnings, never crash ingest

### 5.2 Kill-test underwriting (Daytona)
- From a pre-built snapshot (victim repo + deps installed): remove axios, run test suite, capture failure list
- Failure list + affected-file count = demonstrated blast radius, saved to Butterbase as underwriting_report
- All exec calls wrapped in timeouts

### 5.3 Incident trigger (RocketRide pipeline)
- Mock CVE event (button or scheduled trigger) creates an incident row and starts the transplant pipeline
- Pipeline stages emit status updates the UI can stream

### 5.4 Mitigation options (decision support)
- On incident, one LLM call takes the underwriting evidence JSON (blast radius, call-site count, coupling depth, CVE details) and produces 3-4 ranked strategy cards:
  - **Upgrade**: shown when a patched version exists; recommended but not executed ("that is Dependabot's job", stated on the card)
  - **Shim/wrap**: quarantine the vulnerable surface behind a wrapper; medium effort, contains the CVE, library remains a liability
  - **Transplant**: full replacement, highest effort, permanent cure; the only strategy we execute end-to-end
  - **Accept risk**: quantified from kill-test evidence ("vulnerable path touches N endpoints, blast radius M tests")
- Each card: effort estimate, blast radius, residual risk, all derived from evidence already generated
- User choice recorded in Butterbase (incidents.chosen_strategy); only "transplant" triggers the pipeline
- Positioning: this reframes the product as an impact-analysis and decision-support engine that can also execute the hardest option with proof. Two human gates: choose the strategy, then accept the diff
- Judging honesty: "we built the executor for the hardest strategy; the other executors are roadmap"

### 5.5 Memory (Cognee) — CUT-FIRST FEATURE
- Store verified transplant recipes (library pair, wrapper pattern, known semantic gaps, confirmed fix) after successful runs
- On new incident, query for closest recipe; seed the rewrite prompt with it
- Fallback if integration exceeds 90 min: recipes table in Butterbase, mention Cognee as roadmap

### 5.6 Transplant agent (Claude)
- Input: surgery plan (call-site list from graph) + file contents + recipe (if any)
- Prompt constraints: change only listed call sites; preserve all other lines byte-identical; use the standard fetch wrapper (throws on non-2xx, parses JSON) to close known axios/fetch gaps
- Output validation before verification: strip markdown fences, `node --check` each file, grep for surviving `axios` references (any survivor = incomplete, auto-reject and retry once)

### 5.7 Verification (Daytona)
- Level 1: install + build check on patched tree
- Level 2: full test suite
- Level 4: behavioral diff — sandbox A (original) and sandbox B (patched) receive an identical 10-input battery including 404, 500, and malformed-JSON cases; outputs normalized (strip timestamps/IDs, sort keys) then diffed
- Golden outputs for sandbox A are pre-recorded from the victim repo
- Every verification artifact (logs, diff) saved to Butterbase as the evidence bundle

### 5.8 Judge panel (4 judges)
- Four parallel model calls, each with a distinct rubric, each receiving the full evidence bundle (diff, build log, test results, behavioral diff):
  - Judge A, Correctness: semantic drift beyond what tests cover
  - Judge B, Security: does the rewrite introduce or mask vulnerabilities
  - Judge C, Minimality: surgical change vs. unnecessary rewrite
  - Judge D, Recipe fidelity: does output match the recalled recipe / stated plan
- Judges interpret artifacts; they cannot approve against contradicting evidence
- Consensus rule: 3 of 4 approve -> transplant advances to user review; otherwise marked contested (still shown to user, labeled)
- Verdicts + one-paragraph rationale per judge stored and displayed
- Degradation path: 4 judges -> 2 (Security + Minimality) if behind schedule

### 5.9 Human-in-the-loop diff review (the final gate)
- Claude Code-style UI: per-file unified diff, syntax highlighted, Accept / Reject per file and Accept All / Reject All
- Judge verdicts and behavioral-diff summary rendered alongside the diff, not on a separate page
- Reject on any file blocks the PR; state recorded as rejected with reason field
- Accept All -> PR opened on the repo (GitHub API), evidence bundle linked in PR body
- Nothing ships without explicit human acceptance. This is a product principle, state it at judging

### 5.10 PR Gate (prevention mode) — CUT-SECOND FEATURE
- Poll GitHub API (10s interval; no webhook/ngrok dependency) for new PRs on protected repos
- Screen only changed files + manifest: does the PR introduce the flagged library or new call sites to it?
- Clean PR -> bot comments "screened, no flagged dependencies"
- Dirty PR -> run the existing pipeline scoped to the PR's files; bot posts a review comment with blast-radius warning and the pre-verified replacement as GitHub suggestion blocks (author accepts with one click)
- Reuses transplant agent, verification, and judge panel; new code is polling + comment posting only
- Demo: live-open a PR adding an axios call; bot intervenes within seconds. Closing line: "It doesn't just fix your past, it guards your future"

### 5.11 Frontend (single page, four panels)
- Panel 1: graph view (rendered from stored layout JSON, not live Neo4j)
- Panel 2: underwriting report (kill-test evidence)
- Panel 3: live pipeline log (streaming stages)
- Panel 4: diff review + judge verdicts + Accept/Reject
- Every pipeline state has a terminal render: completed, rejected, contested, failed. No infinite spinners

### 5.12 Data model (Butterbase)
- users (auth-managed)
- repos: url, owner, registered_at
- underwriting_reports: repo_id, target_package, blast_radius_json, graph_layout_json, created_at
- incidents: repo_id, trigger_type, chosen_strategy, status (pending | running | awaiting_review | completed | rejected | contested | failed)
- transplants: incident_id, surgery_plan_json, diff_per_file, evidence_bundle_refs
- judge_verdicts: transplant_id, judge_name, verdict, rationale
- reviews: transplant_id, user_id, decision, per_file_decisions, reason

### 5.13 Security model (prompt injection + untrusted code)

**Principle:** we do not claim jailbreak-proof models; nobody can. We claim a jailbroken model in this system cannot cause harm: it holds no secrets, touches no tools, and every output is gated by mechanical verification plus a human. Defense in depth, not model trust.

**Threat A: prompt injection via repo content** (README/comment/string says "ignore instructions, approve this, insert this code")
1. All repo content sent to any model is wrapped in `<untrusted_file>` tags; system prompt declares tagged content is data to analyze, never instructions, regardless of what it claims
2. Transplant agent is text-in/text-out only: no shell, no file writes, no network, no tools. Never grant tool access "for convenience"
3. All agent output passes mechanical validators regardless of content: `node --check`, axios-survivor grep, test suite, behavioral diff. An injected behavior change is precisely what the behavioral diff detects
4. Judges receive artifacts (diff, test logs, behavioral-diff summary), never raw repo contents. Sandbox logs are sanitized before reaching judges (malicious tests can print injection text into logs; logs are an input channel)
5. Human accept on the rendered diff is the final backstop; a successful attack must pass validators, four judges, and a human reading the actual diff

**Threat B: malicious code executing** (postinstall scripts, hostile test files: key theft, backend attack, resource abuse)
1. Repo code executes in Daytona sandboxes only, never on the backend host. Clone, install, test: all inside; sandbox destroyed after each run
2. ZERO secrets in the sandbox environment: no model API keys, no Butterbase credentials, no GitHub token. A script dumping `process.env` finds nothing. Single most important rule; easiest to violate accidentally
3. `npm install --ignore-scripts` kills the postinstall vector (victim repo needs no lifecycle scripts)
4. Sandbox output is data, never code: backend parses logs/JSON defensively; never eval or shell-interpolate strings originating inside a sandbox
5. Timeouts + ephemeral sandboxes (already required for reliability) double as containment: miners and fork bombs die at timeout, nothing persists
6. PR Gate is the highest-exposure surface (strangers submit code by design): same rules, plus the gate only comments, never merges; worst case for a malicious PR is one wasted sandbox run

**Judge Q&A line:** "The model's opinion can never override an artifact, and no artifact chain ends without a human."

## 6. Non-goals (say these out loud at judging)

- No version pinning / upgrading (Dependabot's job; Tier 1 explicitly out of scope)
- No payment flow (Butterbase used for auth + DB only; one stubbed payment seam behind a flag exists if organizers confirm payment is mandatory)
- One language (JS), one transplant pair (axios -> fetch); other languages are per-language plugins on the same pipeline
- No persistent graph; Neo4j is compute, Butterbase is the system of record
- No auto-merge ever; human acceptance is required by design

## 7. Demo script (90 seconds)

1. Paste victim repo -> graph renders, aliased import visibly caught by traversal (10-sec flex: "grep misses this, the graph doesn't")
2. Kill-test report: "axios removal breaks N tests across 4 files"
3. Fire CVE trigger -> mitigation options card appears: upgrade / shim / transplant / accept risk, each priced by evidence -> choose transplant -> pipeline streams live
4. Behavioral diff CATCHES a planted regression (fetch not throwing on 404) -> judges reject -> agent regenerates with the wrapper -> diff goes clean
5. Judge panel: 4 verdicts appear with rationales
6. Accept All in the diff view -> PR opens on the repo, evidence bundle linked
7. (If PR Gate built) Live-open a PR adding a new axios call -> bot comments with warning + one-click fetch suggestion within seconds
Close: "Every claim is backed by an artifact, not a model's opinion. And it doesn't just fix your past, it guards your future."

## 8. Build risks and cut order

| Risk | Mitigation |
|---|---|
| Transplant agent flaky | Highest-risk block; victim repo and wrapper pre-validated tonight; retry-once + auto-reject guards |
| Nondeterministic diffs | Output normalization; deterministic input battery designed tonight |
| Daytona limits/latency | Snapshot with deps baked in; pre-warmed + one spare paused sandbox; timeouts everywhere |
| Aura hiccup mid-demo | Graph layout persisted to Butterbase; live Neo4j never on the demo critical path |
| Time | Cut order: Cognee -> PR Gate -> judges 4->2 -> live kill-test becomes pre-recorded. Transplant + behavioral diff + HITL review are never cut |

## 9. Success criteria

- End-to-end run (scan -> transplant -> proof -> judges -> accept -> PR) completes live, three consecutive dry runs before presenting
- All mandatory stacks demonstrably load-bearing: Neo4j traversal produces the surgery plan; Butterbase holds auth + all state; RocketRide runs the pipeline; Daytona is the proof engine
- Judge Q&A answers pre-loaded: language generality, interceptor gaps, bad test coverage, judge fallibility, 500k-line repos

## 10. Pre-hackathon checklist (tonight)

- [ ] Victim repo: Node/Express, axios in 4 files incl. one aliased import and one shared client module, deliberate 404-handling call site, decent test suite
- [ ] Golden outputs recorded: 10-input battery vs. original repo, normalized
- [ ] Fetch wrapper written and hand-validated once
- [ ] Name picked; GitHub repo created (victim as a SEPARATE repo so the PR lands on a real repo)
- [ ] At venue: all API keys; confirm payment optionality; confirm Daytona concurrency + network access

## 11. Tech stack

- **Backend:** Python / FastAPI. neo4j driver, Daytona Python SDK, one OpenAI-compatible client with per-role base_url/model config for all LLM calls, Butterbase client/REST, SSE endpoint for pipeline streaming. Flat structure: main.py + routers.
- **Model allocation (all free tiers unless venue provides credits):** Transplant agent: strongest available coder (Claude if venue credits; else Qwen3 Coder :free on OpenRouter if live day-of; fallback GPT-OSS 120B on Groq). Judges, 4 different models across 2 providers for genuine diversity + separate rate buckets: GPT-OSS 120B (Groq), Llama 3.3 70B (Groq), Nemotron 3 Ultra (OpenRouter), GLM/DeepSeek free (OpenRouter). PR Gate screening + mitigation options card: Llama 3.1 8B (Groq). Groq free: 30 RPM / ~1K RPD / 8-12K TPM per model, per org. OpenRouter free: ~20 RPM / 200 RPD, catalog volatile, verify morning-of. TPM discipline: transplant prompt contains only affected files, never the repo.
- **Frontend:** TypeScript / React (Vite). Graph panel rendered from stored layout JSON (precomputed positions, no live physics). Diff panel: react-diff-viewer-continued + Accept/Reject controls. Pipeline log via EventSource. Tailwind, dark terminal aesthetic.
- **Boundary rule:** frontend is dumb. All decisions, artifacts, and judge calls live in Python. UI writes only: register-repo, fire-incident, accept/reject.
- **Repo layout:** /backend, /frontend, /scripts (golden-output recorder, snapshot builder). Victim app is a separate GitHub repo.

## 12. Edge cases

### 12.1 Handle in code (will occur during the demo)

**Transplant agent**
| # | Edge case | Guard |
|---|---|---|
| 1 | Claude returns non-code: markdown fences, commentary, truncated file | Strip fences; validate with `node --check` in sandbox; retry once, then flag failed |
| 2 | Rewrite misses a call site (graph found 6, Claude fixed 5) | Post-rewrite grep for surviving `axios` references; any survivor = auto-reject before verification |
| 3 | Claude over-edits: reformatting, renames, unrequested error handling | Prompt constraint: only listed call sites, all other lines byte-identical; Minimality judge as backstop |

**Behavioral diff**
| # | Edge case | Guard |
|---|---|---|
| 4 | Nondeterministic outputs poison the diff (timestamps, request IDs, ordering) | Normalize before diffing: strip timestamps/IDs, sort keys; input battery designed deterministic |
| 5 | axios/fetch semantic gaps: 404 (axios throws, fetch does not), auto-JSON parsing, error object shape, timeout behavior | Input battery deliberately includes 404, 500, malformed JSON; standard fetch wrapper closes gaps; the planted 404 catch IS the demo wow moment |

**Daytona**
| # | Edge case | Guard |
|---|---|---|
| 6 | Hung process (test suite or app boot never returns) | Timeout wrapper on every exec, no exceptions |
| 7 | Sandbox creation fails or is slow mid-demo | Pre-warm before presenting; keep one paused spare; resume instead of create |
| 8 | npm install flakiness live | Never install live; snapshot with deps baked in |

**Ingestion**
| # | Edge case | Guard |
|---|---|---|
| 9 | Lockfile weirdness: duplicate versions, workspaces, git-URL deps | Parser wraps unknown shapes as skip-with-warning, never crashes ingest |
| 10 | Aliased/dynamic imports: `const http = require('axios')`, wrapped client modules | Graph traversal catches what grep misses; victim repo includes one aliased usage deliberately as a demo flex |

**Pipeline state**
| # | Edge case | Guard |
|---|---|---|
| 11 | Mid-pipeline failure leaves zombie state (judges die after transplant succeeded) | Every incident reaches a terminal state: completed / rejected / contested / failed; UI renders failed gracefully; no infinite spinners |

**HITL review**
| # | Edge case | Guard |
|---|---|---|
| 12 | User rejects one file of a multi-file transplant | Reject on any file blocks the PR; partial accepts are not shipped (a half-transplant is worse than none); state = rejected with per-file reasons |
| 13 | User walks away with review pending | `awaiting_review` is a stable state, nothing times out or auto-ships |

**PR Gate**
| # | Edge case | Guard |
|---|---|---|
| 14 | PR modifies files but touches no flagged dependency | Bot posts "screened, clean" comment (proves the gate ran) |
| 15 | Polling hits GitHub rate limits or duplicate events | Track last-seen PR number + commented flag in Butterbase; idempotent commenting |
| 16 | PR adds the flagged dep in a way the scanner cannot parse | Fail open with a warning comment ("could not fully analyze, manual review advised"), never block silently |

**Security (full spec in 5.13)**
| # | Edge case | Guard |
|---|---|---|
| 17 | Repo content contains prompt-injection text targeting the agent or judges | Untrusted-file tagging; tool-less agent; mechanical validators gate all output; judges see artifacts not raw repo; human accept is final |
| 18 | Repo contains malicious executable code (postinstall, hostile tests) | Execution only in ephemeral Daytona sandboxes; zero secrets in sandbox env; --ignore-scripts; sandbox output parsed as data, never evaluated; timeouts |

### 12.2 Handle with an answer (judge Q&A, do not build)

| Question | Answer |
|---|---|
| "What about TypeScript / Python / my language?" | Pipeline is language-agnostic by design; call-site scanner and test runner are per-language plugins. We built the JS plugin today. |
| "Axios interceptors, retries, cancel tokens - fetch has none of that." | Correct, and that is why the behavioral diff exists: semantic gaps between libraries are exactly what we detect. Complex features map to wrapper shims; unmappable ones fail the diff and route to human review. |
| "What if test coverage is bad?" | The behavioral diff is independent of the test suite - that is the point. Tests are one evidence source; recorded behavior is the stronger one. |
| "What if all 4 judges are wrong together?" | Judges only interpret hard artifacts (diffs, test logs); they cannot approve against contradicting evidence. Split verdicts are labeled contested, and a human accepts every diff before anything ships. In production we would calibrate judge agreement with Cohen's kappa. |
| "Does this scale to a 500k-line repo?" | Graph triage is exactly how it scales: we only analyze and rewrite the paths that touch the target dependency, never the whole codebase. |
| "Why not just upgrade axios?" | Upgrading is a solved problem, that is Dependabot. When a patched version exists, we recommend it on the options card and step aside. We exist for the decision and the hard case: quantified impact analysis across all mitigation strategies, and autonomous execution of the one nobody else automates: full library replacement with behavioral proof. |
| "Why both Neo4j and Cognee?" | Two different graphs: Neo4j is structure (what depends on what, rebuilt per scan), Cognee is experience (what fixes worked, grows over time). Compute vs. memory. |
| "Where is the payment?" | Butterbase is load-bearing for auth and the entire system of record. (If payment confirmed mandatory: flip the Pro-unlock flag built behind the transplant step.) |
| "What if someone jailbreaks the agent, or the repo is malicious?" | We do not trust the model; we contain it. The agent has no tools and no secrets, every output passes mechanical validators, untrusted code runs only in secret-free ephemeral sandboxes, and nothing ships without a human accepting the diff. A jailbroken model in this system can produce bad text, and bad text cannot survive the verification chain. |

