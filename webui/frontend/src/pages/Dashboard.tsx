import { Link } from 'react-router-dom'
import { ArrowRight, BookOpenText, Boxes, Braces, Database, GitBranch, Layers3, ShieldCheck } from 'lucide-react'
import { useApi } from '../hooks'
import { query } from '../api'
import { useDatabase } from '../database-context'
import { formatBytes, formatDate } from '../format'
import type { BundleInfo, DatabaseOverview, ReportInfo } from '../types'
import { EmptyState, ErrorState, Loading, PageHeader } from '../components/UI'

export default function DashboardPage() {
  const { selected } = useDatabase()
  const overview = useApi<DatabaseOverview>(selected ? `/api/database/overview?${query({ path: selected })}` : null)
  const reports = useApi<{ items: ReportInfo[] }>('/api/reports')
  const bundles = useApi<{ items: BundleInfo[] }>('/api/bundles')
  const count = (table: string) => overview.data?.tables.find(item => item.name === table)?.count || 0

  return <>
    <PageHeader eyebrow="Рабочее пространство" title="Всё исследование — в одном окне" description="Навигация по parser registry, таксономии, готовым Family Bundles и отчётам без ручного разбора JSON." action={<div className="flex items-center gap-2 rounded-full border border-emerald-900/60 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300"><ShieldCheck size={15} />Read-only</div>} />

    {overview.error ? <ErrorState message={overview.error} retry={overview.reload} /> : overview.loading ? <Loading /> : !overview.data ? <EmptyState title="Базы не найдены" description="Добавьте SQLite-файлы в research, затем обновите страницу." /> : <>
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Parser revisions', value: count('parser_families'), icon: GitBranch, note: 'Версии грамматик' },
          { label: 'Grok-конструкции', value: count('grok_patterns'), icon: Braces, note: 'Переиспользуемые паттерны' },
          { label: 'Нормализации', value: count('normalization_revisions'), icon: Layers3, note: 'Ревизии нормализации' },
          { label: 'Family Bundles', value: bundles.data?.items.length || 0, icon: Boxes, note: 'Готовые к исполнению' },
        ].map(item => <div key={item.label} className="card p-5">
          <div className="flex items-start justify-between"><span className="text-xs font-medium text-muted">{item.label}</span><item.icon size={18} className="text-accent/80" /></div>
          <div className="mt-4 text-3xl font-semibold tracking-tight text-white">{item.value.toLocaleString('ru-RU')}</div>
          <div className="mt-1 text-xs text-slate-500">{item.note}</div>
        </div>)}
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-5 py-4"><div><h2 className="font-medium text-slate-100">Состав текущей базы</h2><p className="mt-1 text-xs text-muted">{overview.data.name} · {formatBytes(overview.data.size)}</p></div><Link to="/database" className="btn">Открыть <ArrowRight size={15} /></Link></div>
          <div className="divide-y divide-line/70">{overview.data.tables.filter(table => table.count > 0).map(table => <Link to={`/database?table=${table.name}`} key={table.name} className="flex items-center justify-between px-5 py-3.5 transition hover:bg-white/[.025]">
            <div className="flex items-center gap-3"><Database size={15} className="text-slate-500" /><span className="text-sm text-slate-300">{table.label}</span></div><span className="font-mono text-xs text-muted">{table.count.toLocaleString('ru-RU')}</span>
          </Link>)}</div>
          <div className="border-t border-line bg-canvas/30 px-5 py-3 text-xs text-muted">Изменено: {formatDate(overview.data.modified_at)}</div>
        </div>

        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-5 py-4"><h2 className="font-medium text-slate-100">Последние исследования</h2><BookOpenText size={18} className="text-muted" /></div>
          <div className="divide-y divide-line/70">{reports.data?.items.filter(item => item.research_id).slice(0, 5).map(report => <Link to={`/reports?path=${encodeURIComponent(report.path)}`} key={report.path} className="block px-5 py-4 transition hover:bg-white/[.025]">
            <div className="text-[11px] font-semibold tracking-wider text-accent">{report.research_id}</div><div className="mt-1 line-clamp-2 text-sm leading-5 text-slate-300">{report.title}</div>
          </Link>)}</div>
          <Link to="/reports" className="flex items-center justify-center gap-2 border-t border-line px-5 py-3.5 text-xs font-medium text-accent hover:bg-accent/5">Все отчёты <ArrowRight size={14} /></Link>
        </div>
      </section>
    </>}
  </>
}
