import { useMemo, useState } from 'react';
import { Area, AreaChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useBreakdown, useDaily } from '../lib/api';
import { colorForKey, fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';
import FiltersBar from '../components/FiltersBar';
import KpiCard from '../components/KpiCard';
import { Card, Empty, Page } from '../components/Page';

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

export default function Overview() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const daily = useDaily(filters);
  const byClient = useBreakdown('client', filters);
  const byModel = useBreakdown('model', filters);

  const totals = useMemo(() => {
    const rows = daily.data?.data ?? [];
    const sum = (k: keyof typeof rows[number]) => rows.reduce((a, r) => a + (r[k] as number || 0), 0);
    const today = rows[rows.length - 1];
    const yest = rows[rows.length - 2];
    const todayDelta = today && yest && yest.total > 0
      ? ((today.total - yest.total) / yest.total) * 100 : 0;
    return {
      total: sum('total'),
      cached: sum('cached'),
      credits: sum('credits'),
      todayTotal: today?.total ?? 0,
      todayDelta,
    };
  }, [daily.data]);

  const cacheHitPct = totals.total > 0 ? (totals.cached / totals.total) * 100 : 0;

  return (
    <Page title="Overview" subtitle={`Range: ${filters.from} → ${filters.to}`}>
      <FiltersBar value={filters} onChange={setFilters} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <KpiCard title="Total tokens" value={fmtTokens(totals.total)} hint="prompt + completion + cached" />
        <KpiCard title="Today" value={fmtTokens(totals.todayTotal)}
          trend={{ delta: totals.todayDelta }} />
        <KpiCard title="Cache hit" value={`${cacheHitPct.toFixed(1)}%`} hint={fmtTokens(totals.cached) + ' cached'} />
        <KpiCard title="Est. AI Credits" value={fmtCredits(totals.credits)} hint="rough estimate, see Cost page" />
      </div>

      <div className="grid lg:grid-cols-2 gap-3">
        <Card title="Daily token volume">
          {daily.isLoading ? <Empty message="Loading…" /> : (daily.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={daily.data!.data}>
                <defs>
                  <linearGradient id="gPrompt" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7aa2ff" stopOpacity={0.7} />
                    <stop offset="100%" stopColor="#7aa2ff" stopOpacity={0.1} />
                  </linearGradient>
                  <linearGradient id="gCached" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#a48cff" stopOpacity={0.6} />
                    <stop offset="100%" stopColor="#a48cff" stopOpacity={0.05} />
                  </linearGradient>
                  <linearGradient id="gCompl" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7ee787" stopOpacity={0.6} />
                    <stop offset="100%" stopColor="#7ee787" stopOpacity={0.05} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#1f2530" />
                <XAxis dataKey="date" stroke="#7d8597" fontSize={11} />
                <YAxis stroke="#7d8597" fontSize={11} tickFormatter={(v) => fmtTokens(v)} />
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number) => fmtTokens(v)} />
                <Legend />
                <Area type="monotone" dataKey="prompt" name="Prompt" stackId="1" stroke="#7aa2ff" fill="url(#gPrompt)" />
                <Area type="monotone" dataKey="cached" name="Cached" stackId="1" stroke="#a48cff" fill="url(#gCached)" />
                <Area type="monotone" dataKey="completion" name="Completion" stackId="1" stroke="#7ee787" fill="url(#gCompl)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Distribution by client">
          {byClient.isLoading ? <Empty message="Loading…" /> : (byClient.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie data={byClient.data!.data} dataKey="total" nameKey="key"
                  outerRadius={90} innerRadius={50} paddingAngle={2}>
                  {byClient.data!.data.map((d, i) => (
                    <Cell key={d.key} fill={colorForKey(d.key, i)} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number, n) => [fmtTokens(v), n]} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-3 mt-3">
        <Card title="Top models (tokens)">
          {byModel.isLoading ? <Empty message="Loading…" /> : (byModel.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <table className="w-full text-sm">
              <thead className="text-muted text-xs">
                <tr>
                  <th className="text-left font-normal pb-2">Model</th>
                  <th className="text-right font-normal pb-2">Tokens</th>
                  <th className="text-right font-normal pb-2">Calls</th>
                  <th className="text-right font-normal pb-2">Share</th>
                </tr>
              </thead>
              <tbody>
                {byModel.data!.data.slice(0, 10).map(r => (
                  <tr key={r.key} className="border-t border-border">
                    <td className="py-2 font-mono text-xs">{r.key}</td>
                    <td className="py-2 text-right tabular-nums">{fmtTokens(r.total)}</td>
                    <td className="py-2 text-right tabular-nums">{r.calls}</td>
                    <td className="py-2 text-right tabular-nums">{(r.share * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Daily AI Credits estimate">
          {daily.isLoading ? <Empty message="Loading…" /> : (daily.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={daily.data!.data}>
                <defs>
                  <linearGradient id="gCred" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#ffa657" stopOpacity={0.7} />
                    <stop offset="100%" stopColor="#ffa657" stopOpacity={0.05} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#1f2530" />
                <XAxis dataKey="date" stroke="#7d8597" fontSize={11} />
                <YAxis stroke="#7d8597" fontSize={11} />
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number) => fmtCredits(v)} />
                <Area type="monotone" dataKey="credits" name="Credits" stroke="#ffa657" fill="url(#gCred)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>
    </Page>
  );
}
