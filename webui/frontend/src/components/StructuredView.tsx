import { fieldLabel, isPrimitive } from '../format'

function Primitive({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') return <span className="text-slate-600">—</span>
  if (typeof value === 'boolean') return <span className={value ? 'text-emerald-300' : 'text-slate-400'}>{value ? 'Да' : 'Нет'}</span>
  if (typeof value === 'number') return <span className="font-mono text-sky-300">{String(value)}</span>
  return <span className="whitespace-pre-wrap break-words font-mono text-[13px] leading-6 text-slate-300">{String(value)}</span>
}

export default function StructuredView({ value, level = 0 }: { value: unknown; level?: number }) {
  if (isPrimitive(value)) return <Primitive value={value} />
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-sm text-slate-600">Пустой список</span>
    if (value.every(isPrimitive)) return <div className="space-y-2">{value.map((item, index) => <div key={index} className="rounded-lg border border-line/70 bg-canvas/60 px-3 py-2"><Primitive value={item} /></div>)}</div>
    return <div className="space-y-3">{value.map((item, index) => <details key={index} open={level === 0 && index === 0} className="rounded-xl border border-line bg-canvas/40 p-3">
      <summary className="cursor-pointer text-xs font-medium text-muted">Элемент {index + 1}</summary>
      <div className="mt-3"><StructuredView value={item} level={level + 1} /></div>
    </details>)}</div>
  }
  const entries = Object.entries(value as Record<string, unknown>)
  if (!entries.length) return <span className="text-sm text-slate-600">Нет полей</span>
  return <div className="space-y-3">{entries.map(([key, item]) => <div key={key} className={isPrimitive(item) ? 'grid gap-2 border-b border-line/50 pb-3 last:border-0 sm:grid-cols-[170px_1fr]' : 'rounded-xl border border-line/70 bg-canvas/30 p-3.5'}>
    <div className="text-xs font-semibold uppercase tracking-wide text-muted">{fieldLabel(key)}</div>
    <div className="min-w-0"><StructuredView value={item} level={level + 1} /></div>
  </div>)}</div>
}
