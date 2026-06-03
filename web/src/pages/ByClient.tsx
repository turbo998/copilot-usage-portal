import { useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import FiltersBar from '../components/FiltersBar';
import { Card, Empty, Page } from '../components/Page';
import { useBreakdown, useDaily } from '../lib/api';
import { colorForKey, fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';

const CLIENTS = ['copilot-cli', 'openclaw', 'hermes'];

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

export default function ByClient() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const breakdown = useBreakdown('client', filters);
  const cliDaily = useDaily({ ...filters, client: 'copilot-cli' });
  const oclawDaily = useDaily({ ...filters, client: 'openclaw' });
  const hermesDaily = useDaily({ ...filters, client: 'hermes' });

  type StackedRow = Record<string, string | number> & { date: string };
  const stacked = useMemo<StackedRow[]>(() => {
    const map = new Map<string, StackedRow>();
    const sets: [string, ReturnType<typeof useDaily>][] = [
      ['copilot-cli', cliDaily],
      ['openclaw', oclawDaily],
      ['hermes', hermesDaily],
    ];
    for (const [name, q] of sets) {
      for (const row of q.data?.data ?? []) {
        const slot: StackedRow = map.get(row.date) ?? ({ date: row.date } as StackedRow);
        slot[name] = row.total;
        map.set(row.date, slot);
      }
    }
    return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [cliDaily.data, oclawDaily.data, hermesDaily.data]);

  return (
    <Page title="By Client" subtitle="Compare CLI vs OpenClaw vs Hermes usage">
      <FiltersBar value={filters} onChange={setFilters} showClient={false} />

      <div className="grid lg:grid-cols-2 gap-3 mb-3">
        <Card title="Share of total tokens">
          {breakdown.isLoading ? <Empty message="Loading…" /> : (breakdown.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie data={breakdown.data!.data} dataKey="total" nameKey="key" outerRadius={100} innerRadius={56}>
                  {breakdown.data!.data.map((d, i) => <Cell key={d.key} fill={colorForKey(d.key, i)} />)}
                </Pie>
                <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                  formatter={(v: number, n) => [fmtTokens(v), n]} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Per-client totals">
          {breakdown.isLoading ? <Empty message="Loading…" /> : (breakdown.data?.data?.length ?? 0) === 0 ? <Empty /> : (
            <table className="w-full text-sm">
              <thead className="text-muted text-xs">
                <tr>
                  <th className="text-left font-normal pb-2">Client</th>
                  <th className="text-right font-normal pb-2">Tokens</th>
                  <th className="text-right font-normal pb-2">Calls</th>
                  <th className="text-right font-normal pb-2">Credits</th>
                  <th className="text-right font-normal pb-2">Share</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.data!.data.map((r, i) => (
                  <tr key={r.key} className="border-t border-border">
                    <td className="py-2">
                      <span className="inline-block w-2 h-2 rounded-full mr-2"
                        style={{ background: colorForKey(r.key, i) }} />
                      {r.key}
                    </td>
                    <td className="py-2 text-right tabular-nums">{fmtTokens(r.total)}</td>
                    <td className="py-2 text-right tabular-nums">{r.calls}</td>
                    <td className="py-2 text-right tabular-nums">{fmtCredits(r.credits)}</td>
                    <td className="py-2 text-right tabular-nums">{(r.share * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="Daily stacked tokens by client">
        {(cliDaily.isLoading || oclawDaily.isLoading || hermesDaily.isLoading) ? <Empty message="Loading…" /> : stacked.length === 0 ? <Empty /> : (
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={stacked}>
              <CartesianGrid stroke="#1f2530" />
              <XAxis dataKey="date" stroke="#7d8597" fontSize={11} />
              <YAxis stroke="#7d8597" fontSize={11} tickFormatter={(v) => fmtTokens(v)} />
              <Tooltip contentStyle={{ background: '#11141b', border: '1px solid #1f2530' }}
                formatter={(v: number) => fmtTokens(v)} />
              <Legend />
              {CLIENTS.map((c, i) => (
                <Bar key={c} dataKey={c} stackId="a" fill={colorForKey(c, i)} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </Card>
    </Page>
  );
}
