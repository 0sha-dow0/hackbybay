import { DepCoverApi } from "./api";
import "./styles.css";
import type {
  FireIncidentResponse,
  GraphLayout,
  Incident,
  MitigationOption,
  PipelineEvent,
  PullRequestRef,
  RegisterRepoResponse,
  Transplant
} from "./types";

const DEMO_REPO_URL = "https://github.com/depcover/victim-axios";
const DEFAULT_TOKEN = "demo-token";
const STORAGE_KEYS = {
  apiBase: "depcover.apiBase",
  token: "depcover.token",
  repoUrl: "depcover.repoUrl",
  owner: "depcover.owner"
} as const;

type BusyAction = "health" | "scan" | "incident" | "pipeline" | "review" | null;

interface AppState {
  apiBase: string;
  token: string;
  repoUrl: string;
  owner: string;
  busy: BusyAction;
  health: string;
  error: string | null;
  scan: RegisterRepoResponse | null;
  incident: Incident | null;
  mitigations: MitigationOption[];
  events: PipelineEvent[];
  transplant: Transplant | null;
  selectedDiffPath: string | null;
  pullRequest: PullRequestRef | null;
  stream: EventSource | null;
}

const state: AppState = {
  apiBase: localStorage.getItem(STORAGE_KEYS.apiBase) ?? "",
  token: localStorage.getItem(STORAGE_KEYS.token) ?? DEFAULT_TOKEN,
  repoUrl: localStorage.getItem(STORAGE_KEYS.repoUrl) ?? DEMO_REPO_URL,
  owner: localStorage.getItem(STORAGE_KEYS.owner) ?? "demo",
  busy: null,
  health: "unchecked",
  error: null,
  scan: null,
  incident: null,
  mitigations: [],
  events: [],
  transplant: null,
  selectedDiffPath: null,
  pullRequest: null,
  stream: null
};

const appRoot = document.querySelector<HTMLDivElement>("#app");
if (appRoot === null) {
  throw new Error("missing #app root");
}
const app = appRoot;

function api(): DepCoverApi {
  return new DepCoverApi({ baseUrl: state.apiBase, token: state.token });
}

function render(): void {
  app.innerHTML = `
    <div class="app-shell">
      ${renderTopbar()}
      ${renderCommandBand()}
      ${state.error === null ? "" : `<div class="error-strip">${escapeHtml(state.error)}</div>`}
      <main class="workspace">
        <section class="left-rail">${renderRunbook()}</section>
        <section class="main-stage">
          ${renderGraph()}
          ${renderPipeline()}
          ${renderReview()}
        </section>
        <section class="right-rail">
          ${renderUnderwriting()}
          ${renderMitigations()}
        </section>
      </main>
    </div>
  `;
  bind();
}

function renderTopbar(): string {
  return `
    <header class="topbar">
      <div class="brand-block">
        <div class="brand-mark">${icon("shield")}</div>
        <div>
          <h1 class="brand-name">Casc<span>AI</span>de</h1>
          <p>Dependency Transplant Console</p>
        </div>
      </div>
      <div class="connection-bar">
        <label>
          <span>API</span>
          <input id="apiBase" value="${escapeAttr(state.apiBase)}" placeholder="same origin" />
        </label>
        <label>
          <span>Token</span>
          <input id="token" value="${escapeAttr(state.token)}" />
        </label>
        <button id="checkHealth" class="icon-button" title="Check backend health" ${disabled("health")}>
          ${icon("activity")}
        </button>
        <span class="health-pill ${state.health === "online" ? "online" : ""}">${escapeHtml(state.health)}</span>
      </div>
    </header>
  `;
}

function renderCommandBand(): string {
  const scanReady = state.repoUrl.trim() !== "" && state.owner.trim() !== "";
  const incidentReady = state.scan !== null;
  const transplant = state.mitigations.find((option) => option.kind === "transplant");
  const pipelineReady = state.incident !== null && transplant !== undefined;
  return `
    <section class="command-band">
      <div class="repo-fields">
        <label>
          <span>Repository</span>
          <input id="repoUrl" value="${escapeAttr(state.repoUrl)}" />
        </label>
        <label>
          <span>Owner</span>
          <input id="owner" value="${escapeAttr(state.owner)}" />
        </label>
      </div>
      <div class="command-buttons">
        <button id="scanRepo" class="primary" ${scanReady ? disabled("scan") : "disabled"}>
          ${icon("search")} Scan
        </button>
        <button id="fireIncident" ${incidentReady ? disabled("incident") : "disabled"}>
          ${icon("alert")} Incident
        </button>
        <button id="runTransplant" ${pipelineReady ? disabled("pipeline") : "disabled"}>
          ${icon("play")} Transplant
        </button>
        <button id="resetFlow" class="icon-button" title="Reset console">${icon("refresh")}</button>
      </div>
    </section>
    ${renderStageRail()}
  `;
}

function renderStageRail(): string {
  const stages = [
    ["scan", "Scan"],
    ["incident", "Incident"],
    ["pipeline", "Pipeline"],
    ["review", "Review"]
  ] as const;
  const current = currentStage();
  return `
    <section class="stage-rail">
      ${stages
        .map(([, label], index) => {
          const className = index < current ? "done" : index === current ? "active" : "";
          return `<div class="stage-step ${className}"><span>${index + 1}</span>${label}</div>`;
        })
        .join("")}
    </section>
  `;
}

function renderRunbook(): string {
  const scan = state.scan;
  const incident = state.incident;
  return `
    <div class="section-head">
      <h2>Mission</h2>
      <span>${state.busy === null ? "ready" : state.busy}</span>
    </div>
    <div class="mission-panel">
      <div class="mission-row">
        <span class="mission-icon">${icon("repo")}</span>
        <div>
          <strong>${escapeHtml(scan?.repo.id ?? "repo not scanned")}</strong>
          <p>${escapeHtml(scan?.repo.url ?? state.repoUrl)}</p>
        </div>
      </div>
      <div class="mission-row">
        <span class="mission-icon">${icon("target")}</span>
        <div>
          <strong>${escapeHtml(scan?.surgery_plan.target_package ?? "target pending")}</strong>
          <p>${scan === null ? "0 call sites" : `${scan.surgery_plan.call_sites.length} call sites across ${scan.surgery_plan.affected_files.length} files`}</p>
        </div>
      </div>
      <div class="mission-row">
        <span class="mission-icon">${icon("alert")}</span>
        <div>
          <strong>${escapeHtml(incident?.status ?? "no incident")}</strong>
          <p>${escapeHtml(incident?.id ?? "CVE trigger idle")}</p>
        </div>
      </div>
    </div>
    <div class="callsite-list">
      <div class="section-head compact"><h2>Call Sites</h2><span>${scan?.surgery_plan.call_sites.length ?? 0}</span></div>
      ${
        scan === null
          ? `<div class="empty-state">No call sites loaded.</div>`
          : scan.surgery_plan.call_sites
              .map(
                (site) => `
                  <article class="callsite ${site.is_aliased ? "aliased" : ""}">
                    <div><strong>${escapeHtml(site.file_path)}</strong><span>${site.line}</span></div>
                    <code>${escapeHtml(site.symbol)}</code>
                    ${site.alias === null ? "" : `<small>alias ${escapeHtml(site.alias)}</small>`}
                  </article>
                `
              )
              .join("")
      }
    </div>
  `;
}

function renderGraph(): string {
  const layout = state.scan?.graph_layout ?? null;
  return `
    <section class="graph-band">
      <div class="section-head">
        <h2>Dependency Graph</h2>
        <span>${layout === null ? "no graph" : `${layout.nodes.length} nodes / ${layout.edges.length} edges`}</span>
      </div>
      <div class="graph-viewport">
        ${layout === null ? renderGraphPlaceholder() : renderGraphSvg(layout)}
      </div>
    </section>
  `;
}

function renderUnderwriting(): string {
  const report = state.scan?.underwriting ?? null;
  const centrality = report?.centrality[0];
  return `
    <section class="side-panel">
      <div class="section-head">
        <h2>Underwriting</h2>
        <span>${report === null ? "pending" : "captured"}</span>
      </div>
      <div class="metrics-grid">
        <div class="metric">
          <span>Affected</span>
          <strong>${report?.affected_file_count ?? 0}</strong>
        </div>
        <div class="metric">
          <span>Failures</span>
          <strong>${report?.failing_tests.length ?? 0}</strong>
        </div>
        <div class="metric wide">
          <span>Centrality</span>
          <strong>${centrality === undefined ? "-" : `${centrality.package} ${centrality.score.toFixed(2)}`}</strong>
        </div>
      </div>
      <div class="evidence-list">
        ${
          report === null
            ? `<div class="empty-state">No kill-test evidence yet.</div>`
            : renderEvidence(report.failing_tests, report.warnings.map((warning) => warning.shape))
        }
      </div>
    </section>
  `;
}

function renderMitigations(): string {
  return `
    <section class="side-panel">
      <div class="section-head">
        <h2>Mitigations</h2>
        <span>${state.mitigations.length}</span>
      </div>
      <div class="mitigation-stack">
        ${
          state.mitigations.length === 0
            ? `<div class="empty-state">No incident options loaded.</div>`
            : state.mitigations.map(renderMitigation).join("")
        }
      </div>
    </section>
  `;
}

function renderMitigation(option: MitigationOption): string {
  return `
    <article class="mitigation ${option.executable ? "executable" : ""}">
      <div class="mitigation-top">
        <strong>${escapeHtml(option.title)}</strong>
        <span>${escapeHtml(option.kind)}</span>
      </div>
      <p>${escapeHtml(option.rationale)}</p>
      <div class="mitigation-meta">
        <span>${escapeHtml(option.effort)}</span>
        <span>${escapeHtml(option.blast_radius)}</span>
        <span>${escapeHtml(option.residual_risk)}</span>
      </div>
    </article>
  `;
}

function renderPipeline(): string {
  return `
    <section class="pipeline-band">
      <div class="section-head">
        <h2>Pipeline</h2>
        <span>${state.events.length === 0 ? "idle" : `${state.events.length} events`}</span>
      </div>
      <div class="timeline">
        ${
          state.events.length === 0
            ? `<div class="empty-state">No pipeline events.</div>`
            : state.events.map(renderEvent).join("")
        }
      </div>
    </section>
  `;
}

function renderEvent(event: PipelineEvent): string {
  return `
    <article class="timeline-event ${event.terminal ? "terminal" : ""}">
      <span class="event-seq">${event.seq}</span>
      <div>
        <strong>${stageLabel(event.stage)}</strong>
        <p>${escapeHtml(event.message)}</p>
      </div>
      <time>${formatTime(event.at)}</time>
    </article>
  `;
}

function renderReview(): string {
  const transplant = state.transplant;
  if (transplant === null) {
    return `
      <section class="review-band">
        <div class="section-head"><h2>Review</h2><span>waiting</span></div>
        <div class="empty-state tall">No transplant artifact.</div>
      </section>
    `;
  }

  const selected = selectedDiff(transplant);
  return `
    <section class="review-band">
      <div class="section-head">
        <h2>Review</h2>
        <span>${transplant.consensus.approvals}/${transplant.consensus.panel_size} approvals</span>
      </div>
      <div class="review-grid">
        <div class="verdicts">
          <div class="consensus ${transplant.consensus.approved ? "approved" : "contested"}">
            ${transplant.consensus.approved ? icon("check") : icon("x")}
            <strong>${transplant.consensus.approved ? "Approved" : "Contested"}</strong>
          </div>
          ${transplant.consensus.verdicts
            .map(
              (verdict) => `
                <article class="verdict ${verdict.verdict}">
                  <div><strong>${escapeHtml(verdict.judge_name)}</strong><span>${escapeHtml(verdict.verdict)}</span></div>
                  <p>${escapeHtml(verdict.rationale)}</p>
                </article>
              `
            )
            .join("")}
          <div class="review-actions">
            <button id="acceptReview" class="primary" ${disabled("review")}>${icon("check")} Accept</button>
            <button id="rejectReview" ${disabled("review")}>${icon("x")} Reject</button>
          </div>
          ${state.pullRequest === null ? "" : renderPullRequest(state.pullRequest)}
        </div>
        <div class="diff-shell">
          <div class="diff-tabs">
            ${transplant.diff
              .map(
                (file) => `
                  <button class="diff-tab ${file.path === selected.path ? "active" : ""}" data-diff-path="${escapeAttr(file.path)}">
                    ${escapeHtml(file.path)}
                  </button>
                `
              )
              .join("")}
          </div>
          <pre class="diff-view">${renderDiff(selected.unified_diff)}</pre>
        </div>
      </div>
    </section>
  `;
}

function renderPullRequest(pr: PullRequestRef): string {
  return `
    <a class="pr-link" href="${escapeAttr(pr.url)}" target="_blank" rel="noreferrer">
      ${icon("branch")} Pull request #${pr.number}
    </a>
  `;
}

function bind(): void {
  bindInput("#apiBase", "apiBase", STORAGE_KEYS.apiBase);
  bindInput("#token", "token", STORAGE_KEYS.token);
  bindInput("#repoUrl", "repoUrl", STORAGE_KEYS.repoUrl);
  bindInput("#owner", "owner", STORAGE_KEYS.owner);
  byId("checkHealth")?.addEventListener("click", () => void checkHealth());
  byId("scanRepo")?.addEventListener("click", () => void scanRepo());
  byId("fireIncident")?.addEventListener("click", () => void fireIncident());
  byId("runTransplant")?.addEventListener("click", () => void runTransplant());
  byId("resetFlow")?.addEventListener("click", resetFlow);
  byId("acceptReview")?.addEventListener("click", () => void submitReview(true));
  byId("rejectReview")?.addEventListener("click", () => void submitReview(false));
  document.querySelectorAll<HTMLButtonElement>("[data-diff-path]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedDiffPath = button.dataset.diffPath ?? null;
      render();
    });
  });
}

function bindInput<K extends "apiBase" | "token" | "repoUrl" | "owner">(
  selector: string,
  key: K,
  storageKey: string
): void {
  const input = document.querySelector<HTMLInputElement>(selector);
  input?.addEventListener("input", () => {
    state[key] = input.value;
    localStorage.setItem(storageKey, input.value);
  });
}

async function checkHealth(): Promise<void> {
  await runBusy("health", async () => {
    const result = await api().health();
    state.health = result.ok === true || result.status === "ok" ? "online" : "degraded";
  });
}

async function scanRepo(): Promise<void> {
  await runBusy("scan", async () => {
    state.scan = await api().registerRepo(state.repoUrl, state.owner);
    state.incident = null;
    state.mitigations = [];
    state.events = [];
    state.transplant = null;
    state.selectedDiffPath = null;
    state.pullRequest = null;
    state.health = "online";
  });
}

async function fireIncident(): Promise<void> {
  if (state.scan === null) {
    return;
  }
  await runBusy("incident", async () => {
    const response: FireIncidentResponse = await api().fireIncident(state.scan!.repo.id);
    state.incident = response.incident;
    state.mitigations = response.options.options;
    state.events = [];
    state.transplant = null;
    state.pullRequest = null;
  });
}

async function runTransplant(): Promise<void> {
  if (state.incident === null) {
    return;
  }
  await runBusy("pipeline", async () => {
    closeStream();
    state.events = [];
    state.transplant = null;
    state.pullRequest = null;
    await api().chooseStrategy(state.incident!.id, "transplant");
    state.stream = api().streamIncident(
      state.incident!.id,
      (event) => {
        state.events = [...state.events.filter((existing) => existing.seq !== event.seq), event].sort(
          (left, right) => left.seq - right.seq
        );
        if (event.terminal) {
          closeStream();
          void loadTransplant();
        }
        render();
      },
      (message) => {
        state.error = message;
        render();
      }
    );
  });
}

async function loadTransplant(): Promise<void> {
  if (state.incident === null) {
    return;
  }
  await runBusy("pipeline", async () => {
    const transplantId = `transplant-${state.incident!.id}`;
    state.transplant = await api().getTransplant(transplantId);
    state.selectedDiffPath = state.transplant.diff[0]?.path ?? null;
  });
}

async function submitReview(accept: boolean): Promise<void> {
  if (state.transplant === null) {
    return;
  }
  await runBusy("review", async () => {
    const response = await api().submitReview(state.transplant!, accept);
    state.pullRequest = response.pull_request;
    if (state.incident !== null) {
      state.incident = { ...state.incident, status: response.status };
    }
  });
}

async function runBusy(action: BusyAction, task: () => Promise<void>): Promise<void> {
  state.busy = action;
  state.error = null;
  render();
  try {
    await task();
  } catch (error) {
    state.error = error instanceof Error ? error.message : "unknown error";
  } finally {
    state.busy = null;
    render();
  }
}

function resetFlow(): void {
  closeStream();
  state.error = null;
  state.scan = null;
  state.incident = null;
  state.mitigations = [];
  state.events = [];
  state.transplant = null;
  state.selectedDiffPath = null;
  state.pullRequest = null;
  render();
}

function closeStream(): void {
  state.stream?.close();
  state.stream = null;
}

function currentStage(): number {
  if (state.transplant !== null) {
    return 3;
  }
  if (state.events.length > 0 || state.busy === "pipeline") {
    return 2;
  }
  if (state.incident !== null) {
    return 1;
  }
  return 0;
}

function selectedDiff(transplant: Transplant) {
  return (
    transplant.diff.find((file) => file.path === state.selectedDiffPath) ??
    transplant.diff[0] ?? {
      path: "empty",
      unified_diff: "",
      before: "",
      after: ""
    }
  );
}

function renderGraphPlaceholder(): string {
  return `
    <svg class="graph-svg" viewBox="0 0 900 380" role="img" aria-label="Graph placeholder">
      <defs>${graphMarker()}</defs>
      <path class="preview-edge" d="M178 190 C290 70, 420 70, 530 170" />
      <path class="preview-edge muted" d="M178 190 C304 296, 452 294, 646 218" />
      <g class="preview-node package" transform="translate(120 150)"><circle r="42" /><text>axios</text></g>
      <g class="preview-node file" transform="translate(520 150)"><rect x="-54" y="-30" width="108" height="60" rx="8" /><text>api.js</text></g>
      <g class="preview-node call" transform="translate(690 218)"><circle r="30" /><text>call</text></g>
    </svg>
  `;
}

function renderGraphSvg(layout: GraphLayout): string {
  if (layout.nodes.length === 0) {
    return `<div class="empty-state">Graph is empty.</div>`;
  }
  const bounds = graphBounds(layout);
  const nodeById = new Map(layout.nodes.map((node) => [node.id, node]));
  const edges = layout.edges
    .map((edge) => {
      const src = nodeById.get(edge.src);
      const dst = nodeById.get(edge.dst);
      if (src === undefined || dst === undefined) {
        return "";
      }
      return `<line class="graph-edge ${edge.kind}" x1="${gx(src.x, bounds)}" y1="${gy(src.y, bounds)}" x2="${gx(dst.x, bounds)}" y2="${gy(dst.y, bounds)}" />`;
    })
    .join("");
  const nodes = layout.nodes
    .map((node) => {
      const x = gx(node.x, bounds);
      const y = gy(node.y, bounds);
      const label = escapeHtml(node.label);
      if (node.kind === "file") {
        return `<g class="graph-node file" transform="translate(${x} ${y})"><rect x="-58" y="-28" width="116" height="56" rx="8" /><text>${label}</text></g>`;
      }
      if (node.kind === "call_site") {
        return `<g class="graph-node call" transform="translate(${x} ${y})"><circle r="26" /><text>${label}</text></g>`;
      }
      return `<g class="graph-node package" transform="translate(${x} ${y})"><circle r="36" /><text>${label}</text></g>`;
    })
    .join("");
  return `
    <svg class="graph-svg" viewBox="0 0 900 380" role="img" aria-label="Dependency graph">
      <defs>${graphMarker()}</defs>
      ${edges}
      ${nodes}
    </svg>
  `;
}

function graphBounds(layout: GraphLayout): { minX: number; maxX: number; minY: number; maxY: number } {
  const xs = layout.nodes.map((node) => node.x);
  const ys = layout.nodes.map((node) => node.y);
  return {
    minX: Math.min(...xs),
    maxX: Math.max(...xs),
    minY: Math.min(...ys),
    maxY: Math.max(...ys)
  };
}

function gx(x: number, bounds: { minX: number; maxX: number }): number {
  const span = Math.max(bounds.maxX - bounds.minX, 1);
  return 96 + ((x - bounds.minX) / span) * 708;
}

function gy(y: number, bounds: { minY: number; maxY: number }): number {
  const span = Math.max(bounds.maxY - bounds.minY, 1);
  return 72 + ((y - bounds.minY) / span) * 236;
}

function graphMarker(): string {
  return `
    <marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
      <path d="M0,0 L9,4.5 L0,9 Z" fill="currentColor" />
    </marker>
  `;
}

function renderEvidence(failures: string[], warnings: string[]): string {
  const failureItems =
    failures.length === 0
      ? `<li><span class="ok-dot"></span> test suite passed in sandbox</li>`
      : failures.map((failure) => `<li><span class="bad-dot"></span>${escapeHtml(failure)}</li>`).join("");
  const warningItems = warnings.map((warning) => `<li><span class="warn-dot"></span>${escapeHtml(warning)}</li>`).join("");
  return `<ul>${failureItems}${warningItems}</ul>`;
}

function renderDiff(diff: string): string {
  if (diff.trim() === "") {
    return "No diff available.";
  }
  return diff
    .split("\n")
    .map((line) => {
      const escaped = escapeHtml(line);
      if (line.startsWith("+")) {
        return `<span class="diff-add">${escaped}</span>`;
      }
      if (line.startsWith("-")) {
        return `<span class="diff-del">${escaped}</span>`;
      }
      if (line.startsWith("@@")) {
        return `<span class="diff-hunk">${escaped}</span>`;
      }
      return escaped;
    })
    .join("\n");
}

function disabled(action: BusyAction): string {
  return state.busy === null ? "" : `disabled aria-busy="${state.busy === action ? "true" : "false"}"`;
}

function byId(id: string): HTMLElement | null {
  return document.getElementById(id);
}

function stageLabel(stage: string): string {
  return stage.replace(/_/g, " ");
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value: string): string {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

function icon(name: string): string {
  const paths: Record<string, string> = {
    activity: '<path d="M4 12h4l2-6 4 12 2-6h4" />',
    alert: '<path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4" /><path d="M12 17h.01" />',
    branch: '<path d="M7 4v8a4 4 0 0 0 4 4h6" /><circle cx="7" cy="4" r="2" /><circle cx="17" cy="16" r="2" />',
    check: '<path d="m4 12 5 5L20 6" />',
    play: '<path d="M8 5v14l11-7-11-7Z" />',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 16v-5h-5" />',
    repo: '<path d="M5 4h10l4 4v12H5z" /><path d="M15 4v5h5" />',
    search: '<circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" />',
    shield: '<path d="M12 3 5 6v5c0 4.5 2.9 8.5 7 10 4.1-1.5 7-5.5 7-10V6l-7-3Z" />',
    target: '<circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" />',
    x: '<path d="m6 6 12 12" /><path d="M18 6 6 18" />'
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] ?? ""}</svg>`;
}

render();
