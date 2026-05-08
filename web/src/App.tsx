import { Link, NavLink, Route, Routes } from 'react-router-dom';
import Overview from './pages/Overview';
import ByClient from './pages/ByClient';
import ByModel from './pages/ByModel';
import Heatmap from './pages/Heatmap';
import Sessions from './pages/Sessions';
import Cost from './pages/Cost';

const navItems = [
  { to: '/', label: 'Overview' },
  { to: '/by-client', label: 'By Client' },
  { to: '/by-model', label: 'By Model' },
  { to: '/heatmap', label: 'Heatmap' },
  { to: '/sessions', label: 'Sessions' },
  { to: '/cost', label: 'Cost' },
];

export default function App() {
  return (
    <div className="min-h-full flex">
      <aside className="w-56 bg-panel border-r border-border p-4 flex flex-col gap-1">
        <Link to="/" className="text-lg font-semibold mb-4">Copilot Usage</Link>
        {navItems.map(it => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.to === '/'}
            className={({ isActive }) =>
              `px-3 py-2 rounded-md text-sm ${isActive ? 'bg-accent/20 text-accent' : 'text-muted hover:text-white hover:bg-border/50'}`}
          >
            {it.label}
          </NavLink>
        ))}
        <div className="mt-auto text-xs text-muted">
          <a href="/.auth/logout" className="hover:text-white">Sign out</a>
        </div>
      </aside>
      <main className="flex-1 overflow-x-hidden">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/by-client" element={<ByClient />} />
          <Route path="/by-model" element={<ByModel />} />
          <Route path="/heatmap" element={<Heatmap />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/cost" element={<Cost />} />
        </Routes>
      </main>
    </div>
  );
}
