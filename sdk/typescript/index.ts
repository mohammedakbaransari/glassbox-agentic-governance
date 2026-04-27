/**
 * GlassBox Runtime Decision Governance — TypeScript/Node.js SDK
 * =============================================================
 * Zero-dependency thin wrapper around the GlassBox REST API.
 * Compatible with Node.js 18+ (native fetch) and modern browsers.
 *
 * Installation: npm install @glassbox/governance-sdk
 *
 * Usage:
 *   import { GlassBoxClient, DecisionType } from '@glassbox/governance-sdk';
 *
 *   const client = new GlassBoxClient({ baseUrl: 'http://localhost:8000' });
 *
 *   const result = await client.govern({
 *     agent_id:      'procurement_bot',
 *     decision_type: DecisionType.PROCUREMENT,
 *     payload: { amount: 75000, supplier_id: 'SUP-001', category: 'hardware' },
 *   });
 *
 *   if (result.final_status === 'blocked') {
 *     console.log('Blocked:', result.policy_violations);
 *   }
 *
 * Author: Mohammed Akbar Ansari — Independent Researcher
 * License: Apache 2.0
 */

// ── Enums ──────────────────────────────────────────────────────────────────────

export enum DecisionType {
  PROCUREMENT = 'procurement',
  PRICING     = 'pricing',
  FINANCIAL   = 'financial',
  INVENTORY   = 'inventory',
  LOGISTICS   = 'logistics',
  IT_OPS      = 'it_ops',
  HR          = 'hr',
  CUSTOM      = 'custom',
  // v1.1 additions
  CLINICAL    = 'clinical',
  TRADING     = 'trading',
  CONTENT     = 'content',
  LEGAL       = 'legal',
}

export enum FinalStatus {
  EXECUTED       = 'executed',
  BLOCKED        = 'blocked',
  PENDING_REVIEW = 'pending_review',
  REPLAYED       = 'replayed',
}

// ── Request / Response types ───────────────────────────────────────────────────

export interface DecisionContext {
  confidence?:   number;
  environment?:  string;
  source_system?: string;
  user_override?: boolean;
  agent_chain?:  string[];
  currency?:     string;  // ISO 4217
  jurisdiction?: string;  // ISO 3166-1
  metadata?:     Record<string, unknown>;
}

export interface GovernDecisionRequest {
  agent_id:      string;
  decision_type: DecisionType | string;
  payload:       Record<string, unknown>;
  context?:      DecisionContext;
}

export interface GovernDecisionResponse {
  decision_id:               string;
  final_status:              FinalStatus | string;
  risk_score?:               number;
  risk_level?:               string;
  disposition?:              string;
  policy_violations:         string[];
  policy_warnings:           string[];
  circuit_breaker_triggered: boolean;
  circuit_breaker_reason?:   string;
  message:                   string;
  pipeline_latency_ms?:      number;
  risk_explanation?:         string;
  explanation?:              string;
  audit_record?:             Record<string, unknown>;
}

export interface BatchRequest {
  decisions:   GovernDecisionRequest[];
  max_workers?: number;
}

export interface BatchResult {
  results:  GovernDecisionResponse[];
  errors:   Array<{ index: number; error: string }>;
  summary: {
    total:            number;
    executed:         number;
    blocked:          number;
    pending_review:   number;
    parse_errors:     number;
    batch_latency_ms: number;
  };
}

export interface GlassBoxClientConfig {
  baseUrl:     string;
  timeoutMs?:  number;
  headers?:    Record<string, string>;
  onBlocked?:  (response: GovernDecisionResponse) => void;
}

export class GovernanceBlockedError extends Error {
  constructor(
    public readonly response: GovernDecisionResponse,
  ) {
    super(
      `Decision ${response.decision_id} BLOCKED: ` +
      response.policy_violations.join('; ')
    );
    this.name = 'GovernanceBlockedError';
  }
}

// ── Main client ────────────────────────────────────────────────────────────────

export class GlassBoxClient {
  private baseUrl:    string;
  private timeoutMs:  number;
  private headers:    Record<string, string>;
  private onBlocked?: (r: GovernDecisionResponse) => void;

  constructor(config: GlassBoxClientConfig) {
    this.baseUrl   = config.baseUrl.replace(/\/$/, '');
    this.timeoutMs = config.timeoutMs ?? 5000;
    this.headers   = {
      'Content-Type': 'application/json',
      ...config.headers,
    };
    this.onBlocked = config.onBlocked;
  }

  /**
   * Submit a single decision for governance.
   * Throws GovernanceBlockedError if the decision is blocked.
   */
  async govern(request: GovernDecisionRequest): Promise<GovernDecisionResponse> {
    const response = await this._post<GovernDecisionResponse>('/decisions', request);
    if (response.final_status === FinalStatus.BLOCKED) {
      this.onBlocked?.(response);
      throw new GovernanceBlockedError(response);
    }
    return response;
  }

  /**
   * Submit a single decision without throwing on block.
   * Returns the full response regardless of outcome.
   */
  async governSafe(request: GovernDecisionRequest): Promise<GovernDecisionResponse> {
    return this._post<GovernDecisionResponse>('/decisions', request);
  }

  /**
   * Submit multiple decisions in parallel.
   * Returns all results including blocked ones.
   */
  async governBatch(requests: GovernDecisionRequest[], maxWorkers = 4): Promise<BatchResult> {
    const body: BatchRequest = { decisions: requests, max_workers: maxWorkers };
    return this._post<BatchResult>('/decisions/batch', body);
  }

  /** Get a specific audit record by decision ID. */
  async getDecision(decisionId: string): Promise<Record<string, unknown>> {
    return this._get(`/decisions/${decisionId}`);
  }

  /** List audit records with optional status filter. */
  async listDecisions(params?: {
    status?: string;
    agent_id?: string;
    limit?: number;
  }): Promise<{ records: unknown[]; count: number }> {
    let qs = '';
    if (params) {
      const searchParams = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined) {
          searchParams.append(key, String(value));
        }
      });
      qs = '?' + searchParams.toString();
    }
    return this._get(`/decisions${qs}`);
  }

  /** Get governance statistics. */
  async stats(): Promise<Record<string, unknown>> {
    return this._get('/stats');
  }

  /** Get velocity circuit breaker status for an agent. */
  async velocityStatus(agentId: string): Promise<Record<string, unknown>> {
    return this._get(`/agents/${agentId}/velocity`);
  }

  /** List all registered policies. */
  async listPolicies(): Promise<{ policies: unknown[] }> {
    return this._get('/policies');
  }

  /** Health check. */
  async health(): Promise<{ status: string }> {
    return this._get('/health');
  }

  /**
   * Connect to the real-time governance event stream (SSE).
   * Returns a function to close the connection.
   *
   * Example:
   *   const close = client.streamEvents((event) => {
   *     console.log(event.event_type, event.payload);
   *   });
   *   // later:
   *   close();
   */
  streamEvents(
    onEvent: (event: { event_type: string; payload: unknown }) => void,
    onError?: (error: Event) => void,
  ): () => void {
    if (typeof EventSource === 'undefined') {
      throw new Error('EventSource is not available in this environment');
    }
    const es = new EventSource(`${this.baseUrl}/events/stream`);
    es.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch { /* ignore malformed */ }
    };
    if (onError) es.onerror = onError;
    return () => es.close();
  }

  // ── HTTP helpers ────────────────────────────────────────────────────────────

  private async _post<T>(path: string, body: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        method:  'POST',
        headers: this.headers,
        body:    JSON.stringify(body),
        signal:  controller.signal,
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        throw new Error(`GlassBox API error ${resp.status}: ${text}`);
      }
      return resp.json() as Promise<T>;
    } finally {
      clearTimeout(timer);
    }
  }

  private async _get<T = Record<string, unknown>>(path: string): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        method:  'GET',
        headers: this.headers,
        signal:  controller.signal,
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        throw new Error(`GlassBox API error ${resp.status}: ${text}`);
      }
      return resp.json() as Promise<T>;
    } finally {
      clearTimeout(timer);
    }
  }
}

// ── Convenience factory ────────────────────────────────────────────────────────

/**
 * Create a GlassBoxClient with sensible defaults.
 *
 * @param baseUrl   GlassBox REST API URL (default: http://localhost:8000)
 * @param options   Optional client configuration overrides
 */
export function createClient(
  baseUrl = 'http://localhost:8000',
  options: Partial<GlassBoxClientConfig> = {},
): GlassBoxClient {
  return new GlassBoxClient({ baseUrl, ...options });
}

export default GlassBoxClient;
