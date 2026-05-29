import { useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import FiltersBar from '../components/FiltersBar';
import KpiCard from '../components/KpiCard';
import { Card, Empty, Page } from '../components/Page';
import { useCost, useQuota } from '../lib/api';
import { fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

const fmtUSD = (n: number): string =>
  new Intl.NumberFormat('en', { style: 'currency', currency: 'USD',
    maximumFractionDigits: 2 }).format(n);

export default function Credits() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const { data: cost, isLoading: costLoading } = useCost(filters);
  const { data: quota, isLoading: quotaLoading } = useQuota();

  const monthlyIncluded = quota?.monthly_credits_included ?? 1500;
  const unitPrice = quota?.paid_credit_unit_price_usd ?? 0.01;
  const planName = quota?.plan ?? 'Pro+';

  const mtd = cost?.month_to_date_credits ?? 0;
  const projected = cost?.projected_month_credits ?? 0;
  const usedPct = monthlyIncluded > 0 ? (mtd / monthlyIncluded) * 100 : 0;
  const projPct = monthlyIncluded > 0 ? (projected / monthlyIncluded) * 100 : 0;
  const headroom = Math.max(0, monthlyIncluded - mtd);
  const overageCredits = Math.max(0, projected - monthlyIncluded);
  const overageUSD = overageCredits * unitPrice;

  const quotaSource = quota?.source === 'cosmos' ? 'live config' : 'default fallback';

  return (
    <Page
      title="Credits"
      subtitle={`GHCP credit-based billing — plan: ${planName} · ${monthlyIncluded.toLocaleString()} credits/mo · paid credit ${fmtUSD(unitPrice)}`}
    >
      <FiltersBar value={filters} onChange={setFilters} />

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <KpiCard
          title="Plan"
          value={quotaLoading ? '…' : planName}
          hint={`source: ${quotaSource}`}
        />
        <KpiCard
          title="Included / month"
          value={fmtCredits(monthlyIncluded)}
          hint={`paid credit ${fmtUSD(unitPrice)}`}
        />
        <KpiCard
          title="Month-to-date"
          value={fmtCredits(mtd)}
          hint={`${usedPct.toFixed(1)}% of included`}
        >
          <div className="mt-2 h-1.5 bg-border rounded">
            <div className="h-1.5 rounded bg-accent"
              style={{ width: `${Math.min(100, usedPct)}%` }} />
          </div>
        </KpiCard>
        <KpiCard
          title="Projected this month"
          value={fmtCredits(projected)}
          hint={`${projPct.toFixed(1)}% of included`}
        >
          <div className="mt-2 h-1.5 bg-border rounded">
            <div className={`h-1.5 rounded ${projPct > 100 ? 'bg-rose-400' : 'bg-emerald-400'}`}
              style={{ width: `${Math.min(100, projPct)}%` }} />
          </div>
        </KpiCard>
        <KpiCard
          title={overageCredits > 0 ? 'Projected overage' : 'Headroom'}
          value={overageCredits > 0 ? fmtUSD(overageUSD) : fmtCredits(headroom)}
          hint={overageCredits > 0
            ? `${fmtCredits(overageCredits)} paid credits`
            : 'credits remaining'}
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-3">
        <Card title="Daily credits vs. monthly quota">
          {costLoading ? <Empty message="Loading…" />
            : (cost?.daily?.length ?? 0) === 0 ? <Empty /> : (() => {
              // Daily quota line = monthly included / days in current month.
              const today = new Date();
              const daysInMonth = new Date(today.getUTCFullYear(),
                today.getUTCMonth() + 1, 0).getUTCDate();
              const dailyQuota = monthlyIncluded / daysInMonth;
              let cum = 0;
              const series = cost!.daily.map(d => ({
                ...d, cum: (cum += d.credits),
              }));
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
                    <Line yAxisId="r" type="monotone" dataKey="cum"
                      name="Cumulative" stroke="#7aa2ff" dot={false} />
                    <ReferenceLine yAxisId="l" y={dailyQuota} stroke="#7ee787"
                      strokeDasharray="4 4"
                      label={{ value: `Daily quota ${fmtCredits(dailyQuota)}`,
                        fill: '#7ee787', fontSize: 10, position: 'insideTopRight' }} />
                  </ComposedChart>
                </ResponsiveContainer>
              );
            })()}
        </Card>

        <Card title="Credits by model">
          {(cost?.by_model?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={cost!.by_model.slice(0, 12)} layout="vertical" margin={{ left: 80 }}>
                <CartesianGrid stroke="#1f2530" />
                <XAxis type="number" stroke="#7d8597" fontSize={11} />
                <YAxis type="category" dataKey="model" stroke="#7d8597" fontSize={10} width={140} />
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number, n: string) =>
                    [n === 'total' ? fmtTokens(v) : fmtCredits(v), n]} />
                <Bar dataKey="credits" fill="#a48cff" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Credits by token type">
          {(cost?.by_token_type?.length ?? 0) === 0 ? (
            <Empty message="No token-type breakdown yet — pending backend split (prompt / completion / cached / reasoning)." />
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={cost!.by_token_type!} layout="vertical" margin={{ left: 80 }}>
                <CartesianGrid stroke="#1f2530" />
                <XAxis type="number" stroke="#7d8597" fontSize={11} />
                <YAxis type="category" dataKey="token_type" stroke="#7d8597"
                  fontSize={10} width={140} />
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number, n: string) =>
                    [n === 'total' ? fmtTokens(v) : fmtCredits(v), n]} />
                <Bar dataKey="credits" fill="#56d4dd" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Plan summary">
          <dl className="text-sm grid grid-cols-2 gap-y-2">
            <dt className="text-muted">Plan</dt>
            <dd>{planName}</dd>
            <dt className="text-muted">Monthly credits included</dt>
            <dd>{monthlyIncluded.toLocaleString()}</dd>
            <dt className="text-muted">Paid credit unit price</dt>
            <dd>{fmtUSD(unitPrice)}</dd>
            <dt className="text-muted">MTD credits used</dt>
            <dd>{fmtCredits(mtd)} ({usedPct.toFixed(1)}%)</dd>
            <dt className="text-muted">Projected EoM</dt>
            <dd>{fmtCredits(projected)} ({projPct.toFixed(1)}%)</dd>
            <dt className="text-muted">Projected paid overage</dt>
            <dd>
              {overageCredits > 0
                ? `${fmtCredits(overageCredits)} × ${fmtUSD(unitPrice)} = ${fmtUSD(overageUSD)}`
                : '—'}
            </dd>
            {quota?.updated_at && (
              <>
                <dt className="text-muted">Config updated</dt>
                <dd className="text-xs">{quota.updated_at}</dd>
              </>
            )}
          </dl>
        </Card>
      </div>

      <p className="text-xs text-muted mt-4">
        Credits are estimated from the per-model rate table at ingest time.
        Plan configuration is sourced from the Cosmos <code>plan_config</code> container
        (id=<code>current</code>); falls back to <code>Pro+ / 1500 credits / $0.01 per paid credit</code> when unset.
      </p>
    </Page>
  );
}
