export type StrategyKind = "upgrade" | "shim" | "transplant" | "accept_risk";
export type IncidentStatus =
  | "pending"
  | "running"
  | "awaiting_review"
  | "completed"
  | "rejected"
  | "contested"
  | "failed";
export type PipelineStage =
  | "recall"
  | "rewrite"
  | "validate"
  | "verify_build"
  | "verify_test"
  | "verify_behavioral"
  | "judge"
  | "awaiting_review"
  | "completed"
  | "contested"
  | "failed";
export type Verdict = "approve" | "reject";

export interface Repo {
  id: string;
  url: string;
  owner: string;
  registered_at: string;
}

export interface CallSite {
  file_path: string;
  line: number;
  symbol: string;
  is_aliased: boolean;
  alias: string | null;
  snippet: string;
}

export interface SurgeryPlan {
  target_package: string;
  call_sites: CallSite[];
  affected_files: string[];
}

export interface GraphEdge {
  src: string;
  dst: string;
  kind: "depends_on" | "imports" | "calls";
}

export interface GraphLayoutNode {
  id: string;
  x: number;
  y: number;
  kind: "package" | "file" | "call_site";
  label: string;
}

export interface GraphLayout {
  nodes: GraphLayoutNode[];
  edges: GraphEdge[];
}

export interface CentralityScore {
  package: string;
  score: number;
}

export interface LockfileWarning {
  shape: string;
  reason: string;
}

export interface UnderwritingReport {
  id: string;
  repo_id: string;
  target_package: string;
  failing_tests: string[];
  affected_file_count: number;
  centrality: CentralityScore[];
  graph_layout: GraphLayout;
  warnings: LockfileWarning[];
  created_at: string;
}

export interface RegisterRepoResponse {
  repo: Repo;
  surgery_plan: SurgeryPlan;
  graph_layout: GraphLayout;
  underwriting: UnderwritingReport;
}

export interface Incident {
  id: string;
  repo_id: string;
  trigger_type: "mock_cve" | "pr_gate";
  chosen_strategy: StrategyKind | null;
  status: IncidentStatus;
  created_at: string;
  updated_at: string;
}

export interface MitigationOption {
  kind: StrategyKind;
  title: string;
  effort: string;
  blast_radius: string;
  residual_risk: string;
  executable: boolean;
  rationale: string;
}

export interface MitigationCardSet {
  incident_id: string;
  options: MitigationOption[];
}

export interface FireIncidentResponse {
  incident: Incident;
  options: MitigationCardSet;
}

export interface PipelineEvent {
  incident_id: string;
  stage: PipelineStage;
  seq: number;
  message: string;
  at: string;
  terminal: boolean;
}

export interface FileDiff {
  path: string;
  unified_diff: string;
  before: string;
  after: string;
}

export interface JudgeVerdict {
  transplant_id: string;
  judge_name: string;
  verdict: Verdict;
  rationale: string;
}

export interface ConsensusResult {
  approvals: number;
  panel_size: number;
  approved: boolean;
  contested: boolean;
  verdicts: JudgeVerdict[];
}

export interface Transplant {
  id: string;
  incident_id: string;
  surgery_plan: SurgeryPlan;
  diff: FileDiff[];
  consensus: ConsensusResult;
}

export interface PullRequestRef {
  number: number;
  url: string;
}

export interface ReviewResponse {
  review: unknown;
  status: IncidentStatus;
  pull_request: PullRequestRef | null;
}

export interface HealthResponse {
  ok?: boolean;
  use_fakes?: boolean;
  status?: string;
}
