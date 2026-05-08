import { useState } from 'react';
import FiltersBar from '../components/FiltersBar';
import { Card, Empty, Page } from '../components/Page';
import { useTopSessions } from '../lib/api';
import { fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

export default function Sessions() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const [n, setN] = useState(25);
  const { data, isLoading } = useTopSessions(n, filters);

  return (
    <Page title="Top Sessions" subtitle="Sessions ranked by token consumption">
      <FiltersBar value={filters} onChange={setFilters} />
      <div className="flex items-center gap-2 mb-3 text-xs">
        <span className="text-muted">Show top</span>
        {[10, 25, 50, 100].map(v => (
          <button key={v} onClick={() => setN(v)}
            className={`px-2 py-1 rounded-md border ${n === v ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:text-white'}`}>
            {v}
          </button>
        ))}
      </div>

      <Card>
        {isLoading ? <Empty message="Loading…" /> : (data?.data?.length ?? 0) === 0 ? <Empty /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-muted text-xs">
                <tr>
                  <th className="text-left font-normal pb-2 pr-4">Session</th>
                  <th className="text-left font-normal pb-2 pr-4">Client</th>
                  <th className="text-left font-normal pb-2 pr-4">Model</th>
                  <th className="text-right font-normal pb-2 pr-4">Tokens</th>
                  <th className="text-right font-normal pb-2 pr-4">Calls</th>
                  <th className="text-right font-normal pb-2 pr-4">Credits</th>
                  <th className="text-left font-normal pb-2 pr-4">First</th>
                  <th className="text-left font-normal pb-2">Last</th>
                </tr>
              </thead>
              <tbody>
                {data!.data.map(s => (
                  <tr key={s.session_id} className="border-t border-border">
                    <td className="py-2 pr-4 font-mono text-xs max-w-[220px] truncate" title={s.session_id}>
                      {s.session_id}
                    </td>
                    <td className="py-2 pr-4">{s.client}</td>
                    <td className="py-2 pr-4 font-mono text-xs">{s.model}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{fmtTokens(s.total)}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{s.calls}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">{fmtCredits(s.credits)}</td>
                    <td className="py-2 pr-4 text-xs text-muted">{s.first_ts.slice(0, 16).replace('T', ' ')}</td>
                    <td className="py-2 text-xs text-muted">{s.last_ts.slice(0, 16).replace('T', ' ')}</td>
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
