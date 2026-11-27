import type { ReactNode } from 'react'
import { AlertCircle, Inbox, LoaderCircle, X } from 'lucide-react'

export function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: ReactNode }) {
  return <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
    <div>
      {eyebrow && <div className="mb-2 text-xs font-semibold uppercase tracking-[.16em] text-accent">{eyebrow}</div>}
      <h1 className="text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">{title}</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{description}</p>
    </div>
    {action}
  </div>
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'blue' | 'amber' }) {
  const tones = {
    neutral: 'border-slate-700 bg-slate-800/60 text-slate-300',
    green: 'border-emerald-800/70 bg-emerald-950/50 text-emerald-300',
    blue: 'border-sky-800/70 bg-sky-950/50 text-sky-300',
    amber: 'border-amber-800/70 bg-amber-950/50 text-amber-300',
  }
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>
}

export function Loading({ label = 'Загружаем данные…' }: { label?: string }) {
  return <div className="flex min-h-48 items-center justify-center gap-3 text-sm text-muted"><LoaderCircle className="animate-spin text-accent" size={19} />{label}</div>
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="card flex items-start gap-3 border-rose-900/60 bg-rose-950/20 p-5 text-sm text-rose-200">
    <AlertCircle className="mt-0.5 shrink-0" size={19} />
    <div><div className="font-medium">Не получилось открыть данные</div><p className="mt-1 text-rose-300/80">{message}</p>{retry && <button className="btn mt-3" onClick={retry}>Повторить</button>}</div>
  </div>
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return <div className="flex min-h-48 flex-col items-center justify-center p-6 text-center"><Inbox className="mb-3 text-slate-600" size={30} /><div className="font-medium text-slate-300">{title}</div><p className="mt-1 max-w-md text-sm text-muted">{description}</p></div>
}

export function Drawer({ title, subtitle, onClose, children }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode }) {
  return <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm" onMouseDown={onClose}>
    <section className="h-full w-full max-w-2xl overflow-y-auto border-l border-line bg-panel shadow-2xl" onMouseDown={event => event.stopPropagation()}>
      <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-line bg-panel/95 px-6 py-5 backdrop-blur">
        <div><h2 className="text-lg font-semibold text-slate-100">{title}</h2>{subtitle && <p className="mt-1 text-xs text-muted">{subtitle}</p>}</div>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть"><X size={20} /></button>
      </header>
      <div className="p-6">{children}</div>
    </section>
  </div>
}
