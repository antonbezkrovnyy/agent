export async function api<T>(path: string): Promise<T> {
  const response = await fetch(path)
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.detail || `Ошибка запроса: ${response.status}`)
  }
  return payload as T
}

export function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) search.set(key, String(value))
  })
  return search.toString()
}
