import type { ReactNode } from 'react';

interface Props {
  title: string;
  value: string | number;
  hint?: string;
  trend?: { delta: number; suffix?: string };
  className?: string;
  children?: ReactNode;
}

export default function KpiCard({ title, value, hint, trend, className, children }: Props) {
  const trendColor = trend ? (trend.delta > 0 ? 'text-rose-400' : 'text-emerald-400') : '';
  const trendSign = trend ? (trend.delta > 0 ? '+' : '') : '';
  return (
    <div className={`bg-panel border border-border rounded-lg p-4 flex flex-col gap-1 ${className ?? ''}`}>
      <div className="text-xs uppercase tracking-wide text-muted">{title}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
      {trend && (
        <div className={`text-xs ${trendColor}`}>
          {trendSign}{trend.delta.toFixed(1)}{trend.suffix ?? '%'} vs prev
        </div>
      )}
      {children}
    </div>
  );
}
