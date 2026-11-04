"use client";

import { useState } from "react";
import { statusLabel, statusTone } from "./format";

export function Badge({ children, tone }: { children: React.ReactNode; tone?: "ok" | "running" | "error" | "neutral" }) {
  return <span className={`badge badge-${tone ?? "neutral"}`}>{children}</span>;
}

export function StatusBadge({ status }: { status: unknown }) {
  const tone = statusTone(status);
  if (tone === "neutral") return <span className="status-text">{statusLabel(status)}</span>;
  return <Badge tone={tone}>{statusLabel(status)}</Badge>;
}

export function Section({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function CopyButton({ value, label = "Копировать" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  return (
    <button
      type="button"
      className="button button-secondary button-small"
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      }}
    >
      {copied ? "Скопировано" : label}
    </button>
  );
}

export function KeyValue({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="key-value">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}
