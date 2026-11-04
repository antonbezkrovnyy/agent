"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Dict, formatBytes, list, record, text } from "./format";
import { Badge, CopyButton, EmptyState, Section, StatusBadge } from "./ui";

function artifactUrl(item: Dict): string {
  const direct = text(item.url ?? item.data_url, "");
  if (direct) return direct;
  const key = text(item.key, "");
  if (item.storage === "s3" && key) {
    const base = process.env.NEXT_PUBLIC_AGUI_URL ?? "http://localhost:2027";
    return `${base}/artifacts/${key.split("/").map(encodeURIComponent).join("/")}`;
  }
  return "";
}

function ArtifactDownloads({ state }: { state: Dict }) {
  const artifacts = list(state.artifacts);
  const messageLinks = list(state.messages).flatMap((message) => {
    const links = message.artifacts;
    return Array.isArray(links) ? links.filter((link): link is string => typeof link === "string") : [];
  });
  if (!artifacts.length && !messageLinks.length) return null;
  return (
    <div className="artifact-downloads">
      {artifacts.map((artifact, index) => {
        const url = artifactUrl(artifact);
        const filename = text(artifact.filename, `artifact-${index + 1}`);
        return (
          <a className="artifact-card" href={url || undefined} target="_blank" rel="noreferrer" key={text(artifact.key, filename)}>
            <div><strong>{filename}</strong><span>{text(artifact.content_type, "файл")} · {formatBytes(artifact.bytes)}</span></div>
            <span>{url ? "Скачать" : "Ссылка недоступна"}</span>
          </a>
        );
      })}
      {messageLinks.map((url, index) => {
        const filename = decodeURIComponent(url.split("#")[1] || `artifact-${index + 1}`);
        return (
          <a className="artifact-card" href={url} target="_blank" rel="noreferrer" key={url}>
            <div><strong>{filename}</strong><span>Файл из финального ответа</span></div>
            <span>Скачать</span>
          </a>
        );
      })}
    </div>
  );
}

function ReportSupplements({ report }: { report: Dict }) {
  const claims = list(report.claims);
  const limitations = Array.isArray(report.limitations) ? report.limitations : [];
  const notChecked = Array.isArray(report.not_checked) ? report.not_checked : [];
  if (!claims.length && !limitations.length && !notChecked.length) return null;
  return (
    <div className="report-supplements">
      {!!claims.length && (
        <section>
          <h3>Утверждения и доказательная база</h3>
          <div className="claims-table">
            {claims.map((claim, index) => (
              <div className="claim-row" key={index}>
                <p>{text(claim.text)}</p>
                <div>{Array.isArray(claim.evidence_ids) && claim.evidence_ids.length
                  ? claim.evidence_ids.map((id) => <span key={String(id)}>{String(id)}</span>)
                  : <small>Evidence ID не указаны</small>}</div>
              </div>
            ))}
          </div>
        </section>
      )}
      {!!limitations.length && (
        <section className="report-limitations">
          <h3>Ограничения</h3>
          <ul>{limitations.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
        </section>
      )}
      {!!notChecked.length && (
        <section className="report-not-checked">
          <h3>Не проверено</h3>
          <ul>{notChecked.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
        </section>
      )}
    </div>
  );
}

export function ReportView({ state }: { state: Dict }) {
  const report = record(state.report_result);
  const lastReport = record(state.last_report);
  const critic = record(state.critic);
  const markdown = text(lastReport.markdown ?? report.answer, "");
  const title = text(lastReport.title, "Итоговый отчёт");
  const filename = text(lastReport.filename, "investigation-report.md");

  if (!markdown) {
    return (
      <Section title="Итоговый отчёт" subtitle="Выводы, доказательная база и ограничения расследования">
        <EmptyState title="Отчёт ещё не сформирован">Когда reporter завершит работу, здесь появится читаемый отчёт. Сохранённые Markdown- и ZIP-файлы будут доступны рядом.</EmptyState>
      </Section>
    );
  }

  const downloadMarkdown = () => {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="report-view">
      <div className="report-header">
        <div>
          <div className="eyebrow">FINAL REPORT</div>
          <h1>{title}</h1>
          <p>Отчёт построен только на фактических Evidence; ограничения и непроверенные направления не скрыты.</p>
        </div>
        <div className="report-actions">
          {!!critic.verdict && <StatusBadge status={critic.verdict} />}
          <CopyButton value={markdown} label="Копировать Markdown" />
          <button type="button" className="button button-primary" onClick={downloadMarkdown}>Скачать .md</button>
        </div>
      </div>

      <ArtifactDownloads state={state} />

      <article className="report-paper">
        <div className="report-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
        </div>
      </article>

      <ReportSupplements report={report} />
    </div>
  );
}
