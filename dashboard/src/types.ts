// Typed shapes for the three committed exports. These MIRROR the backend's
// model_dump — the dashboard never invents fields, it only reads these.

export interface Comparison {
  seed: number;
  n_episodes: number;
  control_frac: number;
  scorecards: Scorecard[];
}

export interface Scorecard {
  policy: string;
  seed: number;
  control_frac: number;
  n_episodes: number;
  n_treatment: number;
  n_control: number;
  n_blocked: number;
  incremental_recovery_rate: number;
  incremental_recovery_ci_low: number;
  incremental_recovery_ci_high: number;
  incremental_amount_paise: number;
  raw_recovery_rate: number;
  false_effort_actions: number;
  false_effort_amount_paise: number;
  per_cause: CauseStat[];
  exceptions: Exception[];
  assumptions: Record<string, number>;
  assumptions_note: string;
}

export interface CauseStat {
  cause: string;
  n_treatment: number;
  n_control: number;
  n_blocked: number;
  treatment_recovery_rate: number;
  control_recovery_rate: number;
  incremental_lift: number;
  lift_ci_low: number;
  lift_ci_high: number;
  incremental_amount_paise: number;
}

export interface Exception {
  episode_id: string;
  cause: string;
  reason: string;
}

export interface TraceFile {
  seed: number;
  episodes: TraceEpisode[];
}

export interface TraceEpisode {
  episode_id: string;
  example: "fault" | "blocked" | "clean" | string;
  meta: { amount: number; method: string; error_code: string; attempt: number };
  steps: TraceStep[];
}

export interface TraceStep {
  step: string;
  // payload shape varies by step; the viewer reads known keys defensively.
  payload: Record<string, unknown>;
  signature: string | null;
}

export interface AskEntry {
  question: string;
  answer: string;
  matched: number;
  cited_episode_ids: string[];
  used_llm: boolean;
  translation_fallback: boolean;
  narration_fallback: boolean;
  filter: Record<string, unknown>;
  rows: AskRow[];
}

export interface AskRow {
  episode_id: string;
  amount?: number;
  method?: string;
  cause?: string;
  confidence?: number;
  intervention?: string;
  policy_status?: string;
  executed?: string;
  recovered?: boolean;
  fault_reason?: string;
  steps: string[];
}
