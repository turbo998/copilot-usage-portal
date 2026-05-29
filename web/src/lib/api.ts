import useSWR from 'swr';
import type {
  BreakdownRow, CostResponse, DailyMetric, Filters, HeatmapResponse,
  QuotaResponse, TopSession, WeeklyMetric,
} from './types';

const fetcher = async (url: string) => {
  const r = await fetch(url, { credentials: 'include' });
  if (r.status === 401) {
    // Easy Auth not signed in — redirect to login (SWA path)
    window.location.href = '/.auth/login/aad?post_login_redirect_uri=' +
      encodeURIComponent(window.location.pathname + window.location.search);
    return null;
  }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};

function qs(filters: Filters | undefined, extra: Record<string, string | number> = {}): string {
  const sp = new URLSearchParams();
  if (filters) {
    for (const [k, v] of Object.entries(filters)) {
      if (v) sp.set(k, String(v));
    }
  }
  for (const [k, v] of Object.entries(extra)) sp.set(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : '';
}

const swrOpts = { revalidateOnFocus: false, dedupingInterval: 30_000 } as const;

export function useDaily(filters?: Filters) {
  const { data, error, isLoading } = useSWR<{ data: DailyMetric[]; from: string; to: string }>(
    `/api/metrics/daily${qs(filters)}`, fetcher, swrOpts);
  return { data, error, isLoading };
}

export function useWeekly(filters?: Filters) {
  const { data, error, isLoading } = useSWR<{ data: WeeklyMetric[]; from: string; to: string }>(
    `/api/metrics/weekly${qs(filters)}`, fetcher, swrOpts);
  return { data, error, isLoading };
}

export function useBreakdown(dim: 'client' | 'model' | 'host' | 'model_family', filters?: Filters) {
  const { data, error, isLoading } = useSWR<{ data: BreakdownRow[]; dim: string }>(
    `/api/metrics/breakdown${qs(filters, { dim })}`, fetcher, swrOpts);
  return { data, error, isLoading };
}

export function useHeatmap(filters?: Filters) {
  const { data, error, isLoading } = useSWR<HeatmapResponse>(
    `/api/metrics/heatmap${qs(filters)}`, fetcher, swrOpts);
  return { data, error, isLoading };
}

export function useTopSessions(n: number, filters?: Filters) {
  const { data, error, isLoading } = useSWR<{ data: TopSession[] }>(
    `/api/metrics/top-sessions${qs(filters, { n })}`, fetcher, swrOpts);
  return { data, error, isLoading };
}

export function useCost(filters?: Filters) {
  const { data, error, isLoading } = useSWR<CostResponse>(
    `/api/metrics/cost${qs(filters)}`, fetcher, swrOpts);
  return { data, error, isLoading };
}

export function useQuota() {
  const { data, error, isLoading } = useSWR<QuotaResponse>(
    `/api/quota`, fetcher, swrOpts);
  return { data, error, isLoading };
}
