import { useEffect, useState } from 'react';
import type { Filters } from '../lib/types';

interface Props {
  value: Filters;
  onChange: (next: Filters) => void;
  showClient?: boolean;
  showModel?: boolean;
}

const RANGES: { label: string; days: number }[] = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
];

export default function FiltersBar({ value, onChange, showClient = true, showModel = true }: Props) {
  const [from, setFrom] = useState(value.from ?? '');
  const [to, setTo] = useState(value.to ?? '');

  useEffect(() => { setFrom(value.from ?? ''); setTo(value.to ?? ''); }, [value.from, value.to]);

  function applyRange(days: number) {
    const t = new Date();
    const f = new Date();
    f.setUTCDate(t.getUTCDate() - (days - 1));
    onChange({ ...value, from: f.toISOString().slice(0, 10), to: t.toISOString().slice(0, 10) });
  }

  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <div className="flex gap-1">
        {RANGES.map(r => (
          <button key={r.label}
            onClick={() => applyRange(r.days)}
            className="px-2.5 py-1 text-xs rounded-md bg-panel border border-border hover:border-accent">
            {r.label}
          </button>
        ))}
      </div>
      <input type="date" value={from} onChange={e => setFrom(e.target.value)}
        onBlur={() => onChange({ ...value, from: from || undefined })}
        className="bg-panel border border-border rounded-md px-2 py-1 text-xs" />
      <span className="text-muted text-xs">→</span>
      <input type="date" value={to} onChange={e => setTo(e.target.value)}
        onBlur={() => onChange({ ...value, to: to || undefined })}
        className="bg-panel border border-border rounded-md px-2 py-1 text-xs" />
      {showClient && (
        <select value={value.client ?? ''} onChange={e => onChange({ ...value, client: e.target.value || undefined })}
          className="bg-panel border border-border rounded-md px-2 py-1 text-xs">
          <option value="">All clients</option>
          <option value="copilot-cli">copilot-cli</option>
          <option value="openclaw">openclaw</option>
          <option value="hermes">hermes</option>
        </select>
      )}
      {showModel && (
        <input type="text" placeholder="model contains…" value={value.model ?? ''}
          onChange={e => onChange({ ...value, model: e.target.value || undefined })}
          className="bg-panel border border-border rounded-md px-2 py-1 text-xs w-48" />
      )}
    </div>
  );
}
