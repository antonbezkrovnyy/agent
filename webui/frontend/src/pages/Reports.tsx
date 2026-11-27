import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Search } from 'lucide-react'
import { useApi } from '../hooks'
import { query } from '../api'
import type { ReportInfo } from '../types'
import { EmptyState, ErrorState, Loading, PageHeader } from '../components/UI'

export default function ReportsPage() {
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const reports = useApi<{ items: ReportInfo[] }>('/api/reports')
  const filtered = (reports.data?.items || []).filter(item => `${item.research_id} ${item.title} ${item.name}`.toLowerCase().includes(search.toLowerCase()))
  const selected = params.get('path') || ''
  const detail = useApi<{ content: string }>(selected ? `/api/report?${query({ path: selected })}` : null)

  useEffect(() => {
    const firstReport = reports.data?.items.find(item => item.research_id) || reports.data?.items[0]
    if (!selected && firstReport) setParams({ path: firstReport.path }, { replace: true })
  }, [reports.data, selected, setParams])

  return <>
    <PageHeader eyebrow="Research archive" title="Исследования" description="Отчёты R001–R035 с нормальным Markdown, таблицами, ссылками и быстрым поиском по названиям." />
    <div className="grid min-h-[700px] gap-5 xl:grid-cols-[340px_1fr]">
      <aside className="card flex max-h-[calc(100vh-150px)] flex-col overflow-hidden xl:sticky xl:top-28">
        <div className="border-b border-line p-3"><div className="relative"><Search size={16} className="absolute left-3 top-3 text-slate-500" /><input className="input w-full pl-9" placeholder="Найти исследование…" value={search} onChange={event => setSearch(event.target.value)} /></div></div>
        <div className="overflow-y-auto p-2">{reports.loading ? <Loading /> : filtered.map(report => <button key={report.path} onClick={() => setParams({ path: report.path })} className={`mb-1 w-full rounded-xl px-3.5 py-3 text-left transition ${selected === report.path ? 'bg-accent/10 text-slate-100' : 'text-slate-400 hover:bg-white/[.03]'}`}>
          <div className={`text-[10px] font-bold tracking-widest ${selected === report.path ? 'text-accent' : 'text-slate-600'}`}>{report.research_id || 'INFO'}</div><div className="mt-1 line-clamp-2 text-xs leading-5">{report.title}</div>
        </button>)}</div>
      </aside>
      <article className="card min-w-0 p-5 sm:p-8 lg:p-10">
        {detail.loading ? <Loading label="Открываем отчёт…" /> : detail.error ? <ErrorState message={detail.error} retry={detail.reload} /> : detail.data ? <div className="markdown-body mx-auto max-w-5xl"><ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.data.content}</ReactMarkdown></div> : <EmptyState title="Выберите отчёт" description="Список исследований находится слева." />}
      </article>
    </div>
  </>
}
