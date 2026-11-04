"use client";

import { useMemo, useState } from "react";
import { useAgent, UseAgentUpdate } from "@copilotkit/react-core/v2";
import type { AbstractAgent } from "@ag-ui/client";
import {
  Dict,
  formatDuration,
  formatNumber,
  formatTime,
  intentLabel,
  list,
  modeLabel,
  number,
  record,
  resultSummary,
  roleLabel,
  stageLabel,
  statusLabel,
  statusTone,
  text,
  windowLabel,
} from "./format";
import { Badge, CopyButton, EmptyState, KeyValue, Section, StatusBadge } from "./ui";
import { EvidenceExplorer } from "./evidence";
import { ReportView } from "./report";
import { InvestigationTree } from "./tree";
import { SteeringPanel } from "./steering";

type Tab = "overview" | "evidence" | "report";
type SideTab = "plan" | "hypotheses" | "findings" | "metrics";
type TimelineEntry = { at: string; kind: string; title: string; detail: string; status: unknown };

function timelineOf(workItems: Dict[], evidence: Dict[], hypotheses: Dict[], metrics: Dict[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const item of workItems) {
    const at = text(item.finished_at || item.started_at, "");
    if (at) entries.push({
      at,
      kind: "plan",
      title: `${text(item.id)} · ${statusLabel(item.status)}`,
      detail: text(item.goal),
      status: item.status,
    });
  }
  for (const item of evidence) {
    entries.push({
      at: text(item.created_at, ""),
      kind: "evidence",
      title: `${text(item.tool)} · ${statusLabel(item.status)}`,
      detail: `${resultSummary(item.result)} · ${windowLabel(item.window)}`,
      status: item.status,
    });
  }
  for (const hypothesis of hypotheses) {
    entries.push({
      at: text(hypothesis.updated_at || hypothesis.created_at, ""),
      kind: "hypothesis",
      title: `${text(hypothesis.id)} · ${statusLabel(hypothesis.status)}`,
      detail: text(hypothesis.text),
      status: hypothesis.status,
    });
  }
  for (const metric of metrics) {
    entries.push({
      at: text(metric.finished_at, ""),
      kind: "role",
      title: `${roleLabel(metric.agent)} · завершено`,
      detail: `${formatDuration(metric.duration_ms)} · ${formatNumber(metric.total_tokens)} токенов · ${formatNumber(metric.tool_calls)} вызовов`,
      status: "done",
    });
  }
  return entries.sort((a, b) => b.at.localeCompare(a.at));
}

function MetricCard({ label, value, detail, current = false }: { label: string; value: React.ReactNode; detail?: string; current?: boolean }) {
  return (
    <div className={`metric-card ${current ? "metric-current" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function CurrentTask({ state }: { state: Dict }) {
  const progress = record(state.progress);
  const intent = record(state.intent);
  const scope = record(state.investigation_scope);
  const activeItem = record(state.active_work_item);
  return (
    <section className="current-task">
      <div className="current-task-main">
        <div className="eyebrow">ТЕКУЩАЯ ЗАДАЧА</div>
        <h2>{text(scope.objective ?? intent.rationale, "Диалог без активного расследования")}</h2>
        <div className="current-stage-line">
          <Badge tone={statusTone(progress.state)}>{stageLabel(progress.stage)}</Badge>
          <span>{roleLabel(progress.agent)}</span>
          {!!activeItem.id && <span>· этап {String(activeItem.id)}</span>}
        </div>
      </div>
      <div className="current-task-facts">
        <div><span>Тип запроса</span><strong>{intentLabel(intent.label)}</strong></div>
        <div><span>Режим</span><strong>{modeLabel(intent.work_mode ?? state.work_mode)}</strong></div>
        <div><span>Охват</span><strong>{modeLabel(scope.mode)}</strong></div>
      </div>
      {!!activeItem.goal && <div className="active-item"><span>Сейчас проверяется</span>{String(activeItem.goal)}</div>}
    </section>
  );
}

function WorkPlan({ items }: { items: Dict[] }) {
  const done = items.filter((item) => item.status === "done").length;
  return (
    <>
      <div className="side-panel-summary"><strong>{done} / {items.length}</strong><span>этапов завершено</span></div>
      {items.length > 0 && <div className="plan-progress"><span style={{ width: `${Math.round(done / items.length * 100)}%` }} /></div>}
      {items.length === 0 ? (
        <EmptyState title="Декомпозиция не нужна">Для обычного вопроса и быстрой проверки агент работает без отдельных этапов.</EmptyState>
      ) : (
        <div className="work-items">
          {items.map((item, index) => (
            <article className={`work-item work-${statusTone(item.status)}`} key={text(item.id, String(index))}>
              <div className="work-item-top">
                <strong>{text(item.id)}</strong>
                <StatusBadge status={item.status} />
              </div>
              <p>{text(item.goal)}</p>
              {!!item.acceptance && <details><summary>Критерий завершения</summary><small>{String(item.acceptance)}</small></details>}
              <div className="work-item-meta">
                <span>{formatTime(item.started_at)} → {formatTime(item.finished_at)}</span>
                {number(item.attempt) > 0 && <span>попытка {number(item.attempt)}</span>}
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

function Hypotheses({ items }: { items: Dict[] }) {
  if (!items.length) return <EmptyState title="Гипотез пока нет">Investigator будет записывать предположения и отмечать, подтверждены они или отклонены.</EmptyState>;
  return (
    <div className="hypotheses">
      {items.map((hypothesis, index) => (
        <article className={`hypothesis hypothesis-${statusTone(hypothesis.status)}`} key={text(hypothesis.id, String(index))}>
          <div><strong>{text(hypothesis.id)}</strong><StatusBadge status={hypothesis.status} /></div>
          <p>{text(hypothesis.text)}</p>
          {!!hypothesis.reason && <small>{String(hypothesis.reason)}</small>}
          {Array.isArray(hypothesis.evidence_ids) && hypothesis.evidence_ids.length > 0 && (
            <div className="evidence-links">{(hypothesis.evidence_ids as unknown[]).map((id) => <span key={String(id)}>{String(id)}</span>)}</div>
          )}
        </article>
      ))}
    </div>
  );
}

function Findings({ items }: { items: Dict[] }) {
  if (!items.length) return <EmptyState title="Находок пока нет">Здесь появятся выводы, которые агент счёл достаточно важными для отчёта.</EmptyState>;
  return (
    <ol className="findings-list">
      {items.map((finding, index) => (
        <li key={index}>
          <time>{formatTime(finding.timestamp)}</time>
          <p>{text(finding.claim)}</p>
          {Array.isArray(finding.evidence_ids) && finding.evidence_ids.length > 0 && <small>{finding.evidence_ids.join(" · ")}</small>}
        </li>
      ))}
    </ol>
  );
}

function RoleMetrics({ items }: { items: Dict[] }) {
  const maxDuration = Math.max(1, ...items.map((metric) => number(metric.duration_ms)));
  if (!items.length) return <EmptyState title="Метрики появятся после run">Время, токены и количество вызовов по каждой роли.</EmptyState>;
  return (
    <div className="role-metrics">
      {items.map((metric, index) => (
        <div className="role-metric" key={`${text(metric.agent)}-${index}`}>
          <div><strong>{roleLabel(metric.agent)}</strong><span>{formatDuration(metric.duration_ms)}</span></div>
          <div className="role-bar"><span style={{ width: `${Math.max(3, number(metric.duration_ms) / maxDuration * 100)}%` }} /></div>
          <small>{formatNumber(metric.total_tokens)} токенов · {formatNumber(metric.tool_calls)} вызовов · {text(metric.work_item_id, "основной поток")}</small>
        </div>
      ))}
    </div>
  );
}

function Overview({ state, agent, onOpenEvidence }: { state: Dict; agent: AbstractAgent; onOpenEvidence: (id: string) => void }) {
  const [sideTab, setSideTab] = useState<SideTab>("plan");
  const [view, setView] = useState<"tree" | "timeline">("tree");
  const [showAll, setShowAll] = useState(false);
  const workItems = list(state.work_items);
  const hypotheses = list(state.hypotheses);
  const evidence = list(state.evidence);
  const findings = list(state.findings);
  const metrics = list(state.run_metrics);
  const timeline = timelineOf(workItems, evidence, hypotheses, metrics);

  return (
    <>
      <CurrentTask state={state} />
      <SteeringPanel agent={agent} state={state} />
      <div className="overview-grid">
        <Section
          title="Ход работы"
          subtitle={view === "tree" ? "Задача → этапы → вызовы и выводы" : "Все события в обратном хронологическом порядке"}
          className="timeline-panel"
          action={(
            <div className="view-actions">
              <div className="view-switch">
                <button type="button" className={view === "tree" ? "active" : ""} onClick={() => setView("tree")}>Дерево</button>
                <button type="button" className={view === "timeline" ? "active" : ""} onClick={() => setView("timeline")}>Лента</button>
              </div>
              {view === "timeline" && timeline.length > 9 && (
                <button type="button" className="button button-secondary button-small" onClick={() => setShowAll(!showAll)}>
                  {showAll ? "Последние события" : `Все · ${timeline.length}`}
                </button>
              )}
            </div>
          )}
        >
          {view === "tree" ? (
            <InvestigationTree state={state} onOpenEvidence={onOpenEvidence} />
          ) : timeline.length === 0 ? (
            <EmptyState title={record(state.progress).stage === "done" ? "Отдельных этапов не было" : "Работа ещё не началась"}>
              {record(state.progress).stage === "done"
                ? "Агент ответил без обращения к LogiCore и без дополнительных проверок."
                : "Отправьте задачу в чат. Здесь появятся этапы, запросы к LogiCore, гипотезы и находки."}
            </EmptyState>
          ) : (
            <div className="timeline">
              {timeline.slice(0, showAll ? timeline.length : 9).map((entry, index) => (
                <div className={`timeline-entry timeline-${entry.kind}`} key={`${entry.at}-${entry.title}-${index}`}>
                  <div className={`timeline-dot dot-${statusTone(entry.status)}`} />
                  <div className="timeline-body">
                    <div className="timeline-title"><strong>{entry.title}</strong><time>{formatTime(entry.at)}</time></div>
                    <p>{entry.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        <aside className="overview-side">
          <div className="side-tabs">
            <button type="button" className={sideTab === "plan" ? "active" : ""} onClick={() => setSideTab("plan")}>Этапы <span>{workItems.length}</span></button>
            <button type="button" className={sideTab === "hypotheses" ? "active" : ""} onClick={() => setSideTab("hypotheses")}>Гипотезы <span>{hypotheses.length}</span></button>
            <button type="button" className={sideTab === "findings" ? "active" : ""} onClick={() => setSideTab("findings")}>Находки <span>{findings.length}</span></button>
            <button type="button" className={sideTab === "metrics" ? "active" : ""} onClick={() => setSideTab("metrics")}>Метрики</button>
          </div>
          <Section
            title={sideTab === "plan" ? "Этапы расследования" : sideTab === "hypotheses" ? "Журнал гипотез" : sideTab === "findings" ? "Зафиксированные находки" : "Нагрузка по ролям"}
            subtitle={sideTab === "plan" ? "Что уже сделано и что осталось" : sideTab === "hypotheses" ? "Предположения и их проверка" : sideTab === "findings" ? "Выводы со ссылками на Evidence" : "Время, токены и вызовы"}
            className="side-content-panel"
          >
            {sideTab === "plan" && <WorkPlan items={workItems} />}
            {sideTab === "hypotheses" && <Hypotheses items={hypotheses} />}
            {sideTab === "findings" && <Findings items={findings} />}
            {sideTab === "metrics" && <RoleMetrics items={metrics} />}
          </Section>
        </aside>
      </div>
    </>
  );
}

function Welcome() {
  const example = "Проверь подозрительные события в журнале Earth за последний час. Если найдёшь зацепки — разберись и покажи доказательства.";
  return (
    <section className="welcome-screen">
      <div className="eyebrow">РАССЛЕДОВАНИЕ НАЧИНАЕТСЯ С ВОПРОСА</div>
      <h2>Что проверим в LogiCore?</h2>
      <p className="welcome-lead">Опишите событие, аномалию или алерт в чате. Агент покажет не только вывод, но и весь путь к нему.</p>
      <div className="welcome-steps">
        <div><span>01</span><strong>План</strong><p>Поймёт задачу и разобьёт её на проверяемые этапы.</p></div>
        <div><span>02</span><strong>Доказательства</strong><p>Покажет фактические запросы, временные окна и полученные события.</p></div>
        <div><span>03</span><strong>Отчёт</strong><p>Сформулирует выводы, ограничения и то, что осталось непроверенным.</p></div>
      </div>
      <div className="welcome-example">
        <span>Пример задачи</span>
        <p>«{example}»</p>
        <CopyButton value={example} label="Скопировать пример" />
      </div>
      <p className="welcome-note">Для короткого вопроса достаточно быстрой проверки. Для разбора причин и гипотез выбирайте глубокое расследование.</p>
    </section>
  );
}

export function Inspector() {
  const [tab, setTab] = useState<Tab>("overview");
  const [focusEvidenceId, setFocusEvidenceId] = useState("");
  const { agent, isReady } = useAgent({
    agentId: "default",
    updates: [UseAgentUpdate.OnStateChanged, UseAgentUpdate.OnRunStatusChanged],
    throttleMs: 500,
  });
  const state = record(agent.state);
  const progress = record(state.progress);
  const evidence = list(state.evidence);
  const hypotheses = list(state.hypotheses);
  const findings = list(state.findings);
  const metrics = list(state.run_metrics);
  const toolUsage = record(state.tool_usage);
  const report = record(state.report_result);
  const lastReport = record(state.last_report);
  const hasReport = Boolean(text(report.answer, "") || text(lastReport.markdown, ""));

  const stats = useMemo(() => ({
    ok: evidence.filter((item) => item.status === "ok").length,
    supported: hypotheses.filter((item) => item.status === "supported").length,
    tokens: metrics.reduce((sum, metric) => sum + number(metric.total_tokens), 0),
    duration: metrics.reduce((sum, metric) => sum + number(metric.duration_ms), 0),
    calls: Object.values(toolUsage).reduce<number>((sum, value) => sum + number(value), 0),
  }), [evidence, hypotheses, metrics, toolUsage]);

  const hasActivity = Boolean(
    Object.keys(record(state.intent)).length ||
    progress.stage ||
    evidence.length ||
    findings.length ||
    metrics.length ||
    hasReport,
  );

  if (!hasActivity) {
    return (
      <main className="investigation-desk">
        <div className="desk-topline">
          <div>
            <div className="eyebrow">LOGICORE · РАБОЧИЙ СТОЛ АНАЛИТИКА</div>
            <h1>Расследование</h1>
          </div>
          <div className="run-status">
            <Badge tone={isReady ? "ok" : "running"}>{isReady ? "Готов к работе" : "Подключение"}</Badge>
          </div>
        </div>
        <Welcome />
      </main>
    );
  }

  return (
    <main className="investigation-desk">
      <div className="desk-topline">
        <div>
          <div className="eyebrow">LOGICORE · РАБОЧИЙ СТОЛ АНАЛИТИКА</div>
          <h1>Расследование</h1>
        </div>
        <div className="run-status">
          <Badge tone={statusTone(progress.state)}>{isReady ? statusLabel(progress.state) : "Подключение"}</Badge>
          <span>{stageLabel(progress.stage)}</span>
        </div>
      </div>

      <div className="metrics-grid">
        <MetricCard label="Текущий этап" value={stageLabel(progress.stage)} detail={roleLabel(progress.agent)} current />
        <MetricCard label="Доказательства" value={`${stats.ok} / ${evidence.length}`} detail="успешно / всего" />
        <MetricCard label="Находки" value={findings.length} detail={`${stats.supported} подтверждённых гипотез`} />
        <MetricCard label="Вызовы инструментов" value={formatNumber(stats.calls)} detail={`${formatDuration(stats.duration)} по ролям`} />
        <MetricCard label="Токены LLM" value={formatNumber(stats.tokens)} detail="фактический расход" />
      </div>

      <nav className="desk-tabs" aria-label="Разделы расследования">
        <button type="button" className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>Обзор</button>
        <button type="button" className={tab === "evidence" ? "active" : ""} onClick={() => setTab("evidence")}>Запросы и доказательства <span>{evidence.length}</span></button>
        <button type="button" className={tab === "report" ? "active" : ""} onClick={() => setTab("report")}>Итоговый отчёт {hasReport && <i />}</button>
      </nav>

      {tab === "overview" && <Overview state={state} agent={agent} onOpenEvidence={(id) => {
        setFocusEvidenceId(id);
        setTab("evidence");
      }} />}
      {tab === "evidence" && <EvidenceExplorer evidence={evidence} focusId={focusEvidenceId} />}
      {tab === "report" && <ReportView state={state} />}
    </main>
  );
}
