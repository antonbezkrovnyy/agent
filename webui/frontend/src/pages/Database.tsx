import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, RefreshCw, Search } from 'lucide-react'
import { query } from '../api'
import { useApi } from '../hooks'
import { useDatabase } from '../database-context'
import { cellText, fieldLabel, formatBytes, formatDate } from '../format'
import type { DatabaseOverview, TableRows } from '../types'
import { Drawer, EmptyState, ErrorState, Loading, PageHeader, Badge } from '../components/UI'
import StructuredView from '../components/StructuredView'

export default function DatabasePage() {
  const { selected } = useDatabase()
  const [params, setParams] = useSearchParams()
  const overview = useApi<DatabaseOverview>(selected ? `/api/database/overview?${query({ path: selected })}` : null)
  const table = params.get('table') || ''
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<string>()
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc')
  const [selectedRow, setSelectedRow] = useState<number | null>(null)
  const rows = useApi<TableRows>(selected && table ? `/api/database/table?${query({ path: selected, table, page, page_size: pageSize, search, sort, direction })}` : null)
  const record = useApi<Record<string, unknown>>(selected && table && selectedRow !== null ? `/api/database/record?${query({ path: selected, table, rowid: selectedRow })}` : null)

  useEffect(() => {
    if (!overview.data?.tables.length) return
    if (!overview.data.tables.some(item => item.name === table)) setParams({ table: overview.data.tables[0].name }, { replace: true })
  }, [overview.data, table, setParams])

  useEffect(() => {
    setPage(1)
    setSelectedRow(null)
    setSort(undefined)
  }, [selected, table])

  const selectTable = (name: string) => setParams({ table: name })
  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault()
    setPage(1)
    setSearch(searchInput)
  }
  const changeSort = (column: string) => {
    if (column === '__rowid__') return
    setDirection(sort === column && direction === 'asc' ? 'desc' : 'asc')
    setSort(column)
    setPage(1)
  }
  const refresh = () => { void overview.reload(); void rows.reload() }
  const currentTable = overview.data?.tables.find(item => item.name === table)

  return <>
    <PageHeader eyebrow="SQLite explorer" title="База данных" description="Таблицы, ревизии и вложенные поля представлены как данные, а не как простыня JSON. Нажмите на любую строку, чтобы раскрыть её целиком." action={<button className="btn" onClick={refresh}><RefreshCw size={15} />Обновить</button>} />
    {overview.error ? <ErrorState message={overview.error} retry={overview.reload} /> : overview.loading ? <Loading /> : !overview.data?.tables.length ? <EmptyState title="База пуста" description="В выбранном SQLite-файле нет пользовательских таблиц." /> : <>
      <div className="mb-5 flex flex-col gap-3 rounded-2xl border border-line bg-panel/60 p-3 text-xs text-muted sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2"><Badge tone="green">read-only</Badge><span className="break-all">{overview.data.path}</span></div>
        <div className="shrink-0">{formatBytes(overview.data.size)} · {formatDate(overview.data.modified_at)}</div>
      </div>
      <div className="mb-5 flex gap-2 overflow-x-auto pb-2">{overview.data.tables.map(item => <button key={item.name} onClick={() => selectTable(item.name)} className={`flex shrink-0 items-center gap-2 rounded-xl border px-3.5 py-2.5 text-xs transition ${table === item.name ? 'border-accent/40 bg-accent/10 text-accent' : 'border-line bg-panel text-muted hover:text-slate-200'}`}>
        {item.label}<span className={`rounded-md px-1.5 py-0.5 font-mono text-[10px] ${table === item.name ? 'bg-accent/10' : 'bg-canvas'}`}>{item.count}</span>
      </button>)}</div>

      <section className="card overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="font-medium text-slate-100">{currentTable?.label || table}</h2><p className="mt-1 text-xs text-muted">{rows.data?.total.toLocaleString('ru-RU') || 0} записей</p></div>
          <form className="flex w-full gap-2 sm:w-auto" onSubmit={submitSearch}><div className="relative flex-1"><Search size={15} className="absolute left-3 top-3 text-slate-500" /><input className="input w-full pl-9 sm:w-72" placeholder="Поиск по всем полям…" value={searchInput} onChange={event => setSearchInput(event.target.value)} /></div><button className="btn" type="submit">Найти</button></form>
        </div>
        {rows.error ? <ErrorState message={rows.error} retry={rows.reload} /> : rows.loading ? <Loading /> : !rows.data?.rows.length ? <EmptyState title="Ничего не найдено" description={search ? 'Попробуйте изменить поисковый запрос.' : 'В этой таблице пока нет записей.'} /> : <>
          <div className="overflow-x-auto"><table className="w-full border-collapse"><thead><tr>{rows.data.columns.map(column => <th key={column} className="th"><button className="flex items-center gap-1.5 uppercase" onClick={() => changeSort(column)}>{fieldLabel(column)}{sort === column && (direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}</button></th>)}</tr></thead>
            <tbody>{rows.data.rows.map((row, index) => <tr key={String(row.__rowid__ ?? index)} onClick={() => setSelectedRow(Number(row.__rowid__))} className="cursor-pointer transition hover:bg-accent/[.035]">{rows.data?.columns.map(column => <td key={column} className="td" title={cellText(row[column])}><div className="line-clamp-3 break-words">{cellText(row[column])}</div></td>)}</tr>)}</tbody>
          </table></div>
          <div className="flex flex-col gap-3 border-t border-line px-4 py-3 text-xs text-muted sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">Строк на странице<select className="input py-1.5" value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1) }}><option>10</option><option>25</option><option>50</option><option>100</option></select></div>
            <div className="flex items-center gap-3"><span>Страница {rows.data.page} из {rows.data.pages}</span><button className="icon-btn border border-line" disabled={page <= 1} onClick={() => setPage(value => value - 1)}><ChevronLeft size={17} /></button><button className="icon-btn border border-line" disabled={page >= rows.data.pages} onClick={() => setPage(value => value + 1)}><ChevronRight size={17} /></button></div>
          </div>
        </>}
      </section>
    </>}

    {selectedRow !== null && <Drawer title={`${currentTable?.label || table} · #${selectedRow}`} subtitle="Полная запись · только чтение" onClose={() => setSelectedRow(null)}>
      {record.loading ? <Loading /> : record.error ? <ErrorState message={record.error} retry={record.reload} /> : record.data ? <StructuredView value={record.data} /> : null}
    </Drawer>}
  </>
}
