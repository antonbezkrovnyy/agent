import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { BookOpenText, Boxes, Database, Eye, LayoutDashboard, Radar } from 'lucide-react'
import { useApi } from './hooks'
import { DatabaseContext } from './database-context'
import type { DatabaseInfo } from './types'
import DashboardPage from './pages/Dashboard'
import DatabasePage from './pages/Database'
import BundlesPage from './pages/Bundles'
import ReportsPage from './pages/Reports'
import ErrorBoundary from './components/ErrorBoundary'

const navigation = [
  { to: '/', label: 'Обзор', icon: LayoutDashboard },
  { to: '/database', label: 'База данных', icon: Database },
  { to: '/bundles', label: 'Family Bundles', icon: Boxes },
  { to: '/reports', label: 'Исследования', icon: BookOpenText },
]

export default function App() {
  const { data, loading } = useApi<{ items: DatabaseInfo[] }>('/api/databases')
  const databases = data?.items || []
  const [selected, setSelected] = useState(() => localStorage.getItem('workbench-db') || '')

  useEffect(() => {
    if (!databases.length) return
    if (!databases.some(item => item.path === selected)) setSelected(databases[0].path)
  }, [databases, selected])

  useEffect(() => {
    if (selected) localStorage.setItem('workbench-db', selected)
  }, [selected])

  return <DatabaseContext.Provider value={{ databases, selected, setSelected, loading }}>
    <div className="min-h-screen bg-canvas">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-line bg-panel/70 md:flex">
        <div className="flex h-20 items-center gap-3 border-b border-line px-6">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-canvas"><Radar size={21} strokeWidth={2.4} /></div>
          <div><div className="text-sm font-semibold tracking-tight text-white">Log Research</div><div className="text-[11px] tracking-wide text-muted">WORKBENCH</div></div>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {navigation.map(item => <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm transition ${isActive ? 'bg-accent/10 font-medium text-accent' : 'text-muted hover:bg-white/[.035] hover:text-slate-200'}`}>
            <item.icon size={18} />{item.label}
          </NavLink>)}
        </nav>
        <div className="m-4 rounded-xl border border-line bg-canvas/60 p-3.5">
          <div className="flex items-center gap-2 text-xs font-medium text-emerald-300"><Eye size={14} />Только просмотр</div>
          <p className="mt-2 text-[11px] leading-5 text-muted">SQLite открывается в read-only. Запуск и изменение данных отключены.</p>
        </div>
      </aside>

      <div className="md:pl-60">
        <header className="sticky top-0 z-20 border-b border-line bg-canvas/90 backdrop-blur-xl">
          <div className="flex min-h-20 items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3 md:hidden"><Radar className="text-accent" size={23} /><span className="font-semibold">Log Research</span></div>
            <div className="hidden text-sm text-muted md:block">Исследовательские данные <span className="mx-2 text-slate-700">/</span> <span className="text-slate-300">test_agent</span></div>
            <label className="flex min-w-0 items-center gap-2">
              <Database size={15} className="shrink-0 text-muted" />
              <select className="input max-w-[230px] truncate py-2 text-xs sm:max-w-[330px]" value={selected} onChange={event => setSelected(event.target.value)} disabled={!databases.length} aria-label="Текущая база данных">
                {!databases.length && <option>Нет баз данных</option>}
                {databases.map(item => <option key={item.path} value={item.path}>{item.name}</option>)}
              </select>
            </label>
          </div>
          <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:hidden">{navigation.map(item => <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-xs ${isActive ? 'bg-accent/10 text-accent' : 'text-muted'}`}><item.icon size={15} />{item.label}</NavLink>)}</nav>
        </header>
        <main className="mx-auto max-w-[1600px] p-4 sm:p-6 lg:p-8">
          <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/database" element={<DatabasePage />} />
            <Route path="/bundles" element={<BundlesPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  </DatabaseContext.Provider>
}
