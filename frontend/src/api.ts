import type {
  FireIncidentResponse,
  HealthResponse,
  PipelineEvent,
  RegisterRepoResponse,
  ReviewResponse,
  StrategyKind,
  Transplant
} from "./types";

export interface ApiOptions {
  baseUrl: string;
  token: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class DepCoverApi {
  private readonly baseUrl: string;
  private readonly token: string;

  constructor(options: ApiOptions) {
    this.baseUrl = options.baseUrl.trim();
    this.token = options.token.trim();
  }

  health(): Promise<HealthResponse> {
    return this.get<HealthResponse>("/health");
  }

  registerRepo(url: string, owner: string): Promise<RegisterRepoResponse> {
    return this.post<RegisterRepoResponse>("/repos", { url, owner });
  }

  fireIncident(repoId: string): Promise<FireIncidentResponse> {
    return this.post<FireIncidentResponse>("/incidents", { repo_id: repoId });
  }

  chooseStrategy(incidentId: string, strategy: StrategyKind): Promise<unknown> {
    return this.post(`/incidents/${incidentId}/strategy`, { strategy });
  }

  getTransplant(transplantId: string): Promise<Transplant> {
    return this.get<Transplant>(`/transplants/${transplantId}`);
  }

  submitReview(transplant: Transplant, accept: boolean): Promise<ReviewResponse> {
    return this.post<ReviewResponse>(`/transplants/${transplant.id}/review`, {
      decision: accept ? "accept_all" : "reject",
      per_file: transplant.diff.map((file, index) => ({
        path: file.path,
        kind: accept || index > 0 ? "accept" : "reject",
        reason: accept || index > 0 ? null : "needs manual review"
      })),
      reason: accept ? null : "rejected on review"
    });
  }

  streamIncident(
    incidentId: string,
    onEvent: (event: PipelineEvent) => void,
    onError: (message: string) => void
  ): EventSource {
    const source = new EventSource(this.url(`/incidents/${incidentId}/stream`));
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data) as PipelineEvent);
      } catch {
        onError("pipeline stream returned malformed data");
      }
    };
    source.onerror = () => {
      onError("pipeline stream disconnected");
      source.close();
    };
    return source;
  }

  private async get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "GET" });
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: JSON.stringify(body)
    });
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await fetch(this.url(path), {
      ...init,
      headers: {
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
        ...init.headers
      }
    });
    if (!response.ok) {
      throw new ApiError(await this.errorMessage(response), response.status);
    }
    return (await response.json()) as T;
  }

  private async errorMessage(response: Response): Promise<string> {
    const text = await response.text();
    if (text === "") {
      return `HTTP ${response.status}`;
    }
    try {
      const parsed = JSON.parse(text) as { detail?: { message?: string } | string };
      if (typeof parsed.detail === "string") {
        return parsed.detail;
      }
      if (parsed.detail?.message) {
        return parsed.detail.message;
      }
    } catch {
      return text;
    }
    return text;
  }

  private url(path: string): string {
    if (this.baseUrl === "") {
      return path;
    }
    return new URL(path.replace(/^\//, ""), `${this.baseUrl.replace(/\/$/, "")}/`).toString();
  }
}
