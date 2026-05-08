import type { ReactNode } from 'react';

export function Page({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="p-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="text-sm text-muted mt-1">{subtitle}</p>}
      </header>
      {children}
    </div>
  );
}

export function Card({ title, children, action }: { title?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="bg-panel border border-border rounded-lg p-4">
      {(title || action) && (
        <div className="flex items-center justify-between mb-3">
          {title && <h2 className="text-sm font-medium text-white">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ message = 'No data yet — collectors haven\u2019t reported anything in this range.' }) {
  return <div className="text-sm text-muted py-8 text-center">{message}</div>;
}
