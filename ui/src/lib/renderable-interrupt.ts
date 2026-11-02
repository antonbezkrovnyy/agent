export function isRenderableInterrupt(interrupt: unknown): boolean {
  const items = Array.isArray(interrupt) ? interrupt : [interrupt];
  return items.some((item) => {
    if (!item) return false;
    if (typeof item !== "object") return true;
    const candidate = item as { when?: string; value?: unknown };
    return candidate.when !== "breakpoint" || "value" in candidate;
  });
}
