export type Dict = Record<string, unknown>;

export function record(value: unknown): Dict {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Dict) : {};
}

export function unwrap(value: unknown): unknown {
  const wrapped = record(value);
  return wrapped.type === "__overwrite__" ? wrapped.value : value;
}

export function list(value: unknown): Dict[] {
  const unwrapped = unwrap(value);
  return Array.isArray(unwrapped)
    ? unwrapped.filter((item): item is Dict => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

export function text(value: unknown, fallback = "—"): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" && value.trim() ? value : fallback;
}

export function number(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatNumber(value: unknown): string {
  const n = number(value);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return new Intl.NumberFormat("ru-RU").format(n);
}

export function formatDuration(value: unknown): string {
  const ms = number(value);
  if (!ms) return "—";
  if (ms < 1000) return `${Math.round(ms)} мс`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} с`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} мин ${seconds % 60} с`;
}

export function formatTime(value: unknown): string {
  const raw = text(value, "");
  if (!raw) return "—";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(value: unknown): string {
  const raw = text(value, "");
  if (!raw) return "—";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatBytes(value: unknown): string {
  const bytes = number(value);
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

export function windowLabel(value: unknown): string {
  const window = record(value);
  const from = text(window.from ?? window.time_from, "");
  const to = text(window.to ?? window.time_to, "");
  if (from || to) return `${from || "…"} → ${to || "…"}`;
  const minutes = number(window.last_minutes);
  return minutes ? `последние ${minutes} мин` : "окно не указано";
}

export function statusTone(value: unknown): "ok" | "running" | "error" | "neutral" {
  const status = text(value, "").toLowerCase();
  if (["ok", "done", "supported", "complete", "success"].includes(status)) return "ok";
  if (["error", "failed", "rejected", "rework_data", "rework_report"].includes(status)) return "error";
  if (["running", "pending", "critic_review", "partial", "limited", "waiting"].includes(status)) return "running";
  return "neutral";
}

export function statusLabel(value: unknown): string {
  const labels: Record<string, string> = {
    ok: "Успешно",
    done: "Готово",
    running: "В работе",
    pending: "Ожидает",
    partial: "Частично",
    error: "Ошибка",
    failed: "Ошибка",
    open: "Проверяется",
    supported: "Подтверждена",
    rejected: "Отклонена",
    critic_review: "Проверка критиком",
    critic_unavailable: "Критик недоступен",
    rework_data: "Нужны данные",
    rework_report: "Нужна доработка",
    skipped: "Пропущено",
    limited: "Лимит вызовов",
    waiting: "Ожидание",
    idle: "Ожидание",
  };
  return labels[text(value, "")] ?? text(value);
}

export function intentLabel(value: unknown): string {
  const labels: Record<string, string> = {
    simple: "Обычный вопрос",
    complex: "Новое расследование",
    continuation: "Продолжение",
    export: "Выгрузка данных",
  };
  return labels[text(value, "")] ?? text(value);
}

export function modeLabel(value: unknown): string {
  const labels: Record<string, string> = {
    quick: "Быстрая проверка",
    deep: "Глубокое расследование",
    discovery: "Обзор · до 2 направлений",
    targeted: "Точечная проверка",
    alert: "Разбор алерта",
  };
  return labels[text(value, "")] ?? text(value);
}

export function stageLabel(value: unknown): string {
  const labels: Record<string, string> = {
    entry: "Подготовка",
    intent: "Определяю задачу",
    memory_context: "Восстанавливаю контекст",
    memory_retrieve: "Ищу похожие дела",
    scope: "Определяю границы проверки",
    planner: "Строю план расследования",
    execute: "Проверяю данные",
    scout: "Быстрая проверка",
    investigator: "Анализирую данные",
    critic_evidence: "Проверяю доказательства",
    reporter: "Формирую отчёт",
    critic_report: "Проверяю отчёт",
    memory_save: "Сохраняю результат",
    export: "Выгружаю данные",
    done: "Готово",
  };
  return labels[text(value, "")] ?? text(value);
}

export function roleLabel(value: unknown): string {
  const labels: Record<string, string> = {
    scout: "Быстрая проверка",
    investigator: "Анализ данных",
    reporter: "Формирование отчёта",
    planner: "Планирование",
    intent: "Определение задачи",
  };
  return labels[text(value, "")] ?? text(value);
}

export function resultRows(result: unknown): Dict[] {
  const source = record(result);
  const candidates = [source.events, source.rows, source.items, source.results, source.data, source.events_csv];
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.some((item) => item && typeof item === "object")) {
      return candidate.filter((item): item is Dict => Boolean(item && typeof item === "object" && !Array.isArray(item)));
    }
  }
  return [];
}

export function resultSummary(result: unknown): string {
  const source = record(result);
  const rows = resultRows(result);
  if (rows.length) return `${rows.length} записей`;
  if (source.count !== undefined) return `count = ${text(source.count)}`;
  if (source.returned_rows !== undefined) return `${text(source.returned_rows)} строк`;
  if (source.ok === false || source.error) return "ошибка вызова";
  const keys = Object.keys(source).filter((key) => !["ok", "window"].includes(key));
  return keys.length ? `${keys.length} полей результата` : "результат получен";
}
