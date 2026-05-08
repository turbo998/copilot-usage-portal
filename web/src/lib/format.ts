/** Number formatting helpers. */

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 });
const std = new Intl.NumberFormat('en', { maximumFractionDigits: 0 });

export const fmtTokens = (n: number | undefined | null): string =>
  n == null ? '—' : compact.format(n);

export const fmtTokensFull = (n: number | undefined | null): string =>
  n == null ? '—' : std.format(n);

export const fmtCredits = (n: number | undefined | null): string =>
  n == null ? '—' : (n < 0.01 ? n.toFixed(4) : n.toFixed(2));

export const fmtPct = (n: number | undefined | null, digits = 1): string =>
  n == null ? '—' : `${(n * 100).toFixed(digits)}%`;

export const fmtDateRange = (from?: string, to?: string): string =>
  from && to ? `${from} → ${to}` : '—';

export const palette = [
  '#7aa2ff', '#a48cff', '#ff8fa3', '#ffa657', '#ffd166',
  '#7ee787', '#56d4dd', '#d2a8ff', '#ff7eb6', '#79c0ff',
  '#bd93f9', '#50fa7b', '#ff79c6', '#8be9fd', '#f1fa8c',
];

export const colorForKey = (_key: string, idx: number): string =>
  palette[idx % palette.length] || '#7aa2ff';
