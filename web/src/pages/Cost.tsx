import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis, ComposedChart } from 'recharts';
import FiltersBar from '../components/FiltersBar';
import KpiCard from '../components/KpiCard';
import { Card, Empty, Page } from '../components/Page';
import { useCost } from '../lib/api';
import { fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

const QUOTAS: Record<string, number> = {
  'Pro': 300,
  'Pro+': 1500,
  'Business': 300,
  'Enterprise': 1000,
};

export default function Cost() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const [tier, setTier] = useState<keyof typeof QUOTAS>('Pro+');
  const { data, isLoading } = useCost(filters);

  const quota = QUOTAS[tier];
  const usedPct = data ? (data.month_to_date_credits / quota) * 100 : 0;
  const projPct = data ? (data.projected_month_credits / quota) * 100 : 0;

  return (
    <Page title="Cost (estimated)" subtitle="AI Credits estimation based on per-model rate table">
      <FiltersBar value={filters} onChange={setFilters} />

      <div className="flex items-center gap-2 mb-3 text-xs">
        <span className="text-muted">Plan</span>
        {(Object.keys(QUOTAS) as (keyof typeof QUOTAS)[]).map(t => (
          <button key={t} onClick={() => setTier(t)}
            className={`px-2 py-1 rounded-md border ${tier === t ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:text-white'}`}>
            {t} ({QUOTAS[t]})
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <KpiCard title="Total credits (range)" value={fmtCredits(data?.total_credits)} />
        <KpiCard title="Month-to-date" value={fmtCredits(data?.month_to_date_credits)}
          hint={`${usedPct.toFixed(1)}% of ${quota}`}>
          <div className="mt-2 h-1.5 bg-border rounded">
            <div className="h-1.5 rounded bg-accent" style={{ width: `${Math.min(100, usedPct)}%` }} />
          </div>
        </KpiCard>
        <KpiCard title="Projected this month" value={fmtCredits(data?.projected_month_credits)}
          hint={`${projPct.toFixed(1)}% of ${quota}`}>
          <div className="mt-2 h-1.5 bg-border rounded">
            <div className={`h-1.5 rounded ${projPct > 100 ? 'bg-rose-400' : 'bg-emerald-400'}`}
              style={{ width: `${Math.min(100, projPct)}%` }} />
          </div>
        </KpiCard>
        <KpiCard title="Plan headroom" value={fmtCredits(quota - (data?.month_to_date_credits ?? 0))}
          hint={`Tier: ${tier}`} />
      </div>

      <div className="grid lg:grid-cols-2 gap-3">
        <Card title="Daily credits + cumulative">
          {isLoading ? <Empty message="Loading…" /> : (data?.daily?.length ?? 0) === 0 ? <Empty /> : (() => {
            let cum = 0;
            const series = data!.daily.map(d => ({ ...d, cum: (cum += d.credits) }));
            return (
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={series}>
                  <CartesianGrid stroke="#1f2530" />
                  <XAxis dataKey="date" stroke="#7d8597" fontSize={11} />
                  <YAxis yAxisId="l" stroke="#7d8597" fontSize={11} />
                  <YAxis yAxisId="r" orientation="right" stroke="#7d8597" fontSize={11} />
                  <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                    formatter={(v: number) => fmtCredits(v)} />
                  <Legend />
                  <Bar yAxisId="l" dataKey="credits" name="Daily credits" fill="#ffa657" />
                  <Line yAxisId="r" type="monotone" dataKey="cum" name="Cumulative" stroke="#7aa2ff" dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            );
          })()}
        </Card>

        <Card title="Credits by model">
          {isLoading ? <Empty message="Loading…" /> : (data?.by_model?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={data!.by_model.slice(0, 12)} layout="vertical" margin={{ left: 80 }}>
                <CartesianGrid stroke="#1f2530" />
                <XAxis type="number" stroke="#7d8597" fontSize={11} />
                <YAxis type="category" dataKey="model" stroke="#7d8597" fontSize={10} width={140} />
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number, n: string) => [n === 'total' ? fmtTokens(v) : fmtCredits(v), n]} />
                <Bar dataKey="credits" fill="#a48cff" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {data?.notes && (
        <p className="text-xs text-muted mt-4">{data.notes}</p>
      )}
    </Page>
  );
}
