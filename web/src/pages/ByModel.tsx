import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import FiltersBar from '../components/FiltersBar';
import { Card, Empty, Page } from '../components/Page';
import { useBreakdown } from '../lib/api';
import { colorForKey, fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

export default function ByModel() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const byModel = useBreakdown('model', filters);
  const byFamily = useBreakdown('model_family', filters);

  return (
    <Page title="By Model" subtitle="Per-model and per-family token usage">
      <FiltersBar value={filters} onChange={setFilters} showModel={false} />

      <div className="grid lg:grid-cols-3 gap-3 mb-3">
        <Card title="Family share">
          {(byFamily.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie data={byFamily.data!.data} dataKey="total" nameKey="key" outerRadius={90} innerRadius={50}>
                  {byFamily.data!.data.map((d, i) => <Cell key={d.key} fill={colorForKey(d.key, i)} />)}
                </Pie>
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number, n) => [fmtTokens(v), n]} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>

        <div className="lg:col-span-2">
          <Card title="Top models">
            {(byModel.data?.data?.length ?? 0) === 0 ? <Empty /> : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={byModel.data!.data.slice(0, 12)} layout="vertical" margin={{ left: 80 }}>
                  <CartesianGrid stroke="#1f2530" />
                  <XAxis type="number" stroke="#7d8597" fontSize={11} tickFormatter={(v) => fmtTokens(v)} />
                  <YAxis type="category" dataKey="key" stroke="#7d8597" fontSize={10} width={140} />
                  <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                    formatter={(v: number) => fmtTokens(v)} />
                  <Bar dataKey="total" fill="#7aa2ff">
                    {byModel.data!.data.slice(0, 12).map((d, i) => (
                      <Cell key={d.key} fill={colorForKey(d.key, i)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </Card>
        </div>
      </div>

      <Card title="All models">
        {(byModel.data?.data?.length ?? 0) === 0 ? <Empty /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-muted text-xs">
                <tr>
                  <th className="text-left font-normal pb-2 pr-4">Model</th>
                  <th className="text-right font-normal pb-2 pr-4">Tokens</th>
                  <th className="text-right font-normal pb-2 pr-4">Calls</th>
                  <th className="text-right font-normal pb-2 pr-4">Credits</th>
                  <th className="text-right font-normal pb-2 pr-4">Share</th>
                </tr>
              </thead>
              <tbody>
                {byModel.data!.data.map((r) => (
                  <tr key={r.key} className="border-t border-border">
                    <td className="py-2 pr-4 font-mono text-xs">{r.key}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{fmtTokens(r.total)}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{r.calls}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{fmtCredits(r.credits)}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{(r.share * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Page>
  );
}
