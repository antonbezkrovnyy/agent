import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Box, Search } from 'lucide-react'
import { query } from '../api'
import { useApi } from '../hooks'
import type { BundleInfo } from '../types'
import { Badge, EmptyState, ErrorState, Loading, PageHeader } from '../components/UI'
import StructuredView from '../components/StructuredView'

const tabs = [
  { id: 'overview', label: 'Обзор' },
  { id: 'parser', label: 'Парсер' },
  { id: 'normalization', label: 'Нормализация' },
  { id: 'mapping', label: 'MP SIEM mapping' },
  { id: 'dispatch', label: 'Диспетчеризация' },
]

export default function BundlesPage() {
  const [params, setParams] = useSearchParams()
  const bundles = useApi<{ items: BundleInfo[] }>('/api/bundles')
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState('overview')
  const selected = params.get('path') || ''
  const detail = useApi<{ content: Record<string, unknown> }>(selected ? `/api/bundle?${query({ path: selected })}` : null)
  const items = (bundles.data?.items || []).filter(item => `${item.family_id} ${item.path}`.toLowerCase().includes(search.toLowerCase()))
  const info = bundles.data?.items.find(item => item.path === selected)

  useEffect(() => {
    if (!selected && bundles.data?.items.length) setParams({ path: bundles.data.items[0].path }, { replace: true })
  }, [bundles.data, selected, setParams])

  const content = detail.data?.content
  const tabValue = tab === 'overview' ? {
    schema_version: content?.schema_version,
    family_id: content?.family_id,
    parser_revision: info?.parser_revision,
    normalization_revision: info?.normalization_revision,
    mapping_revision: info?.mapping_revision,
    taxonomy: info?.taxonomy,
    evidence: info?.evidence_count,
    golden: info?.golden_path,
  } : content?.[tab]

  return <>
    <PageHeader eyebrow="Rust data plane" title="Family Bundles" description="Готовые версионированные контракты: parser, normalization, mapping и dispatch. Здесь можно изучить всё содержимое, не запуская исполнение." />
    <div className="grid gap-5 xl:grid-cols-[360px_1fr]">
      <aside className="card h-fit overflow-hidden xl:sticky xl:top-28">
        <div className="border-b border-line p-3"><div className="relative"><Search size={16} className="absolute left-3 top-3 text-slate-500" /><input className="input w-full pl-9" placeholder="Найти Family…" value={search} onChange={event => setSearch(event.target.value)} /></div></div>
        <div className="max-h-[600px] overflow-y-auto p-2">{bundles.loading ? <Loading /> : items.map(item => <button key={item.path} onClick={() => { setParams({ path: item.path }); setTab('overview') }} className={`mb-1 w-full rounded-xl px-3.5 py-3.5 text-left transition ${selected === item.path ? 'bg-accent/10' : 'hover:bg-white/[.03]'}`}>
          <div className="flex items-start gap-3"><Box size={16} className={`mt-0.5 shrink-0 ${selected === item.path ? 'text-accent' : 'text-slate-600'}`} /><div className="min-w-0"><div className="break-words text-xs font-medium leading-5 text-slate-200">{item.family_id}</div><div className="mt-1.5 flex flex-wrap gap-1.5 text-[10px] text-muted"><span>parser v{item.parser_revision}</span><span>·</span><span>norm v{item.normalization_revision}</span><span>·</span><span>map v{item.mapping_revision}</span></div></div></div>
        </button>)}</div>
      </aside>
      <section className="card min-w-0 overflow-hidden">
        {detail.loading ? <Loading label="Читаем bundle…" /> : detail.error ? <ErrorState message={detail.error} retry={detail.reload} /> : content ? <>
          <header className="border-b border-line p-5 sm:p-6"><div className="flex flex-wrap items-center gap-2"><Badge tone="green">{info?.taxonomy || 'bundle'}</Badge><Badge>{info?.evidence_count || 0} evidence</Badge>{info?.golden_path && <Badge tone="blue">golden доступен</Badge>}</div><h2 className="mt-4 break-words text-xl font-semibold text-slate-100">{info?.family_id}</h2><p className="mt-2 break-all font-mono text-[11px] text-muted">{selected}</p></header>
          <nav className="flex gap-1 overflow-x-auto border-b border-line px-3">{tabs.map(item => <button key={item.id} onClick={() => setTab(item.id)} className={`shrink-0 border-b-2 px-3.5 py-3.5 text-xs font-medium transition ${tab === item.id ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-slate-200'}`}>{item.label}</button>)}</nav>
          <div className="p-4 sm:p-6">{tabValue ? <StructuredView value={tabValue} /> : <EmptyState title="Раздел пуст" description="В этом bundle такой блок не заполнен." />}</div>
        </> : <EmptyState title="Выберите bundle" description="Список готовых Family находится слева." />}
      </section>
    </div>
  </>
}
