/**
 * Shared metric record types used by all dashboards.
 * Mirrors the API contract documented in concept/docs/ARCHITECTURE.md §5.
 */

export interface DailyMetric {
  date: string;
  prompt: number;
  completion: number;
  cached: number;
  reasoning: number;
  total: number;
  credits: number;
}

export interface WeeklyMetric extends Omit<DailyMetric, 'date'> {
  week: string;
}

export interface BreakdownRow {
  key: string;
  total: number;
  credits: number;
  calls: number;
  share: number;
}

export interface HeatmapResponse {
  from: string;
  to: string;
  rows: string[];
  cols: string[];
  tokens: number[][];
  credits: number[][];
  calls: number[][];
}

export interface TopSession {
  session_id: string;
  client: string;
  model: string;
  total: number;
  credits: number;
  calls: number;
  first_ts: string;
  last_ts: string;
}

export interface CostResponse {
  from: string;
  to: string;
  daily: { date: string; credits: number; total: number }[];
  by_model: { model: string; credits: number; total: number }[];
  // Added by feat/credits-backend; may be absent until that PR lands.
  by_token_type?: { token_type: string; credits: number; total: number }[];
  total_credits: number;
  month_to_date_credits: number;
  projected_month_credits: number;
  notes: string;
}

export interface QuotaResponse {
  id: string;
  plan: 'Pro+' | 'Pro' | 'Business' | 'Enterprise' | string;
  monthly_credits_included: number;
  paid_credit_unit_price_usd: number;
  updated_at?: string | null;
  source?: 'cosmos' | 'default';
}

export type Filters = {
  from?: string;
  to?: string;
  client?: string;
  model?: string;
  host?: string;
};
