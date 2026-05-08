import { useMemo, useState } from 'react';
import FiltersBar from '../components/FiltersBar';
import { Card, Empty, Page } from '../components/Page';
import { useHeatmap } from '../lib/api';
import { fmtCredits, fmtTokens } from '../lib/format';
import type { Filters } from '../lib/types';

function defaultRange(days = 30): Filters {
  const t = new Date();
  const f = new Date();
  f.setUTCDate(t.getUTCDate() - (days - 1));
  return { from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) };
}

type Metric = 'tokens' | 'credits' | 'calls';

export default function HeatmapPage() {
  const [filters, setFilters] = useState<Filters>(defaultRange());
  const [metric, setMetric] = useState<Metric>('tokens');
  const { data, isLoading } = useHeatmap(filters);

  const matrix: number[][] = data ? data[metric] : [];
  const max = useMemo(() => {
    let m = 0;
    for (const row of matrix) for (const v of row) if (v > m) m = v;
    return m || 1;
  }, [matrix]);

  function color(v: number) {
    const t = Math.sqrt(v / max); // perceptual scaling
    const a = Math.max(0.04, t);
    return `rgba(122, 162, 255, ${a.toFixed(3)})`;
  }

  const fmt = metric === 'credits' ? fmtCredits : (v: number) => fmtTokens(v);

  return (
    <Page title="Heatmap" subtitle="Client × Model usage matrix">
      <FiltersBar value={filters} onChange={setFilters} showClient={false} showModel={false} />

      <div className="flex gap-2 mb-3">
        {(['tokens', 'credits', 'calls'] as Metric[]).map(m => (
          <button key={m} onClick={() => setMetric(m)}
            className={`px-3 py-1 text-xs rounded-md border ${metric === m ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:text-white'}`}>
            {m}
          </button>
        ))}
      </div>

      <Card>
        {isLoading ? <Empty message="Loading…" /> : (!data || data.rows.length === 0 || data.cols.length === 0) ? <Empty /> : (
          <div className="overflow-x-auto">
            <table className="text-xs">
              <thead>
                <tr>
                  <th className="text-left p-2 sticky left-0 bg-panel">client \\ model</th>
                  {data.cols.map(c => (
                    <th key={c} className="p-2 font-mono font-normal text-muted text-left whitespace-nowrap"
                      title={c}>
                      <span className="block max-w-[140px] truncate">{c}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, ri) => (
                  <tr key={r}>
                    <td className="p-2 font-medium sticky left-0 bg-panel">{r}</td>
                    {data.cols.map((c, ci) => {
                      const v = matrix[ri]?.[ci] ?? 0;
                      return (
                        <td key={c} className="p-0">
                          <div className="m-0.5 rounded px-2 py-2 min-w-[80px] text-center tabular-nums"
                            style={{ background: color(v) }}
                            title={`${r} × ${c}: ${fmt(v)}`}>
                            {v > 0 ? fmt(v) : ''}
                          </div>
                        </td>
                      );
                    })}
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
